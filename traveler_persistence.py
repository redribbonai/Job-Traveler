"""Revision-aware persistence boundary for the Job Traveler desktops.

Local compatibility mode is the only implementation in this repository that
writes traveler JSON.  It deliberately shares the backend's canonical lock
identity and persistent ``filelock`` protocol so a desktop and ShopOS server
serialize changes to the same job-scoped file.
"""

from __future__ import annotations

import copy
import errno
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from filelock import FileLock, Timeout as FileLockTimeout

import traveler_domain as domain


MODE_ENV = "JOB_TRAVELER_PERSISTENCE_MODE"
TEST_PROCESS_ENV = "JOB_TRAVELER_TEST_PROCESS"
TEST_ROOTS_ENV = "JOB_TRAVELER_TEST_WRITE_ROOTS"
LOCK_DIRECTORY_NAME = ".shopos-locks"
READ_VERSION = re.compile(r"^sha256:[0-9a-f]{64}$")
INVALID_WINDOWS_FILENAME = re.compile(r'[<>:"/\\|?*]')
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class PersistenceError(RuntimeError):
    """Base class for safe desktop persistence failures."""


class PersistenceConfigurationError(PersistenceError, ValueError):
    """Raised when persistence mode selection is invalid or incomplete."""


class PersistenceValidationError(PersistenceError, ValueError):
    """Raised when a traveler cannot be validated without writing it."""


class PersistenceNotFoundError(PersistenceError, LookupError):
    """Raised when a requested traveler does not exist."""


class PersistenceAlreadyExistsError(PersistenceError):
    """Raised when creation would overwrite an existing traveler."""


class PersistenceStorageError(PersistenceError):
    """Raised when local storage cannot confirm a safe result."""


class PersistenceLockTimeoutError(PersistenceStorageError, TimeoutError):
    """Raised when the canonical job lock cannot be acquired in time."""


class UnsupportedPersistenceAction(PersistenceError):
    """Raised when the selected implementation cannot perform an action safely."""


class PlanResizeConfirmationRequired(PersistenceError):
    """A shrink needs an explicit confirmation of the latest stable identities."""

    def __init__(
        self,
        *,
        requested_count: int,
        document_revision: int,
        read_version: str,
        removed_operations: list[dict[str, Any]],
    ) -> None:
        self.requested_count = requested_count
        self.document_revision = document_revision
        self.read_version = read_version
        self.removed_operations = tuple(copy.deepcopy(removed_operations))
        super().__init__("Confirm the exact operations that would be removed.")

    @property
    def operation_ids(self) -> list[str]:
        return [item["operation_id"] for item in self.removed_operations]


class PlanResizeConflict(PersistenceError):
    """The plan changed before a resize or shrink confirmation."""

    def __init__(self, latest_snapshot: "TravelerSnapshot") -> None:
        self.latest_snapshot = latest_snapshot
        super().__init__("The operation plan changed. Review the latest traveler.")


@dataclass(frozen=True)
class TravelerSnapshot:
    """One desktop document paired with its authoritative strong read version."""

    job_number: str
    traveler: dict[str, Any]
    read_version: str
    document_revision: int
    location: Path | None = None
    etag: str | None = None

    def __post_init__(self) -> None:
        if READ_VERSION.fullmatch(self.read_version) is None:
            raise ValueError("A traveler snapshot requires a strong SHA-256 read version.")


@dataclass(frozen=True)
class TravelerSummary:
    job_number: str
    customer: Any
    part_number: Any
    quantity: Any
    status: str
    snapshot: TravelerSnapshot | None = None


@dataclass(frozen=True)
class SaveResult:
    snapshot: TravelerSnapshot
    changed: bool


@dataclass(frozen=True)
class ConflictField:
    """Field-specific three-way conflict context safe for desktop display."""

    path: tuple[str | int, ...]
    base_value: Any
    intended_value: Any
    authoritative_value: Any
    authoritative_hash: str
    target: dict[str, Any] | None = None

    @property
    def label(self) -> str:
        parts = []
        for component in self.path:
            if isinstance(component, int):
                parts.append(f"[{component + 1}]")
            elif parts:
                parts.append(f".{component}")
            else:
                parts.append(component)
        return "".join(parts) or "traveler"


