"""Pure domain, sanitized fixture, and desktop compatibility contract tests."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path

os.environ.setdefault("JOB_TRAVELER_TEST_PROCESS", "job-traveler-tests")
os.environ.setdefault("JOB_TRAVELER_TEST_WRITE_ROOTS", tempfile.gettempdir())

import job_traveler as terminal_app
import job_traveler_gui as gui
import traveler_domain as domain


REPOSITORY = Path(__file__).resolve().parent
LEGACY_FIXTURE = REPOSITORY / "test_fixtures" / "legacy" / "SANITIZED-LEGACY.json"
CURRENT_FIXTURE = (
    REPOSITORY / "test_fixtures" / "canonical" / "SANITIZED-MULTI.json"
)


def read_fixture(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class TravelerDomainContractTests(unittest.TestCase):
    @staticmethod
    def _stable(current):
        counter = iter(range(1, 100))
        return domain.bootstrap_stable_identities(
            current,
            lambda: str(uuid.UUID(int=next(counter))),
        )

    def test_contract_module_is_pure_to_import(self):
        with tempfile.TemporaryDirectory() as directory:
            script = (
                "import os, pathlib, sys; "
                f"sys.path.insert(0, {str(REPOSITORY)!r}); "
                "before=set(pathlib.Path('.').iterdir()); "
                "import traveler_domain; "
                "after=set(pathlib.Path('.').iterdir()); "
                "assert before == after; "
                "assert 'tkinter' not in sys.modules"
            )
            subprocess.run(
                [sys.executable, "-c", script],
                cwd=directory,
                check=True,
                capture_output=True,
                text=True,
            )

    def test_legacy_normalization_preserves_source_and_unknown_fields(self):
        original = read_fixture(LEGACY_FIXTURE)
        before = copy.deepcopy(original)
        normalized = domain.normalize_operations(original)
        self.assertEqual(original, before)
        self.assertEqual(normalized["programming"]["operations"][0]["operation_number"], 1)
        self.assertEqual(normalized["cnc_machining"]["operations"][0]["qty_complete"], 12)
        self.assertEqual(
            normalized["compatible_top_level"], {"nested": {"preserve": "yes"}}
        )
        self.assertTrue(
            normalized["programming"]["compatible_extension"]["preserve"]
        )

    def test_current_fixture_is_canonical_and_unknown_fields_round_trip(self):
        current = read_fixture(CURRENT_FIXTURE)
        canonical = domain.canonical_job(current)
        self.assertEqual(canonical, current)
        self.assertEqual(domain.canonical_job(canonical), canonical)
        self.assertEqual(canonical["compatible_top_level"], {"preserve": True})
        self.assertEqual(
            canonical["programming"]["operations"][0][
                "compatible_operation_extension"
            ],
            "preserve",
        )

    def test_malformed_protected_fields_are_rejected_without_erasure(self):
        malformed = read_fixture(CURRENT_FIXTURE)
        malformed["programming"]["operations"][1]["operation_number"] = 1
        before = copy.deepcopy(malformed)
        with self.assertRaisesRegex(
            domain.TravelerValidationError, "duplicate operation number"
        ):
            domain.validate_traveler_structure(malformed)
        self.assertEqual(malformed, before)

        malformed_section = read_fixture(CURRENT_FIXTURE)
        malformed_section["inspection"] = []
        with self.assertRaisesRegex(
            domain.TravelerValidationError, "inspection.*JSON object"
        ):
            domain.read_model(malformed_section)

    def test_desktop_compatibility_exports_are_the_domain_functions(self):
        self.assertIs(terminal_app.normalize_operations, domain.normalize_operations)
        self.assertIs(terminal_app.canonical_job, domain.canonical_job)
        self.assertIs(terminal_app.status_if_missing, domain.status_if_missing)
        self.assertIs(gui.terminal_app.normalize_operations, domain.normalize_operations)
        current = read_fixture(CURRENT_FIXTURE)
        self.assertEqual(gui.normalized_job(current), domain.normalize_operations(current))

    def test_view_print_and_normalization_never_rewrite_fixture_bytes(self):
        for path in (LEGACY_FIXTURE, CURRENT_FIXTURE):
            with self.subTest(path=path.name):
                before = hashlib.sha256(path.read_bytes()).hexdigest()
                loaded = gui.load_job_path(path)
                domain.read_model(read_fixture(path))
                gui.traveler_text(loaded)
                gui.build_traveler_print_html(
                    loaded, generated_at="2026-07-16T05:24:00+00:00"
                )
                with redirect_stdout(io.StringIO()):
                    terminal_app.print_traveler(loaded)
                after = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(after, before)

    def test_read_model_covers_all_sections_and_marks_refs_as_temporary(self):
        model = domain.read_model(read_fixture(CURRENT_FIXTURE))
        self.assertEqual(list(model["section_statuses"]), domain.SECTIONS)
        self.assertEqual(
            set(model["normalized"]).intersection(domain.SECTIONS),
            set(domain.SECTIONS),
        )
        cnc = [
            row for row in model["operations"] if row["section"] == "cnc_machining"
        ]
        self.assertEqual([row["operation_number"] for row in cnc], [1, 2])
        self.assertTrue(
            all(row["reference_stability"] == "temporary_positional" for row in cnc)
        )

    def test_parts_count_projection_preserves_established_alias_contract(self):
        aliased = {
            "job_num": "ALIAS-1",
            "part_num": "PART-1",
            "part_total": "5",
            "customer": " Example ",
        }
        self.assertEqual(
            domain.parts_count_projection(aliased),
            {
                "job_number": "ALIAS-1",
                "part_number": "PART-1",
                "qty_to_make": 5,
                "customer": "Example",
                "description": "",
            },
        )

    def test_contract_v2_bootstraps_stable_linked_identities_only_on_copy(self):
        current = read_fixture(CURRENT_FIXTURE)
        before = copy.deepcopy(current)
        stable = self._stable(current)
        self.assertEqual(current, before)
        self.assertNotIn("_shopos", current)
        identities = domain.stable_identity_projection(stable)
        self.assertEqual(set(identities["sections"]), set(domain.SECTIONS))
        self.assertEqual(set(identities["machining_operations"]), {"1", "2"})
        descriptors = domain.operation_descriptors(stable)
        linked = {
            row["section"]: row["stable_operation_id"]
            for row in descriptors
            if row["operation_number"] == 1
        }
        self.assertEqual(set(linked), {"programming", "cnc_machining", "inspection"})
        self.assertEqual(len(set(linked.values())), 1)
        self.assertEqual(domain.operation_reference_contract(stable), "stable_uuid_v1")

    def test_duplicate_or_malformed_stable_identities_are_rejected(self):
        stable = self._stable(read_fixture(CURRENT_FIXTURE))
        metadata = stable["_shopos"]["operation_identities"]
        metadata["sections"]["shipping"] = metadata["machining_operations"]["1"]
        with self.assertRaisesRegex(domain.TravelerValidationError, "unique"):
            domain.validate_traveler_structure(stable)

        malformed = self._stable(read_fixture(CURRENT_FIXTURE))
        malformed["_shopos"]["operation_identities"]["machining_operations"]["1"] = "not-a-uuid"
        with self.assertRaisesRegex(domain.TravelerValidationError, "canonical UUID"):
            domain.read_model(malformed)

    def test_revision_metadata_is_legacy_compatible_and_preserves_unknown_values(self):
        current = read_fixture(CURRENT_FIXTURE)
        self.assertEqual(domain.document_revision(current), 0)
        stable = self._stable(current)
        stable["_shopos"]["compatible_private_extension"] = {"preserve": True}
        mutation_id = str(uuid.UUID(int=999))
        confirmed = domain.confirm_mutation_metadata(
            stable, prior_revision=0, mutation_id=mutation_id
        )
        self.assertEqual(domain.document_revision(confirmed), 1)
        self.assertEqual(domain.last_applied_mutation_id(confirmed), mutation_id)
        self.assertTrue(
            domain.canonical_job(confirmed)["_shopos"]["compatible_private_extension"]["preserve"]
        )

    def test_allowed_field_change_hashes_one_field_and_preserves_unknown_fields(self):
        current = read_fixture(CURRENT_FIXTURE)
        target = {
            "section": "programming",
            "field": "program_name",
            "compatibility_reference": "programming:operation:1",
        }
        state = domain.ordinary_field_state(current, target)
        self.assertEqual(state["value"], "EXAMPLE-OP10")
        self.assertEqual(
            state["value_hash"], domain.deterministic_value_hash("EXAMPLE-OP10")
        )
        changed, confirmed = domain.apply_ordinary_field_change(
            current, target, "EXAMPLE-OP10-REV"
        )
        self.assertEqual(confirmed["value"], "EXAMPLE-OP10-REV")
        self.assertEqual(
            changed["programming"]["operations"][0]["compatible_operation_extension"],
            "preserve",
        )
        self.assertEqual(current["programming"]["operations"][0]["program_name"], "EXAMPLE-OP10")

    def test_stable_target_resolves_across_linked_sections(self):
        stable = self._stable(read_fixture(CURRENT_FIXTURE))
        operation_id = domain.stable_identity_projection(stable)[
            "machining_operations"
        ]["1"]
        target = {
            "section": "cnc_machining",
            "field": "operator",
            "operation_id": operation_id,
        }
        changed, state = domain.apply_ordinary_field_change(
            stable, target, "Updated Operator"
        )
        self.assertEqual(state["operation_number"], 1)
        self.assertEqual(changed["cnc_machining"]["operations"][0]["operator"], "Updated Operator")

    def test_protected_and_invalid_domain_values_are_rejected(self):
        current = read_fixture(CURRENT_FIXTURE)
        protected = {
            "section": "programming",
            "field": "status",
            "compatibility_reference": "programming:operation:1",
        }
        with self.assertRaisesRegex(domain.TravelerValidationError, "protected"):
            domain.ordinary_field_state(current, protected)

        parts_count_owned = {
            "section": "cnc_machining",
            "field": "qty_complete",
            "compatibility_reference": "cnc_machining:operation:1",
        }
        with self.assertRaisesRegex(domain.TravelerValidationError, "protected"):
            domain.ordinary_field_state(current, parts_count_owned)

        machine = {
            "section": "cnc_machining",
            "field": "machine",
            "compatibility_reference": "cnc_machining:operation:1",
        }
        with self.assertRaisesRegex(domain.TravelerValidationError, "operation type"):
            domain.apply_ordinary_field_change(current, machine, "Haas ST15Y")

    def test_future_closure_marker_is_read_only_compatible(self):
        current = read_fixture(CURRENT_FIXTURE)
        current["_shopos"] = {"closure_state": "closed"}
        self.assertTrue(domain.is_authoritatively_closed(current))
        self.assertTrue(domain.read_model(current)["authoritatively_closed"])
        self.assertEqual(domain.document_revision(current), 0)

    def test_desktop_canonical_save_and_plan_resize_preserve_server_metadata(self):
        stable = self._stable(read_fixture(CURRENT_FIXTURE))
        stable = domain.confirm_mutation_metadata(
            stable,
            prior_revision=0,
            mutation_id=str(uuid.UUID(int=777)),
        )
        stable["_shopos"]["compatible_private_extension"] = {
            "unknown": {"preserve": True}
        }
        metadata = copy.deepcopy(stable["_shopos"])

        resized = domain.resize_operation_plan(stable, 3)
        self.assertEqual(resized["_shopos"], metadata)
        self.assertEqual(domain.canonical_job(stable)["_shopos"], metadata)

        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory).resolve()
            destination = jobs / "SANITIZED-MULTI.json"
            gui.save_job_to_path(
                stable, destination, jobs_directory=jobs, overwrite=True
            )
            persisted = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(persisted["_shopos"], metadata)
        self.assertEqual(domain.document_revision(persisted), 1)

    def test_contract_version_is_deliberately_version_two(self):
        self.assertEqual(domain.DOMAIN_CONTRACT_VERSION, 2)


if __name__ == "__main__":
    unittest.main()
