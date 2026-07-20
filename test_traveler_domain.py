"""Pure domain, sanitized fixture, and desktop compatibility contract tests."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

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
        self.assertEqual(set(model["normalized"]).intersection(domain.SECTIONS), set(domain.SECTIONS))
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


if __name__ == "__main__":
    unittest.main()