class PersistenceConflict(PersistenceError):
    """A stale save that made no write and includes the latest snapshot."""

    def __init__(
        self,
        conflicts: Iterable[ConflictField],
        latest_snapshot: TravelerSnapshot,
        base_snapshot: TravelerSnapshot | None = None,
    ) -> None:
        self.conflicts = tuple(conflicts)
        self.latest_snapshot = latest_snapshot
        self.base_snapshot = base_snapshot
        super().__init__(
            "The traveler changed after it was opened. Review the conflicting field."
        )


@dataclass(frozen=True)
class LocalWriteHooks:
    """Temporary-test fault points; production callers use the empty default."""

    before_stage: Callable[[Path], None] | None = None
    after_stage_fsync: Callable[[Path], None] | None = None
    before_replace: Callable[[Path], None] | None = None
    after_replace: Callable[[Path], None] | None = None


class TravelerPersistence(ABC):
    """The one persistence contract used by terminal and Tkinter entry points."""

    mode: str

    @abstractmethod
    def load(self, job_number: object) -> TravelerSnapshot:
        raise NotImplementedError

    @abstractmethod
    def list_summaries(self) -> list[TravelerSummary]:
        raise NotImplementedError

    def list_summaries_with_errors(
        self,
    ) -> tuple[list[TravelerSummary], list[tuple[Path, str]]]:
        return self.list_summaries(), []

    @abstractmethod
    def create(self, traveler: dict[str, Any], *, overwrite: bool = False) -> SaveResult:
        raise NotImplementedError

    @abstractmethod
    def save(
        self,
        base: TravelerSnapshot,
        intended: dict[str, Any],
        *,
        action: str = "logical_save",
    ) -> SaveResult:
        raise NotImplementedError

    def resolve_conflict(
        self,
        conflict: PersistenceConflict,
        intended: dict[str, Any],
        *,
        action: str = "logical_save",
    ) -> SaveResult:
        """Retry one explicit user decision against the returned latest version."""
        rebased = rebase_conflict_intent(conflict, intended)
        return self.save(conflict.latest_snapshot, rebased, action=action)

    @property
    def job_planner_authorized(self) -> bool:
        return self.mode == "local"

    def resize_plan(
        self,
        base: TravelerSnapshot,
        operation_count: int,
        *,
        confirm_removed_operation_ids: list[str] | None = None,
    ) -> SaveResult:
        del base, operation_count, confirm_removed_operation_ids
        raise UnsupportedPersistenceAction(
            "This persistence mode has no dedicated plan-resize command."
        )


