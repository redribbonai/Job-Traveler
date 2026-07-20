"""Phase 2E desktop participation in the shared backup barrier."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from filelock import FileLock


os.environ.setdefault("JOB_TRAVELER_TEST_PROCESS", "job-traveler-tests")
os.environ.setdefault("JOB_TRAVELER_TEST_WRITE_ROOTS", tempfile.gettempdir())

from shopos_coordination import (
    CoordinationConfigurationError,
    CoordinationSettings,
    canonical_coordination_identity,
)
from traveler_persistence import (
    LocalTravelerPersistence,
    PersistenceLockTimeoutError,
)


REPOSITORY = Path(__file__).resolve().parent
FIXTURE = REPOSITORY / "test_fixtures" / "canonical" / "SANITIZED-MULTI.json"


def traveler_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class ShopOSCoordinationTests(unittest.TestCase):
    def test_explicit_path_validation_rejects_relative_root_and_symlink(self):
        with self.assertRaises(CoordinationConfigurationError):
            CoordinationSettings(True, Path("relative.lock"))
        with self.assertRaises(CoordinationConfigurationError):
            CoordinationSettings(True, Path("/shopos.lock"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir()
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaises(CoordinationConfigurationError):
                CoordinationSettings(True, alias / "shopos.lock")

    def test_barrier_timeout_never_falls_back_to_uncoordinated_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / "jobs"
            coordination = root / "coordination"
            coordination.mkdir()
            lock_path = coordination / "shopos.lock"
            held = FileLock(str(lock_path), mode=0o600)
            held.acquire(timeout=1)
            try:
                persistence = LocalTravelerPersistence(
                    jobs,
                    lock_timeout=0.05,
                    coordination=CoordinationSettings(True, lock_path, 0.05),
                )
                with self.assertRaises(PersistenceLockTimeoutError):
                    persistence.create(traveler_fixture())
                self.assertFalse(jobs.exists())
            finally:
                held.release()

            created = persistence.create(traveler_fixture())
            self.assertTrue(created.changed)
            self.assertEqual(len(list(jobs.glob("*.json"))), 1)

    def test_disabled_coordination_preserves_local_compatibility_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / "jobs"
            lock_path = root / "coordination" / "unused.lock"
            result = LocalTravelerPersistence(
                jobs,
                coordination=CoordinationSettings(False),
            ).create(traveler_fixture())
            self.assertTrue(result.changed)
            self.assertFalse(lock_path.exists())

    def test_test_process_cannot_touch_a_path_outside_its_temporary_root(self):
        with tempfile.TemporaryDirectory() as approved_directory:
            with tempfile.TemporaryDirectory() as blocked_directory:
                blocked_root = Path(blocked_directory)
                lock_path = blocked_root / "coordination" / "blocked.lock"
                lock_path.parent.mkdir()
                with mock.patch.dict(
                    os.environ,
                    {"JOB_TRAVELER_TEST_WRITE_ROOTS": approved_directory},
                ):
                    settings = CoordinationSettings(True, lock_path, 0)
                    with self.assertRaises(CoordinationConfigurationError):
                        with settings.writer():
                            pass
                self.assertFalse(lock_path.exists())

    def test_canonical_identity_is_stable_without_creating_a_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock_path = root / "coordination" / "shopos.lock"
            lock_path.parent.mkdir()
            first = canonical_coordination_identity(lock_path)
            second = canonical_coordination_identity(
                lock_path.parent / "." / lock_path.name
            )
            self.assertEqual(first, second)
            self.assertFalse(lock_path.exists())


if __name__ == "__main__":
    unittest.main()
