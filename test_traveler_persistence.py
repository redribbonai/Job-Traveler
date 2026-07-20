"""Phase 2C desktop persistence, conflict, lock, and isolation coverage."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from filelock import FileLock


os.environ.setdefault("JOB_TRAVELER_TEST_PROCESS", "job-traveler-tests")
os.environ.setdefault("JOB_TRAVELER_TEST_WRITE_ROOTS", tempfile.gettempdir())

import job_traveler as terminal
import job_traveler_gui as gui
import traveler_domain as domain
from traveler_persistence import (
    ConflictField,
    LocalTravelerPersistence,
    LocalWriteHooks,
    PersistenceConflict,
    PersistenceConfigurationError,
    PersistenceStorageError,
    SaveResult,
    TravelerPersistence,
    TravelerSnapshot,
    TravelerSummary,
    build_persistence,
    canonical_job_lock_identity,
    canonical_job_lock_path,
)


REPOSITORY = Path(__file__).resolve().parent
FIXTURE = REPOSITORY / "test_fixtures" / "canonical" / "SANITIZED-MULTI.json"
LEGACY_FIXTURE = REPOSITORY / "test_fixtures" / "legacy" / "SANITIZED-LEGACY.json"


def fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def sequence_factory(start=1, calls=None):
    counter = iter(range(start, start + 1_000))

    def generate():
        value = str(uuid.UUID(int=next(counter)))
        if calls is not None:
            calls.append(value)
        return value

    return generate


class FakePersistence(TravelerPersistence):
    mode = "fake"

    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.saved = []
        self.resolved = []
        self.conflict = None

    def load(self, job_number):
        return self.snapshot

    def list_summaries(self):
        return [TravelerSummary(self.snapshot.job_number, "C", "P", 1, "Pending")]

    def create(self, traveler, *, overwrite=False):
        self.saved.append(("create", copy.deepcopy(traveler), overwrite))
        return SaveResult(self.snapshot, True)

    def save(self, base, intended, *, action="logical_save"):
        self.saved.append(("save", copy.deepcopy(intended), action))
        if self.conflict is not None:
            conflict, self.conflict = self.conflict, None
            raise conflict
        return SaveResult(self.snapshot, True)

    def resolve_conflict(self, conflict, intended, *, action="logical_save"):
        self.resolved.append((copy.deepcopy(intended), action))
        return SaveResult(self.snapshot, True)


class LocalPersistenceTests(unittest.TestCase):
    def test_legacy_noop_preserves_bytes_then_confirmed_edit_bootstraps_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory)
            path = jobs / "SANITIZED-LEGACY.json"
            path.write_bytes(LEGACY_FIXTURE.read_bytes())
            persistence = LocalTravelerPersistence(
                jobs, id_factory=sequence_factory()
            )
            loaded = persistence.load("SANITIZED-LEGACY")
            before = path.read_bytes()
            no_op = persistence.save(loaded, copy.deepcopy(loaded.traveler))
            self.assertFalse(no_op.changed)
            self.assertEqual(path.read_bytes(), before)
            self.assertNotIn("_shopos", json.loads(path.read_text(encoding="utf-8")))

            intended = copy.deepcopy(loaded.traveler)
            intended["saw_cutting"]["notes"] = "CONFIRMED-LEGACY-EDIT"
            saved = persistence.save(loaded, intended).snapshot
            self.assertEqual(saved.document_revision, 1)
            self.assertEqual(
                domain.operation_reference_contract(saved.traveler), "stable_uuid_v1"
            )
            self.assertEqual(
                saved.traveler["compatible_top_level"],
                {"nested": {"preserve": "yes"}},
            )

    def test_confirmed_save_noop_revision_ids_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory)
            calls = []
            persistence = LocalTravelerPersistence(
                jobs, id_factory=sequence_factory(calls=calls)
            )
            created = persistence.create(fixture())
            first_bytes = created.snapshot.location.read_bytes()
            first_ids = domain.stable_identity_projection(created.snapshot.traveler)

            self.assertEqual(created.snapshot.document_revision, 1)
            self.assertIsNone(domain.last_applied_mutation_id(created.snapshot.traveler))
            self.assertEqual(len(calls), 9)
            self.assertEqual(
                created.snapshot.traveler["compatible_top_level"], {"preserve": True}
            )

            no_op = persistence.save(
                created.snapshot, copy.deepcopy(created.snapshot.traveler)
            )
            self.assertFalse(no_op.changed)
            self.assertEqual(no_op.snapshot.document_revision, 1)
            self.assertEqual(created.snapshot.location.read_bytes(), first_bytes)
            self.assertEqual(len(calls), 9)

            intended = copy.deepcopy(no_op.snapshot.traveler)
            intended["programming"]["operations"][0]["program_name"] = "LOCAL-EDIT"
            saved = persistence.save(no_op.snapshot, intended)
            self.assertTrue(saved.changed)
            self.assertEqual(saved.snapshot.document_revision, 2)
            self.assertEqual(
                domain.stable_identity_projection(saved.snapshot.traveler), first_ids
            )
            self.assertIsNone(domain.last_applied_mutation_id(saved.snapshot.traveler))
            self.assertEqual(len(calls), 9)

    def test_unrelated_edits_merge_and_same_field_conflicts_are_structured(self):
        with tempfile.TemporaryDirectory() as directory:
            persistence = LocalTravelerPersistence(
                directory, id_factory=sequence_factory()
            )
            created = persistence.create(fixture()).snapshot
            first_view = persistence.load(created.job_number)
            second_view = persistence.load(created.job_number)
            first = copy.deepcopy(first_view.traveler)
            first["programming"]["operations"][0]["program_name"] = "FIRST"
            persistence.save(first_view, first)

            unrelated = copy.deepcopy(second_view.traveler)
            unrelated["programming"]["operations"][0]["notes"] = "UNRELATED"
            merged = persistence.save(second_view, unrelated).snapshot
            self.assertEqual(
                merged.traveler["programming"]["operations"][0]["program_name"],
                "FIRST",
            )
            self.assertEqual(
                merged.traveler["programming"]["operations"][0]["notes"],
                "UNRELATED",
            )

            stale_one = persistence.load(created.job_number)
            stale_two = persistence.load(created.job_number)
            winner = copy.deepcopy(stale_one.traveler)
            winner["programming"]["operations"][0]["program_name"] = "WINNER"
            persistence.save(stale_one, winner)
            loser = copy.deepcopy(stale_two.traveler)
            loser["programming"]["operations"][0]["program_name"] = "LOSER"
            loser["programming"]["operations"][0]["notes"] = "KEEP-ME"
            before = stale_two.location.read_bytes()
            with self.assertRaises(PersistenceConflict) as raised:
                persistence.save(stale_two, loser)
            conflict = raised.exception
            self.assertEqual(len(conflict.conflicts), 1)
            self.assertEqual(conflict.conflicts[0].intended_value, "LOSER")
            self.assertEqual(conflict.conflicts[0].authoritative_value, "WINNER")
            self.assertEqual(stale_two.location.read_bytes(), before)

            from traveler_persistence import set_conflict_value

            set_conflict_value(
                loser,
                conflict.conflicts[0].path,
                conflict.conflicts[0].authoritative_value,
            )
            kept = persistence.resolve_conflict(conflict, loser).snapshot
            self.assertEqual(
                kept.traveler["programming"]["operations"][0]["program_name"],
                "WINNER",
            )
            self.assertEqual(
                kept.traveler["programming"]["operations"][0]["notes"], "KEEP-ME"
            )

    def test_deliberate_replacement_uses_latest_and_second_race_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            persistence = LocalTravelerPersistence(
                directory, id_factory=sequence_factory()
            )
            base = persistence.create(fixture()).snapshot
            stale = persistence.load(base.job_number)
            winner = copy.deepcopy(base.traveler)
            winner["programming"]["operations"][0]["program_name"] = "ONE"
            persistence.save(base, winner)
            intended = copy.deepcopy(stale.traveler)
            intended["programming"]["operations"][0]["program_name"] = "MINE"
            with self.assertRaises(PersistenceConflict) as first:
                persistence.save(stale, intended)

            intervening_base = persistence.load(base.job_number)
            intervening = copy.deepcopy(intervening_base.traveler)
            intervening["programming"]["operations"][0]["program_name"] = "TWO"
            persistence.save(intervening_base, intervening)
            with self.assertRaises(PersistenceConflict) as second:
                persistence.resolve_conflict(first.exception, intended)
            self.assertEqual(
                second.exception.conflicts[0].authoritative_value, "TWO"
            )

            confirmed = persistence.resolve_conflict(
                second.exception, intended
            ).snapshot
            self.assertEqual(
                confirmed.traveler["programming"]["operations"][0]["program_name"],
                "MINE",
            )

    def test_resize_preserves_retires_and_never_recycles_operation_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            persistence = LocalTravelerPersistence(
                directory, id_factory=sequence_factory(calls=calls)
            )
            current = persistence.create(fixture()).snapshot
            initial = domain.stable_identity_projection(current.traveler)[
                "machining_operations"
            ]

            expanded_doc = domain.resize_operation_plan(current.traveler, 3)
            expanded = persistence.save(
                current, expanded_doc, action="plan_resize"
            ).snapshot
            expanded_ids = domain.stable_identity_projection(expanded.traveler)[
                "machining_operations"
            ]
            self.assertEqual(expanded_ids["1"], initial["1"])
            self.assertEqual(expanded_ids["2"], initial["2"])
            retired_id = expanded_ids["3"]

            shrunk_doc = domain.resize_operation_plan(expanded.traveler, 2, True)
            shrunk = persistence.save(
                expanded, shrunk_doc, action="plan_resize"
            ).snapshot
            self.assertEqual(
                set(
                    domain.stable_identity_projection(shrunk.traveler)[
                        "machining_operations"
                    ]
                ),
                {"1", "2"},
            )

            reexpanded_doc = domain.resize_operation_plan(shrunk.traveler, 3)
            reexpanded = persistence.save(
                shrunk, reexpanded_doc, action="plan_resize"
            ).snapshot
            new_id = domain.stable_identity_projection(reexpanded.traveler)[
                "machining_operations"
            ]["3"]
            self.assertNotEqual(new_id, retired_id)
            self.assertEqual(reexpanded.document_revision, 4)

            stale_one = persistence.load(reexpanded.job_number)
            stale_two = persistence.load(reexpanded.job_number)
            winner = copy.deepcopy(stale_one.traveler)
            winner["programming"]["programmer"] = "WINNER"
            persistence.save(stale_one, winner)
            attempted_resize = domain.resize_operation_plan(stale_two.traveler, 4)
            attempted_resize["programming"]["programmer"] = "LOSER"
            before_conflict_calls = len(calls)
            with self.assertRaises(PersistenceConflict):
                persistence.save(
                    stale_two, attempted_resize, action="plan_resize"
                )
            self.assertEqual(len(calls), before_conflict_calls)

    def test_failed_staging_cleans_up_and_does_not_claim_or_increment(self):
        with tempfile.TemporaryDirectory() as directory:
            stable = LocalTravelerPersistence(
                directory, id_factory=sequence_factory()
            ).create(fixture()).snapshot
            before = stable.location.read_bytes()

            def stop(_path):
                raise RuntimeError("simulated interruption")

            failing = LocalTravelerPersistence(
                directory,
                id_factory=sequence_factory(start=100),
                hooks=LocalWriteHooks(before_replace=stop),
            )
            intended = copy.deepcopy(stable.traveler)
            intended["programming"]["operations"][0]["notes"] = "NOT-SAVED"
            with self.assertRaises(PersistenceStorageError):
                failing.save(stable, intended)
            self.assertEqual(stable.location.read_bytes(), before)
            self.assertFalse(list(Path(directory).glob(".*shopos-stage-*.tmp")))
            self.assertEqual(
                domain.document_revision(
                    json.loads(stable.location.read_text(encoding="utf-8"))
                ),
                stable.document_revision,
            )

    def test_lock_identity_has_golden_vectors_and_persistent_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory).resolve()
            path = jobs / "SANITIZED-MULTI.json"
            expected = os.path.normcase(os.path.normpath(str(path)))
            self.assertEqual(canonical_job_lock_identity(jobs, path), expected)
            self.assertEqual(
                canonical_job_lock_identity(jobs, path, windows=True),
                expected.casefold(),
            )
            digest = hashlib.sha256(expected.encode("utf-8")).hexdigest()
            self.assertEqual(
                canonical_job_lock_path(jobs, path),
                jobs / ".shopos-locks" / f"{digest}.lock",
            )

    def test_lock_timeout_writes_no_stage_or_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            persistence = LocalTravelerPersistence(
                directory, id_factory=sequence_factory()
            )
            snapshot = persistence.create(fixture()).snapshot
            intended = copy.deepcopy(snapshot.traveler)
            intended["programming"]["operations"][0]["notes"] = "LOCKED-OUT"
            lock_path = canonical_job_lock_path(directory, snapshot.location)
            before = snapshot.location.read_bytes()
            with FileLock(str(lock_path)):
                locked = LocalTravelerPersistence(directory, lock_timeout=0)
                from traveler_persistence import PersistenceLockTimeoutError

                with self.assertRaises(PersistenceLockTimeoutError):
                    locked.save(snapshot, intended)
            self.assertEqual(snapshot.location.read_bytes(), before)
            self.assertFalse(list(Path(directory).glob(".*shopos-stage-*.tmp")))

    def test_mode_selection_defaults_local_and_service_never_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("JOB_TRAVELER_PERSISTENCE_MODE", None)
                selected = build_persistence(jobs_directory=directory)
                self.assertEqual(selected.mode, "local")
            with self.assertRaises(PersistenceConfigurationError):
                build_persistence(
                    jobs_directory=directory, mode="service", service_client=None
                )
            with self.assertRaises(PersistenceConfigurationError):
                build_persistence(jobs_directory=directory, mode="ambiguous")

    def test_test_guard_rejects_production_path_without_instantiating_writer(self):
        production_jobs = REPOSITORY / "jobs"
        lock_directory = production_jobs / ".shopos-locks"
        before = tuple(lock_directory.iterdir()) if lock_directory.exists() else None
        from traveler_persistence import _assert_test_write_path

        with self.assertRaisesRegex(PersistenceStorageError, "approved temporary roots"):
            _assert_test_write_path(
                production_jobs / "TEST-GUARD-NOT-WRITTEN.json",
                purpose="traveler write",
            )
        after = tuple(lock_directory.iterdir()) if lock_directory.exists() else None
        self.assertEqual(after, before)
        self.assertFalse((production_jobs / "TEST-GUARD-NOT-WRITTEN.json").exists())


class BoundaryDelegationAndConflictTests(unittest.TestCase):
    def snapshot(self):
        traveler = domain.canonical_job(fixture())
        return TravelerSnapshot(
            job_number="SANITIZED-MULTI",
            traveler=traveler,
            read_version="sha256:" + "1" * 64,
            document_revision=0,
        )

    def test_terminal_save_delegates_and_adopts_only_confirmed_state(self):
        snapshot = self.snapshot()
        fake = FakePersistence(snapshot)
        job = copy.deepcopy(snapshot.traveler)
        terminal._loaded_snapshots.pop(id(job), None)
        with redirect_output():
            self.assertTrue(terminal.save_job(job, persistence=fake))
        self.assertEqual(fake.saved[0][0], "create")
        self.assertEqual(job, snapshot.traveler)

    def test_terminal_conflict_defaults_keep_and_cancel_never_resolves(self):
        snapshot = self.snapshot()
        latest_doc = copy.deepcopy(snapshot.traveler)
        latest_doc["programming"]["operations"][0]["program_name"] = "SERVER"
        latest = TravelerSnapshot(
            job_number=snapshot.job_number,
            traveler=latest_doc,
            read_version="sha256:" + "2" * 64,
            document_revision=1,
        )
        path = ("programming", "operations", 0, "program_name")
        conflict = PersistenceConflict(
            [
                ConflictField(
                    path,
                    "BASE",
                    "MINE",
                    "SERVER",
                    domain.deterministic_value_hash("SERVER"),
                )
            ],
            latest,
        )
        intended = copy.deepcopy(snapshot.traveler)
        intended["programming"]["operations"][0]["program_name"] = "MINE"
        intended["programming"]["operations"][0]["notes"] = "UNRELATED"
        fake = FakePersistence(snapshot)
        with (
            mock.patch("builtins.input", return_value=""),
            redirect_output(),
        ):
            result = terminal._resolve_terminal_conflict(
                fake, conflict, intended, action="logical_save"
            )
        self.assertIsNotNone(result)
        self.assertEqual(
            fake.resolved[0][0]["programming"]["operations"][0]["program_name"],
            "SERVER",
        )
        self.assertEqual(
            fake.resolved[0][0]["programming"]["operations"][0]["notes"],
            "UNRELATED",
        )

        canceled = FakePersistence(snapshot)
        with (
            mock.patch("builtins.input", return_value="c"),
            redirect_output(),
        ):
            self.assertIsNone(
                terminal._resolve_terminal_conflict(
                    canceled,
                    conflict,
                    copy.deepcopy(intended),
                    action="logical_save",
                )
            )
        self.assertFalse(canceled.resolved)

    def test_gui_conflict_defaults_to_authoritative_and_preserves_unrelated_input(self):
        snapshot = self.snapshot()
        latest_doc = copy.deepcopy(snapshot.traveler)
        latest_doc["programming"]["operations"][0]["program_name"] = "SERVER"
        latest = TravelerSnapshot(
            job_number=snapshot.job_number,
            traveler=latest_doc,
            read_version="sha256:" + "2" * 64,
            document_revision=1,
        )
        path = ("programming", "operations", 0, "program_name")
        conflict = PersistenceConflict(
            [
                ConflictField(
                    path,
                    "BASE",
                    "MINE",
                    "SERVER",
                    domain.deterministic_value_hash("SERVER"),
                )
            ],
            latest,
        )
        fake = FakePersistence(snapshot)
        fake.conflict = conflict
        app = gui.JobTravelerApp.__new__(gui.JobTravelerApp)
        app.root = object()
        app.persistence = fake
        app.current_snapshot = snapshot
        app.current_job = copy.deepcopy(snapshot.traveler)
        app.current_path = None
        candidate = copy.deepcopy(snapshot.traveler)
        candidate["programming"]["operations"][0]["program_name"] = "MINE"
        candidate["programming"]["operations"][0]["notes"] = "UNSAVED-UNRELATED"
        with (
            mock.patch.object(gui.messagebox, "askyesnocancel", return_value=False),
            mock.patch.object(gui.messagebox, "showinfo"),
            mock.patch.object(gui.messagebox, "showerror"),
        ):
            self.assertTrue(app._persist_candidate(candidate, "Confirmed"))
        resolved = fake.resolved[0][0]
        self.assertEqual(
            resolved["programming"]["operations"][0]["program_name"], "SERVER"
        )
        self.assertEqual(
            resolved["programming"]["operations"][0]["notes"],
            "UNSAVED-UNRELATED",
        )

    def test_gui_conflict_cancellation_performs_no_resolution_or_success(self):
        snapshot = self.snapshot()
        path = ("programming", "operations", 0, "program_name")
        conflict = PersistenceConflict(
            [
                ConflictField(
                    path,
                    "BASE",
                    "MINE",
                    "SERVER",
                    domain.deterministic_value_hash("SERVER"),
                )
            ],
            snapshot,
        )
        fake = FakePersistence(snapshot)
        fake.conflict = conflict
        app = gui.JobTravelerApp.__new__(gui.JobTravelerApp)
        app.root = object()
        app.persistence = fake
        app.current_snapshot = snapshot
        app.current_job = copy.deepcopy(snapshot.traveler)
        app.current_path = None
        with (
            mock.patch.object(gui.messagebox, "askyesnocancel", return_value=None),
            mock.patch.object(gui.messagebox, "showinfo") as success,
        ):
            self.assertFalse(
                app._persist_candidate(copy.deepcopy(snapshot.traveler), "Saved")
            )
        self.assertFalse(fake.resolved)
        success.assert_not_called()

    def test_entry_points_contain_no_raw_traveler_json_writer(self):
        for filename in ("job_traveler.py", "job_traveler_gui.py"):
            source = (REPOSITORY / filename).read_text(encoding="utf-8")
            tree = ast.parse(source)
            calls = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and (
                    node.func.attr in {"dump", "write_bytes", "rename"}
                    or (
                        node.func.attr == "replace"
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "os"
                    )
                )
            ]
            self.assertFalse(calls, f"raw traveler writer remains in {filename}")
        gui_source = (REPOSITORY / "job_traveler_gui.py").read_text(encoding="utf-8")
        self.assertIn("self.persistence.save", gui_source)
        terminal_source = (REPOSITORY / "job_traveler.py").read_text(encoding="utf-8")
        self.assertIn("active.save", terminal_source)

    def test_every_known_terminal_and_tkinter_save_path_delegates(self):
        terminal_tree = ast.parse(
            (REPOSITORY / "job_traveler.py").read_text(encoding="utf-8")
        )
        terminal_functions = {
            node.name: node
            for node in terminal_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in (
            "create_new_job",
            "update_programming",
            "update_saw_cutting",
            "update_cnc_machining",
            "update_deburr",
            "update_inspection",
            "update_packing",
            "update_shipping",
        ):
            calls = [
                node
                for node in ast.walk(terminal_functions[name])
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "save_job"
            ]
            self.assertTrue(calls, f"{name} does not delegate to save_job")

        gui_source = (REPOSITORY / "job_traveler_gui.py").read_text(encoding="utf-8")
        self.assertIn("result = self.persistence.create(job, overwrite=False)", gui_source)
        self.assertGreaterEqual(gui_source.count("self._persist_candidate("), 7)
        self.assertNotIn("save_job_to_path(\n                candidate", gui_source)


class redirect_output:
    def __enter__(self):
        import contextlib
        import io

        self.stream = io.StringIO()
        self.context = contextlib.redirect_stdout(self.stream)
        return self.context.__enter__()

    def __exit__(self, *args):
        return self.context.__exit__(*args)


if __name__ == "__main__":
    unittest.main()