def _safe_job_number(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise PersistenceValidationError("The Job Traveler job number is invalid.")
    job_number = str(value).strip()
    if (
        not job_number
        or len(job_number) > 64
        or job_number in {".", ".."}
        or INVALID_WINDOWS_FILENAME.search(job_number)
        or any(ord(character) < 32 for character in job_number)
        or job_number.endswith((" ", "."))
        or job_number.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES
    ):
        raise PersistenceValidationError("The Job Traveler job number is invalid.")
    return job_number


def _resolve_root(root: str | os.PathLike[str], *, must_exist: bool) -> Path:
    try:
        candidate = Path(root).expanduser()
        if candidate.is_symlink():
            raise PersistenceStorageError("The jobs directory cannot be a symlink.")
        resolved = candidate.resolve(strict=must_exist)
    except PersistenceError:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise PersistenceStorageError("The jobs directory is unavailable.") from error
    if resolved == Path(resolved.anchor):
        raise PersistenceStorageError("A filesystem root cannot be the jobs directory.")
    if must_exist and not resolved.is_dir():
        raise PersistenceStorageError("The jobs directory is unavailable.")
    return resolved


def _resolve_direct_target(root: Path, target: str | os.PathLike[str]) -> Path:
    try:
        requested = Path(target).expanduser()
        candidate = requested if requested.is_absolute() else root / requested
        lexical = Path(os.path.abspath(os.fspath(candidate)))
        if lexical.parent != root:
            raise PersistenceStorageError("The traveler path is outside the jobs directory.")
        if lexical.is_symlink():
            raise PersistenceStorageError("A traveler file cannot be a symlink.")
        resolved = lexical.resolve(strict=False)
    except PersistenceError:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise PersistenceStorageError("The traveler path is unsafe.") from error
    if resolved.parent != root or resolved.suffix.casefold() != ".json":
        raise PersistenceStorageError("The traveler path is unsafe.")
    return resolved


def canonical_job_lock_identity(
    jobs_root: str | os.PathLike[str],
    traveler_path: str | os.PathLike[str],
    *,
    windows: bool | None = None,
) -> str:
    """Return the exact path identity used by the backend's JobTravelerStore."""
    root = _resolve_root(jobs_root, must_exist=True)
    target = _resolve_direct_target(root, traveler_path)
    windows = os.name == "nt" if windows is None else windows
    normalized = os.path.normcase(os.path.normpath(str(target)))
    if windows:
        normalized = normalized.casefold()
    return normalized


def canonical_job_lock_path(
    jobs_root: str | os.PathLike[str], traveler_path: str | os.PathLike[str]
) -> Path:
    root = _resolve_root(jobs_root, must_exist=True)
    identity = canonical_job_lock_identity(root, traveler_path)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return root / LOCK_DIRECTORY_NAME / f"{digest}.lock"


def _approved_test_roots() -> tuple[Path, ...]:
    raw = os.environ.get(TEST_ROOTS_ENV, "")
    roots = []
    for value in raw.split(os.pathsep):
        if not value.strip():
            continue
        try:
            roots.append(Path(value).resolve(strict=True))
        except (OSError, RuntimeError, ValueError):
            continue
    return tuple(roots)


def _assert_test_write_path(path: Path, *, purpose: str) -> None:
    if not os.environ.get(TEST_PROCESS_ENV):
        return
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as error:
        raise PersistenceStorageError(f"The test {purpose} path is invalid.") from error
    for root in _approved_test_roots():
        try:
            resolved.relative_to(root)
            return
        except ValueError:
            continue
    raise PersistenceStorageError(
        f"The test {purpose} is outside approved temporary roots."
    )


class _DestinationLock:
    def __init__(self, root: Path, target: Path, timeout: float | None) -> None:
        self.root = root
        self.target = target
        self.timeout = timeout
        self.path = canonical_job_lock_path(root, target)
        self._lock: FileLock | None = None

    def __enter__(self) -> "_DestinationLock":
        _assert_test_write_path(self.path, purpose="job lock")
        try:
            self.path.parent.mkdir(mode=0o700, exist_ok=True)
            if self.path.parent.is_symlink() or self.path.is_symlink():
                raise PersistenceStorageError("The persistent job lock is unsafe.")
            lock = FileLock(str(self.path))
            lock.acquire(timeout=-1 if self.timeout is None else self.timeout)
        except FileLockTimeout as error:
            raise PersistenceLockTimeoutError(
                "The Job Traveler is busy. Try saving again."
            ) from error
        except PersistenceError:
            raise
        except OSError as error:
            raise PersistenceStorageError("The job lock is unavailable.") from error
        self._lock = lock
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        lock = self._lock
        self._lock = None
        if lock is not None:
            lock.release()


def _reject_constant(_value: str) -> None:
    raise PersistenceValidationError("The traveler contains non-finite JSON.")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise PersistenceValidationError("The traveler contains non-finite JSON.")
    return number


def _without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PersistenceValidationError("The traveler contains a duplicate JSON key.")
        result[key] = value
    return result


def _loads_strict(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            parse_constant=_reject_constant,
            parse_float=_finite_float,
            object_pairs_hook=_without_duplicates,
        )
    except PersistenceValidationError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise PersistenceValidationError("The Job Traveler JSON is invalid.") from error
    if not isinstance(value, dict):
        raise PersistenceValidationError("The Job Traveler must contain a JSON object.")
    try:
        domain.validate_traveler_structure(value)
    except (TypeError, ValueError, RecursionError) as error:
        raise PersistenceValidationError("The Job Traveler is invalid.") from error
    return value


def _dumps_strict(value: dict[str, Any]) -> bytes:
    try:
        domain.validate_traveler_structure(value)
        encoded = json.dumps(
            value,
            indent=4,
            ensure_ascii=True,
            allow_nan=False,
        ) + "\n"
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise PersistenceValidationError("The Job Traveler is invalid.") from error
    return encoded.encode("utf-8")


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(directory, flags)
    except OSError as error:
        if os.name == "nt" or error.errno in {
            errno.EACCES,
            errno.EINVAL,
            errno.ENOTSUP,
            errno.EOPNOTSUPP,
        }:
            return
        raise
    try:
        os.fsync(descriptor)
    except OSError as error:
        if os.name != "nt" and error.errno not in {
            errno.EACCES,
            errno.EINVAL,
            errno.ENOTSUP,
            errno.EOPNOTSUPP,
        }:
            raise
    finally:
        os.close(descriptor)


def _stage_prefix(target: Path) -> str:
    return f".{target.name}.shopos-stage-"


def _owned_stages(target: Path) -> list[Path]:
    prefix = _stage_prefix(target)
    try:
        return sorted(
            entry
            for entry in target.parent.iterdir()
            if entry.name.startswith(prefix) and entry.name.endswith(".tmp")
        )
    except OSError as error:
        raise PersistenceStorageError("The traveler directory is unavailable.") from error


def _validate_stage(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise PersistenceStorageError("A staged traveler is unsafe.")
    try:
        _loads_strict(path.read_bytes())
    except OSError as error:
        raise PersistenceStorageError("A staged traveler is unavailable.") from error


def _recover_locked(target: Path) -> None:
    stages = _owned_stages(target)
    if not stages:
        return
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file():
            raise PersistenceStorageError("The existing traveler is unsafe.")
        try:
            _loads_strict(target.read_bytes())
        except OSError as error:
            raise PersistenceStorageError("The existing traveler is unavailable.") from error
        for stage in stages:
            if stage.is_symlink() or not stage.is_file():
                raise PersistenceStorageError("A staged traveler is unsafe.")
        for stage in stages:
            stage.unlink()
        _fsync_directory(target.parent)
        return
    if len(stages) != 1:
        raise PersistenceStorageError("Traveler recovery requires operator attention.")
    stage = stages[0]
    _validate_stage(stage)
    try:
        os.replace(stage, target)
        _fsync_directory(target.parent)
    except OSError as error:
        raise PersistenceStorageError("Traveler recovery could not be completed.") from error


def _snapshot(path: Path, expected_job_number: str | None = None) -> TravelerSnapshot:
    if path.is_symlink() or not path.is_file():
        raise PersistenceStorageError("The traveler file is unsafe.")
    try:
        source = path.read_bytes()
    except OSError as error:
        raise PersistenceStorageError("The traveler could not be read.") from error
    persisted = _loads_strict(source)
    try:
        job_number = domain.parts_count_projection(persisted)["job_number"]
        traveler = domain.canonical_job(persisted)
        revision = domain.document_revision(persisted)
    except (TypeError, ValueError, RecursionError) as error:
        raise PersistenceValidationError("The Job Traveler is invalid.") from error
    if expected_job_number is not None and job_number.casefold() != expected_job_number.casefold():
        raise PersistenceStorageError("The traveler identity changed unexpectedly.")
    return TravelerSnapshot(
        job_number=job_number,
        traveler=traveler,
        read_version="sha256:" + hashlib.sha256(source).hexdigest(),
        document_revision=revision,
        location=path,
    )


_MISSING = object()


def _conflict(
    path: tuple[str | int, ...], base: Any, intended: Any, authoritative: Any
) -> ConflictField:
    shown_authoritative = None if authoritative is _MISSING else copy.deepcopy(authoritative)
    return ConflictField(
        path=path,
        base_value=None if base is _MISSING else copy.deepcopy(base),
        intended_value=None if intended is _MISSING else copy.deepcopy(intended),
        authoritative_value=shown_authoritative,
        authoritative_hash=domain.deterministic_value_hash(shown_authoritative),
    )


def _merge_list(
    base: list[Any],
    intended: list[Any],
    authoritative: list[Any],
    path: tuple[str | int, ...],
    conflicts: list[ConflictField],
) -> list[Any]:
    if intended == base:
        return copy.deepcopy(authoritative)
    if authoritative == base or authoritative == intended:
        return copy.deepcopy(intended)

    if len(intended) == len(base) == len(authoritative):
        return [
            _three_way_value(
                base[index], intended[index], authoritative[index], path + (index,), conflicts
            )
            for index in range(len(base))
        ]

    # Operation-plan expansion and shrink are deliberately append/tail-only in
    # this phase.  Retained rows still merge recursively by their persisted
    # operation identity/order while concurrent structural changes conflict.
    if len(intended) > len(base):
        if len(authoritative) != len(base):
            conflicts.append(_conflict(path, base, intended, authoritative))
            return copy.deepcopy(authoritative)
        retained = [
            _three_way_value(
                base[index], intended[index], authoritative[index], path + (index,), conflicts
            )
            for index in range(len(base))
        ]
        return retained + copy.deepcopy(intended[len(base) :])

    if len(intended) < len(base):
        if len(authoritative) != len(base):
            conflicts.append(_conflict(path, base, intended, authoritative))
            return copy.deepcopy(authoritative)
        for index in range(len(intended), len(base)):
            if authoritative[index] != base[index]:
                conflicts.append(
                    _conflict(path + (index,), base[index], _MISSING, authoritative[index])
                )
        return [
            _three_way_value(
                base[index], intended[index], authoritative[index], path + (index,), conflicts
            )
            for index in range(len(intended))
        ]

    conflicts.append(_conflict(path, base, intended, authoritative))
    return copy.deepcopy(authoritative)


def _three_way_value(
    base: Any,
    intended: Any,
    authoritative: Any,
    path: tuple[str | int, ...],
    conflicts: list[ConflictField],
) -> Any:
    if intended == base:
        return copy.deepcopy(authoritative)
    if isinstance(base, dict) and isinstance(intended, dict) and isinstance(authoritative, dict):
        merged = copy.deepcopy(authoritative)
        # Omitted keys are compatible extensions or untouched fields and remain
        # authoritative.  Desktop forms express an intentional clear as an empty
        # value, never by silently dropping a key.
        for key, intended_value in intended.items():
            base_value = base.get(key, _MISSING)
            authoritative_value = authoritative.get(key, _MISSING)
            if base_value is _MISSING:
                if authoritative_value is _MISSING or authoritative_value == intended_value:
                    merged[key] = copy.deepcopy(intended_value)
                else:
                    conflicts.append(
                        _conflict(path + (key,), base_value, intended_value, authoritative_value)
                    )
                continue
            if authoritative_value is _MISSING:
                conflicts.append(
                    _conflict(path + (key,), base_value, intended_value, authoritative_value)
                )
                continue
            merged[key] = _three_way_value(
                base_value,
                intended_value,
                authoritative_value,
                path + (key,),
                conflicts,
            )
        return merged
    if authoritative == base or authoritative == intended:
        return copy.deepcopy(intended)
    if isinstance(base, list) and isinstance(intended, list) and isinstance(authoritative, list):
        return _merge_list(base, intended, authoritative, path, conflicts)
    conflicts.append(_conflict(path, base, intended, authoritative))
    return copy.deepcopy(authoritative)


def three_way_merge(
    base: dict[str, Any], intended: dict[str, Any], authoritative: dict[str, Any]
) -> tuple[dict[str, Any], tuple[ConflictField, ...]]:
    """Merge unrelated recursive edits and identify every same-field race."""
    conflicts: list[ConflictField] = []
    merged = _three_way_value(base, intended, authoritative, (), conflicts)
    assert isinstance(merged, dict)
    return merged, tuple(conflicts)


def set_conflict_value(
    traveler: dict[str, Any], path: tuple[str | int, ...], value: Any
) -> None:
    """Apply one conflict choice to a desktop copy without touching storage."""
    if not path:
        raise PersistenceValidationError("A whole-document conflict cannot be replaced.")
    current: Any = traveler
    for component in path[:-1]:
        current = current[component]
    final = path[-1]
    if isinstance(final, int) and value is None:
        del current[final]
    else:
        current[final] = copy.deepcopy(value)


def _path_value(traveler: dict[str, Any], path: tuple[str | int, ...]) -> Any:
    current: Any = traveler
    for component in path:
        current = current[component]
    return copy.deepcopy(current)


def rebase_conflict_intent(
    conflict: PersistenceConflict, intended: dict[str, Any]
) -> dict[str, Any]:
    """Rebase unsaved edits while honoring explicit keep/replace choices."""
    if conflict.base_snapshot is None:
        return copy.deepcopy(intended)
    rebased, _remaining_conflicts = three_way_merge(
        conflict.base_snapshot.traveler,
        intended,
        conflict.latest_snapshot.traveler,
    )
    for field in conflict.conflicts:
        selected = _path_value(intended, field.path)
        set_conflict_value(rebased, field.path, selected)
    return rebased


class LocalTravelerPersistence(TravelerPersistence):
    """Coordinated compatibility writer for the existing local jobs directory."""

    mode = "local"

    def __init__(
        self,
        jobs_directory: str | os.PathLike[str],
        *,
        lock_timeout: float | None = 10.0,
        id_factory: Callable[[], str] | None = None,
        hooks: LocalWriteHooks | None = None,
    ) -> None:
        if lock_timeout is not None and lock_timeout < 0:
            raise ValueError("Lock timeout must be zero or greater, or None.")
        self.root = _resolve_root(jobs_directory, must_exist=False)
        self.lock_timeout = lock_timeout
        self.id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self.hooks = hooks or LocalWriteHooks()

    def _ensure_root(self) -> Path:
        _assert_test_write_path(self.root / LOCK_DIRECTORY_NAME / "guard", purpose="jobs write")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise PersistenceStorageError("The jobs directory could not be created.") from error
        return _resolve_root(self.root, must_exist=True)

    def _entries(self) -> list[tuple[str, Path]]:
        root = _resolve_root(self.root, must_exist=True)
        try:
            paths = list(root.iterdir())
        except OSError as error:
            raise PersistenceStorageError("The jobs directory could not be read.") from error
        filenames: dict[str, Path] = {}
        jobs: dict[str, Path] = {}
        entries: list[tuple[str, Path]] = []
        for path in paths:
            if path.name == LOCK_DIRECTORY_NAME or path.suffix.casefold() != ".json":
                continue
            if path.is_symlink() or not path.is_file():
                raise PersistenceStorageError("A traveler entry is unsafe.")
            _safe_job_number(path.stem)
            filename_key = path.name.casefold()
            if filename_key in filenames:
                raise PersistenceStorageError("Traveler filenames are ambiguous.")
            filenames[filename_key] = path
            snapshot = _snapshot(path)
            job_key = snapshot.job_number.casefold()
            if job_key in jobs:
                raise PersistenceStorageError("Traveler job numbers are ambiguous.")
            jobs[job_key] = path
            entries.append((snapshot.job_number, path))
        return sorted(entries, key=lambda item: (item[0].casefold(), item[0]))

    def load_path(self, path: str | os.PathLike[str]) -> TravelerSnapshot:
        root = _resolve_root(self.root, must_exist=True)
        return _snapshot(_resolve_direct_target(root, path))

    def load(self, job_number: object) -> TravelerSnapshot:
        requested = _safe_job_number(job_number)
        matches = [
            path
            for value, path in self._entries()
            if value.casefold() == requested.casefold()
        ]
        if not matches:
            raise PersistenceNotFoundError("The Job Traveler was not found.")
        if len(matches) != 1:
            raise PersistenceStorageError("The Job Traveler identity is ambiguous.")
        return _snapshot(matches[0], requested)

    def list_summaries(self) -> list[TravelerSummary]:
        summaries, errors = self.list_summaries_with_errors()
        if errors:
            raise PersistenceValidationError(
                "One or more Job Traveler files could not be interpreted safely."
            )
        return summaries

    @staticmethod
    def _summary(snapshot: TravelerSnapshot) -> TravelerSummary:
        traveler = snapshot.traveler
        return TravelerSummary(
            job_number=snapshot.job_number,
            customer=traveler.get("customer", ""),
            part_number=traveler.get("part_number", traveler.get("part_num", "")),
            quantity=traveler.get(
                "qty_to_make", traveler.get("part_total", traveler.get("quantity", ""))
            ),
            status=(
                "Completed"
                if all(
                    domain.status_if_missing(traveler, section) == "Completed"
                    for section in domain.SECTIONS
                )
                else "In Progress"
                if any(
                    domain.status_if_missing(traveler, section)
                    in {"In Progress", "Completed"}
                    for section in domain.SECTIONS
                )
                else "Pending"
            ),
            snapshot=snapshot,
        )

    def list_summaries_with_errors(
        self,
    ) -> tuple[list[TravelerSummary], list[tuple[Path, str]]]:
        """Preserve the legacy GUI's skip-and-count behavior for bad local files."""
        root = _resolve_root(self.root, must_exist=True)
        try:
            paths = sorted(
                (
                    path
                    for path in root.iterdir()
                    if path.name != LOCK_DIRECTORY_NAME
                    and path.suffix.casefold() == ".json"
                ),
                key=lambda path: (path.name.casefold(), path.name),
            )
        except OSError as error:
            raise PersistenceStorageError("The jobs directory could not be read.") from error
        summaries: list[TravelerSummary] = []
        errors: list[tuple[Path, str]] = []
        seen_filenames: set[str] = set()
        seen_jobs: set[str] = set()
        for path in paths:
            try:
                if path.name.casefold() in seen_filenames:
                    raise PersistenceStorageError("The traveler filename is ambiguous.")
                seen_filenames.add(path.name.casefold())
                if path.is_symlink() or not path.is_file():
                    raise PersistenceStorageError("The traveler entry is unsafe.")
                _safe_job_number(path.stem)
                snapshot = _snapshot(path)
                if snapshot.job_number.casefold() in seen_jobs:
                    raise PersistenceStorageError("The traveler job number is ambiguous.")
                seen_jobs.add(snapshot.job_number.casefold())
                summaries.append(self._summary(snapshot))
            except PersistenceError as error:
                errors.append((path, str(error)))
        return summaries, errors

    def _atomic_replace(self, target: Path, candidate: dict[str, Any]) -> None:
        data = _dumps_strict(candidate)
        _assert_test_write_path(target, purpose="traveler write")
        if self.hooks.before_stage is not None:
            self.hooks.before_stage(target)
        existing_mode: int | None = None
        try:
            existing_mode = stat.S_IMODE(target.stat().st_mode)
        except FileNotFoundError:
            pass
        descriptor = -1
        stage: Path | None = None
        identity: tuple[int, int] | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=_stage_prefix(target), suffix=".tmp", dir=target.parent
            )
            stage = Path(name)
            state = os.fstat(descriptor)
            identity = (state.st_dev, state.st_ino)
            if existing_mode is not None:
                os.fchmod(descriptor, existing_mode)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            _validate_stage(stage)
            if self.hooks.after_stage_fsync is not None:
                self.hooks.after_stage_fsync(stage)
            if self.hooks.before_replace is not None:
                self.hooks.before_replace(target)
            os.replace(stage, target)
            stage = None
            _fsync_directory(target.parent)
            if self.hooks.after_replace is not None:
                self.hooks.after_replace(target)
        except PersistenceError:
            raise
        except OSError as error:
            raise PersistenceStorageError("The traveler could not be replaced safely.") from error
        except Exception as error:
            raise PersistenceStorageError("The traveler write was interrupted safely.") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if stage is not None and identity is not None:
                try:
                    current = stage.stat(follow_symlinks=False)
                    if not stage.is_symlink() and (current.st_dev, current.st_ino) == identity:
                        stage.unlink()
                except (FileNotFoundError, OSError):
                    pass

    def _save_locked(
        self,
        target: Path,
        base: TravelerSnapshot,
        intended: dict[str, Any],
        *,
        action: str,
    ) -> SaveResult:
        if action not in {"logical_save", "plan_resize"}:
            raise UnsupportedPersistenceAction("The requested save action is unsupported.")
        authoritative = _snapshot(target, base.job_number)
        try:
            intended_document = domain.canonical_job(intended)
            domain.validate_traveler_structure(intended_document)
            intended_job = domain.parts_count_projection(intended_document)["job_number"]
        except (TypeError, ValueError, RecursionError) as error:
            raise PersistenceValidationError("The intended Job Traveler is invalid.") from error
        if intended_job.casefold() != base.job_number.casefold():
            raise PersistenceValidationError("A save cannot change the Job Traveler job number.")

        merged, conflicts = three_way_merge(
            base.traveler, intended_document, authoritative.traveler
        )
        if conflicts:
            raise PersistenceConflict(conflicts, authoritative, base_snapshot=base)
        if merged == authoritative.traveler:
            return SaveResult(authoritative, changed=False)

        try:
            domain.validate_traveler_structure(merged)
            prior_revision = domain.document_revision(authoritative.traveler)
            prior_mutation = domain.last_applied_mutation_id(authoritative.traveler)
            candidate = domain.bootstrap_stable_identities(merged, self.id_factory)
            candidate = domain.confirm_local_save_metadata(
                candidate, prior_revision=prior_revision
            )
            domain.validate_traveler_structure(candidate)
        except (TypeError, ValueError, RecursionError) as error:
            raise PersistenceValidationError("The merged Job Traveler is invalid.") from error

        self._atomic_replace(target, candidate)
        confirmed = _snapshot(target, base.job_number)
        try:
            if (
                confirmed.document_revision != prior_revision + 1
                or domain.last_applied_mutation_id(confirmed.traveler) != prior_mutation
            ):
                raise PersistenceStorageError("The saved traveler could not be confirmed.")
        except ValueError as error:
            raise PersistenceStorageError("The saved traveler could not be confirmed.") from error
        return SaveResult(confirmed, changed=True)

    def create(self, traveler: dict[str, Any], *, overwrite: bool = False) -> SaveResult:
        try:
            intended = domain.canonical_job(traveler)
            domain.validate_traveler_structure(intended)
            job_number = domain.parts_count_projection(intended)["job_number"]
        except (TypeError, ValueError, RecursionError) as error:
            raise PersistenceValidationError("The intended Job Traveler is invalid.") from error
        root = self._ensure_root()
        target = _resolve_direct_target(root, f"{_safe_job_number(job_number)}.json")
        with _DestinationLock(root, target, self.lock_timeout):
            _recover_locked(target)
            if target.exists():
                if not overwrite:
                    raise PersistenceAlreadyExistsError("The Job Traveler already exists.")
                base = _snapshot(target, job_number)
                return self._save_locked(
                    target, base, intended, action="logical_save"
                )
            try:
                candidate = domain.bootstrap_stable_identities(intended, self.id_factory)
                candidate = domain.confirm_local_save_metadata(candidate, prior_revision=0)
                domain.validate_traveler_structure(candidate)
            except (TypeError, ValueError, RecursionError) as error:
                raise PersistenceValidationError("The intended Job Traveler is invalid.") from error
            self._atomic_replace(target, candidate)
            confirmed = _snapshot(target, job_number)
            if confirmed.document_revision != 1:
                raise PersistenceStorageError("The created traveler could not be confirmed.")
            return SaveResult(confirmed, changed=True)

    def save(
        self,
        base: TravelerSnapshot,
        intended: dict[str, Any],
        *,
        action: str = "logical_save",
    ) -> SaveResult:
        if not isinstance(base, TravelerSnapshot) or base.location is None:
            raise PersistenceValidationError("A local save requires its loaded snapshot.")
        root = _resolve_root(self.root, must_exist=True)
        target = _resolve_direct_target(root, base.location)
        with _DestinationLock(root, target, self.lock_timeout):
            _recover_locked(target)
            if not target.exists():
                raise PersistenceNotFoundError("The Job Traveler was not found.")
            return self._save_locked(target, base, intended, action=action)


def build_persistence(
    *,
    jobs_directory: str | os.PathLike[str] | None,
    mode: str | None = None,
    service_client=None,
) -> TravelerPersistence:
    """Build one explicit mode, defaulting only to local compatibility mode."""
    if mode is None:
        if MODE_ENV in os.environ:
            raw_mode = os.environ[MODE_ENV]
            if not raw_mode or raw_mode != raw_mode.strip():
                raise PersistenceConfigurationError("Persistence mode is invalid.")
            selected = raw_mode.casefold()
        else:
            selected = "local"
    elif not isinstance(mode, str) or not mode or mode != mode.strip():
        raise PersistenceConfigurationError("Persistence mode is invalid.")
    else:
        selected = mode.casefold()

    if selected == "local":
        if service_client is not None or jobs_directory is None:
            raise PersistenceConfigurationError("Local persistence configuration is ambiguous.")
        return LocalTravelerPersistence(jobs_directory)
    if selected == "service":
        if jobs_directory is not None or service_client is None:
            raise PersistenceConfigurationError(
                "Service mode requires an explicitly injected authenticated client and no local path."
            )
        from traveler_service_persistence import ServiceTravelerPersistence

        return ServiceTravelerPersistence(service_client)
    raise PersistenceConfigurationError("Persistence mode must be 'local' or 'service'.")


__all__ = [
    "ConflictField",
    "LocalTravelerPersistence",
    "LocalWriteHooks",
    "MODE_ENV",
    "PersistenceAlreadyExistsError",
    "PersistenceConfigurationError",
    "PersistenceConflict",
    "PersistenceError",
    "PersistenceLockTimeoutError",
    "PersistenceNotFoundError",
    "PlanResizeConfirmationRequired",
    "PlanResizeConflict",
    "PersistenceStorageError",
    "PersistenceValidationError",
    "SaveResult",
    "TravelerPersistence",
    "TravelerSnapshot",
    "TravelerSummary",
    "UnsupportedPersistenceAction",
    "build_persistence",
    "canonical_job_lock_identity",
    "canonical_job_lock_path",
    "rebase_conflict_intent",
    "set_conflict_value",
    "three_way_merge",
]
