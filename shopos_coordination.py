"""Explicit cross-process writer/backup coordination for ShopOS state."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping

from filelock import FileLock, Timeout as FileLockTimeout


COORDINATED_BACKUPS_ENV = "SHOP_ENABLE_COORDINATED_BACKUPS"
COORDINATION_LOCK_PATH_ENV = "SHOP_COORDINATION_LOCK_PATH"
DEFAULT_COORDINATION_TIMEOUT_SECONDS = 10.0
TEST_PROCESS_ENV = "JOB_TRAVELER_TEST_PROCESS"
TEST_ROOTS_ENV = "JOB_TRAVELER_TEST_WRITE_ROOTS"


class CoordinationError(RuntimeError):
    """Base class for fail-closed coordination failures."""


class CoordinationConfigurationError(CoordinationError, ValueError):
    """Raised when the shared lock identity is not explicit and safe."""


class CoordinationTimeoutError(CoordinationError, TimeoutError):
    """Raised when the bounded shared barrier wait expires."""


def _boolean(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise CoordinationConfigurationError(f"{name} is invalid.")


def resolve_coordination_lock_path(value: str | os.PathLike[str]) -> Path:
    """Resolve one absolute lock file without following symlinked components."""
    try:
        raw = Path(value)
    except (TypeError, ValueError) as error:
        raise CoordinationConfigurationError(
            "The coordination lock path is invalid."
        ) from error
    if not raw.is_absolute() or any(part in {"", ".", ".."} for part in raw.parts[1:]):
        raise CoordinationConfigurationError(
            "The coordination lock path must be absolute and normalized."
        )
    if raw == Path(raw.anchor) or raw.parent == Path(raw.anchor):
        raise CoordinationConfigurationError(
            "The coordination lock cannot be a filesystem root-level path."
        )
    if raw.parent == Path.home().resolve(strict=False):
        raise CoordinationConfigurationError(
            "The coordination lock cannot use the profile root directly."
        )
    current = Path(raw.anchor)
    try:
        for part in raw.parts[1:-1]:
            current = current / part
            if current.is_symlink():
                raise CoordinationConfigurationError(
                    "The coordination lock path cannot contain symlinks."
                )
        parent = raw.parent.resolve(strict=True)
    except CoordinationConfigurationError:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise CoordinationConfigurationError(
            "The coordination lock parent must already exist."
        ) from error
    if not parent.is_dir() or parent == Path(parent.anchor):
        raise CoordinationConfigurationError(
            "The coordination lock parent is unsafe."
        )
    candidate = parent / raw.name
    if candidate.is_symlink() or (candidate.exists() and not candidate.is_file()):
        raise CoordinationConfigurationError(
            "The coordination lock target is unsafe."
        )
    return candidate


def canonical_coordination_identity(value: str | os.PathLike[str]) -> str:
    resolved = resolve_coordination_lock_path(value)
    return os.path.normcase(os.path.normpath(str(resolved)))


def _assert_test_lock_path(path: Path) -> None:
    """Keep unit tests away from an operator's real coordination location."""
    if not os.environ.get(TEST_PROCESS_ENV):
        return
    configured = os.environ.get(TEST_ROOTS_ENV, "")
    roots = []
    for value in configured.split(os.pathsep):
        if not value.strip():
            continue
        try:
            root = Path(value).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as error:
            raise CoordinationConfigurationError(
                "The test coordination root is unavailable."
            ) from error
        if root.is_dir() and root != Path(root.anchor):
            roots.append(root)
    candidate = path.resolve(strict=False)
    if not roots or not any(
        candidate == root or candidate.is_relative_to(root) for root in roots
    ):
        raise CoordinationConfigurationError(
            "Tests cannot use a coordination lock outside temporary roots."
        )


_LOCK_CACHE: dict[str, FileLock] = {}
_LOCK_CACHE_GUARD = threading.Lock()


def _shared_file_lock(path: Path) -> FileLock:
    identity = canonical_coordination_identity(path)
    with _LOCK_CACHE_GUARD:
        lock = _LOCK_CACHE.get(identity)
        if lock is None:
            lock = FileLock(str(path), mode=0o600, thread_local=True)
            _LOCK_CACHE[identity] = lock
        return lock


@dataclass(frozen=True)
class CoordinationSettings:
    enabled: bool = False
    lock_path: Path | None = None
    timeout_seconds: float = DEFAULT_COORDINATION_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        enabled = _boolean(self.enabled, COORDINATED_BACKUPS_ENV)
        object.__setattr__(self, "enabled", enabled)
        try:
            timeout = float(self.timeout_seconds)
        except (TypeError, ValueError) as error:
            raise CoordinationConfigurationError(
                "The coordination timeout is invalid."
            ) from error
        if timeout < 0 or timeout > 300:
            raise CoordinationConfigurationError(
                "The coordination timeout must be between 0 and 300 seconds."
            )
        object.__setattr__(self, "timeout_seconds", timeout)
        if enabled:
            if self.lock_path is None:
                raise CoordinationConfigurationError(
                    "Coordinated writers require an explicit lock path."
                )
            object.__setattr__(
                self, "lock_path", resolve_coordination_lock_path(self.lock_path)
            )
        elif self.lock_path is not None:
            object.__setattr__(
                self, "lock_path", resolve_coordination_lock_path(self.lock_path)
            )

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        timeout_seconds: float = DEFAULT_COORDINATION_TIMEOUT_SECONDS,
    ) -> "CoordinationSettings":
        environment = os.environ if environ is None else environ
        enabled = _boolean(
            environment.get(COORDINATED_BACKUPS_ENV, False),
            COORDINATED_BACKUPS_ENV,
        )
        path = environment.get(COORDINATION_LOCK_PATH_ENV)
        return cls(enabled, Path(path) if path else None, timeout_seconds)

    @contextmanager
    def writer(self) -> Iterator[None]:
        """Take the shared barrier before the canonical Job Traveler lock."""
        if not self.enabled:
            with nullcontext():
                yield
            return
        assert self.lock_path is not None
        _assert_test_lock_path(self.lock_path)
        lock = _shared_file_lock(self.lock_path)
        try:
            lock.acquire(timeout=self.timeout_seconds)
        except FileLockTimeout as error:
            raise CoordinationTimeoutError(
                "Timed out waiting for the ShopOS coordination barrier."
            ) from error
        except OSError as error:
            raise CoordinationError(
                "The ShopOS coordination barrier is unavailable."
            ) from error
        try:
            try:
                os.chmod(self.lock_path, 0o600)
            except OSError:
                if os.name != "nt":
                    raise CoordinationError(
                        "The ShopOS coordination lock could not be secured."
                    )
            yield
        finally:
            lock.release()


__all__ = [
    "COORDINATED_BACKUPS_ENV",
    "COORDINATION_LOCK_PATH_ENV",
    "CoordinationConfigurationError",
    "CoordinationError",
    "CoordinationSettings",
    "CoordinationTimeoutError",
    "canonical_coordination_identity",
    "resolve_coordination_lock_path",
]
