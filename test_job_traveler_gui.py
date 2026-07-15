"""Compatibility and workflow tests for the native Job Traveler GUI."""

import copy
import io
import tempfile
import tkinter as tk
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tkinter import ttk
from unittest import mock

import job_traveler as terminal_app
import job_traveler_gui as gui


class JobTravelerGuiTests(unittest.TestCase):
    """Exercise GUI business helpers without touching the real jobs folder."""

    def setUp(self):
        self.repository = Path(__file__).resolve().parent
        self.temporary = tempfile.TemporaryDirectory(
            prefix=".job_traveler_gui_test_", dir=self.repository
        )
        self.jobs_directory = Path(self.temporary.name) / "jobs"
        self.assertNotEqual(self.jobs_directory.resolve(), (self.repository / "jobs").resolve())
        self.valid_fields = {
            "job_number": "GUI-1001",
            "customer": "Example Customer",
            "part_number": "PART-42",
            "description": "Compatibility fixture",
            "qty_to_make": "10",
            "material": "6061-T6",
            "cut_length": "2.500",
        }

    def tearDown(self):
        self.temporary.cleanup()

    def new_job(self):
        return gui.create_job_record(self.valid_fields)

    def test_create_reopen_and_terminal_compatibility(self):
        job = self.new_job()
        path = gui.save_job_data(job, self.jobs_directory, overwrite=False)

        self.assertEqual(path, self.jobs_directory / "GUI-1001.json")
        self.assertIsInstance(job["qty_to_make"], int)
        self.assertEqual(set(terminal_app.SECTIONS), {key for key in job if key in terminal_app.SECTIONS})
        self.assertTrue(all(isinstance(job[section], dict) for section in terminal_app.SECTIONS))

        reopened = gui.load_job_data("GUI-1001", self.jobs_directory)
        self.assertEqual(reopened, job)

        with mock.patch.object(terminal_app, "JOBS_FOLDER", str(self.jobs_directory)):
            tui_loaded = terminal_app.load_job("GUI-1001")
        self.assertEqual(tui_loaded, job)

        output = io.StringIO()
        with redirect_stdout(output):
            terminal_app.print_traveler(tui_loaded)
        self.assertIn("GUI-1001", output.getvalue())
        self.assertIn("Example Customer", output.getvalue())

    def test_non_ascii_json_remains_safe_for_terminal_default_encoding(self):
        job = self.new_job()
        job["customer"] = "José Manufacturing"
        path = gui.save_job_data(job, self.jobs_directory, overwrite=False)

        self.assertIn(b"Jos\\u00e9 Manufacturing", path.read_bytes())
        with mock.patch.object(terminal_app, "JOBS_FOLDER", str(self.jobs_directory)):
            reopened = terminal_app.load_job(job["job_number"])
        self.assertEqual(reopened["customer"], "José Manufacturing")

    def test_existing_job_is_not_overwritten_without_permission(self):
        original = self.new_job()
        path = gui.save_job_data(original, self.jobs_directory, overwrite=False)
        original_bytes = path.read_bytes()

        changed = copy.deepcopy(original)
        changed["customer"] = "Replacement"
        with self.assertRaises(FileExistsError):
            gui.save_job_data(changed, self.jobs_directory, overwrite=False)

        self.assertEqual(path.read_bytes(), original_bytes)

    def test_quantity_and_required_field_validation(self):
        for invalid in ("", "0", "-1", "1.5", "ten"):
            with self.subTest(quantity=invalid):
                values = dict(self.valid_fields, qty_to_make=invalid)
                with self.assertRaises(gui.ValidationError):
                    gui.create_job_record(values)

        for field in (
            "job_number",
            "customer",
            "part_number",
            "description",
            "material",
            "cut_length",
        ):
            with self.subTest(field=field):
                values = dict(self.valid_fields, **{field: "  "})
                with self.assertRaises(gui.ValidationError):
                    gui.create_job_record(values)

        accepted = gui.create_job_record(dict(self.valid_fields, qty_to_make=" 12 "))
        self.assertEqual(accepted["qty_to_make"], 12)

    def test_job_number_cannot_escape_jobs_directory(self):
        for invalid in ("../escape", r"folder\escape", "CON", "bad:name", "trailing."):
            with self.subTest(job_number=invalid):
                values = dict(self.valid_fields, job_number=invalid)
                with self.assertRaises(gui.ValidationError):
                    gui.create_job_record(values)

    def test_multiple_section_updates_preserve_unknown_and_private_fields(self):
        job = self.new_job()
        job["programming"] = {"legacy_key": {"keep": True}}
        job["saw_cutting"] = {"private_note": "keep me", "scrap_qty": 7}

        gui.apply_programming_update(
            job,
            {
                "programmer": "Ada",
                "program_name": "OP10",
                "revision": "B",
                "operation": "Mill",
                "machine": terminal_app.MILLING_MACHINES[0],
                "status": "Completed",
                "notes": "Released",
            },
            timestamp="2026-07-13 10:00",
        )
        gui.apply_standard_section_update(
            job,
            "saw_cutting",
            {
                "employee": "Sam",
                "qty_cut": "10",
                "cut_length": "2.500",
                "scrap_qty": "1",
                "status": "Completed",
                "notes": "Done",
            },
            timestamp="2026-07-13 10:01",
        )
        gui.apply_standard_section_update(
            job,
            "deburr",
            {
                "employee": "Dee",
                "deburr_needed": "Yes",
                "qty_deburred": "5",
                "status": "In Progress",
                "notes": "Halfway",
            },
            timestamp="2026-07-13 10:02",
        )

        self.assertEqual(job["programming"]["legacy_key"], {"keep": True})
        self.assertEqual(job["programming"]["operation"], "Mill")
        self.assertEqual(job["saw_cutting"]["private_note"], "keep me")
        self.assertEqual(job["saw_cutting"]["scrap_qty"], 1)
        self.assertEqual(job["deburr"]["last_updated"], "2026-07-13 10:02")

    def test_invalid_section_number_does_not_partially_update(self):
        job = self.new_job()
        job["packing"] = {"unknown": "preserve", "qty_packed": 3}
        before = copy.deepcopy(job)

        with self.assertRaises(gui.ValidationError):
            gui.apply_standard_section_update(
                job,
                "packing",
                {
                    "employee": "Pat",
                    "qty_packed": "not a number",
                    "box_count": "2",
                    "status": "In Progress",
                    "notes": "",
                },
            )

        self.assertEqual(job, before)

    def test_official_machine_lists_and_cnc_inheritance(self):
        self.assertEqual(gui.machines_for_operation("Mill"), tuple(terminal_app.MILLING_MACHINES))
        self.assertEqual(gui.machines_for_operation("Turning"), tuple(terminal_app.TURNING_MACHINES))

        job = self.new_job()
        job["programming"] = {"machine": terminal_app.MILLING_MACHINES[1]}
        job["cnc_machining"] = {
            "machine": terminal_app.TURNING_MACHINES[0],
            "qty_rejected": 4,
            "private_key": "keep",
        }
        updated = gui.apply_cnc_update(
            job,
            {
                "operator": "Casey",
                "machine": terminal_app.TURNING_MACHINES[2],
                "qty_completed": "5",
            },
            timestamp="2026-07-13 11:00",
        )
        self.assertEqual(updated["machine"], terminal_app.MILLING_MACHINES[1])
        self.assertEqual(updated["status"], "In Progress")
        self.assertEqual(updated["qty_rejected"], 4)
        self.assertEqual(updated["private_key"], "keep")

        completed = gui.apply_cnc_update(
            job,
            {"operator": "Casey", "machine": "ignored", "qty_completed": "10"},
        )
        self.assertEqual(completed["status"], "Completed")

        del job["programming"]["machine"]
        self.assertEqual(gui.resolve_cnc_machine(job), terminal_app.MILLING_MACHINES[1])

    def test_programming_operation_machine_pair_and_tui_update_compatibility(self):
        self.assertEqual(
            gui.operation_for_machine(terminal_app.MILLING_MACHINES[0]), "Mill"
        )
        self.assertEqual(
            gui.operation_for_machine(terminal_app.TURNING_MACHINES[0]), "Turning"
        )

        job = self.new_job()
        with self.assertRaises(gui.ValidationError):
            gui.apply_programming_update(
                job,
                {
                    "programmer": "Ada",
                    "program_name": "OP10",
                    "revision": "A",
                    "operation": "Mill",
                    "machine": terminal_app.TURNING_MACHINES[0],
                    "status": "Pending",
                    "notes": "",
                },
            )

        gui.apply_programming_update(
            job,
            {
                "programmer": "Ada",
                "program_name": "OP10",
                "revision": "A",
                "operation": "Turning",
                "machine": terminal_app.TURNING_MACHINES[0],
                "status": "In Progress",
                "notes": "Ready",
            },
        )
        self.assertIn("Operation:     Turning", gui.traveler_text(job))

        with (
            redirect_stdout(io.StringIO()),
            mock.patch.object(
                terminal_app,
                "get_existing_or_new",
                side_effect=lambda section, key, _prompt: section.get(key, ""),
            ),
            mock.patch.object(
                terminal_app,
                "get_existing_machine_or_new",
                return_value=terminal_app.TURNING_MACHINES[0],
            ),
            mock.patch.object(terminal_app, "get_status", return_value="Completed"),
            mock.patch.object(terminal_app, "get_timestamp", return_value="2026-07-13 13:00"),
            mock.patch.object(terminal_app, "save_job"),
        ):
            terminal_app.update_programming(job)

        self.assertEqual(job["programming"]["operation"], "Turning")
        self.assertIn("Operation:     Turning", gui.traveler_text(job))

        with (
            redirect_stdout(io.StringIO()),
            mock.patch.object(
                terminal_app,
                "get_existing_or_new",
                side_effect=lambda section, key, _prompt: section.get(key, ""),
            ),
            mock.patch.object(
                terminal_app,
                "get_existing_machine_or_new",
                return_value=terminal_app.MILLING_MACHINES[0],
            ),
            mock.patch.object(terminal_app, "get_status", return_value="Completed"),
            mock.patch.object(terminal_app, "get_timestamp", return_value="2026-07-13 13:01"),
            mock.patch.object(terminal_app, "save_job"),
        ):
            terminal_app.update_programming(job)

        self.assertEqual(job["programming"]["operation"], "Mill")
        self.assertEqual(job["programming"]["machine"], terminal_app.MILLING_MACHINES[0])

    def test_cnc_status_zero_and_legacy_quantity(self):
        job = self.new_job()
        job["cnc_machining"] = {"status": "In Progress", "machine": terminal_app.ALL_MACHINES[0]}
        updated = gui.apply_cnc_update(
            job, {"operator": "Op", "machine": "", "qty_completed": "0"}
        )
        self.assertEqual(updated["status"], "In Progress")

        job["qty_to_make"] = "legacy-invalid"
        updated = gui.apply_cnc_update(
            job, {"operator": "Op", "machine": "", "qty_completed": "1"}
        )
        self.assertEqual(updated["status"], "In Progress")

    def test_first_article_dimensions_and_legacy_equipment_round_trip(self):
        job = self.new_job()
        job["inspection"] = {
            "unknown": "preserve",
            "dimensions": [
                {
                    "dimension_number": 1,
                    "target_dimension": "1.000",
                    "tolerance": "+/- .005",
                    "finding": "1.001",
                    "tool_used": "Micrometer",
                    "result": "Pass",
                }
            ],
        }
        gui.apply_inspection_update(
            job,
            {
                "inspector": "Ivy",
                "operation": "Turning",
                "machine": terminal_app.TURNING_MACHINES[0],
                "status": "In Progress",
                "notes": "FAI",
            },
            timestamp="2026-07-13 12:00",
        )
        dimensions = copy.deepcopy(job["inspection"]["dimensions"])
        dimensions.append(
            gui.build_dimension(
                {
                    "target_dimension": "2.000",
                    "tolerance": "+/- .010",
                    "finding": "2.002",
                    "measurement_equipment_used": "Caliper",
                    "result": "Pass",
                },
                gui.next_dimension_number(dimensions),
            )
        )
        gui.apply_dimensions_update(job, dimensions, timestamp="2026-07-13 12:01")
        gui.save_job_data(job, self.jobs_directory)
        reopened = gui.load_job_data(job["job_number"], self.jobs_directory)

        self.assertEqual(reopened["inspection"]["unknown"], "preserve")
        self.assertEqual(reopened["inspection"]["dimensions"][0]["tool_used"], "Micrometer")
        self.assertEqual(reopened["inspection"]["dimensions"][1]["dimension_number"], 2)
        self.assertEqual(
            reopened["inspection"]["dimensions"][1]["measurement_equipment_used"],
            "Caliper",
        )
        self.assertNotIn("first_article_inspection", reopened)
        self.assertIn("Micrometer", gui.traveler_text(reopened))

    def test_public_preview_hides_private_counts(self):
        job = self.new_job()
        job["saw_cutting"] = {"scrap_qty": 99}
        job["cnc_machining"] = {"qty_rejected": 88}
        preview = gui.traveler_text(job)
        self.assertNotIn("scrap_qty", preview)
        self.assertNotIn("qty_rejected", preview)
        self.assertNotIn("99", preview)
        self.assertNotIn("88", preview)

    def test_legacy_missing_sections_are_normalized_without_losing_top_level_data(self):
        legacy = {
            "job_number": "LEGACY-1",
            "customer": "Legacy",
            "qty_to_make": "25",
            "custom_metadata": {"keep": True},
            "inspection": {"dimensions": "legacy malformed value"},
        }
        normalized = gui.normalized_job(legacy)
        self.assertEqual(normalized["custom_metadata"], {"keep": True})
        self.assertTrue(all(isinstance(normalized[name], dict) for name in terminal_app.SECTIONS))
        self.assertEqual(normalized["inspection"]["dimensions"], "legacy malformed value")

        gui.save_job_data(normalized, self.jobs_directory)
        reopened = gui.load_job_data("LEGACY-1", self.jobs_directory)
        self.assertEqual(reopened["custom_metadata"], {"keep": True})

    def test_malformed_section_and_dimensions_are_never_silently_erased(self):
        malformed_section = self.new_job()
        malformed_section["packing"] = ["unexpected legacy value"]
        with self.assertRaises(gui.ValidationError):
            gui.save_job_data(malformed_section, self.jobs_directory)

        malformed_dimensions = self.new_job()
        malformed_dimensions["inspection"] = {
            "dimensions": "unexpected legacy value",
            "unknown": "preserve",
        }
        before = copy.deepcopy(malformed_dimensions)
        with self.assertRaises(gui.ValidationError):
            gui.apply_inspection_update(
                malformed_dimensions,
                {
                    "inspector": "Ivy",
                    "operation": "Mill",
                    "machine": terminal_app.MILLING_MACHINES[0],
                    "status": "Pending",
                    "notes": "",
                },
            )
        self.assertEqual(malformed_dimensions, before)

    def test_opened_filename_identity_is_preserved_and_collision_is_avoided(self):
        opened_job = self.new_job()
        opened_job["job_number"] = "EMBEDDED-B"
        opened_path = self.jobs_directory / "OPENED-A.json"
        gui.save_job_to_path(opened_job, opened_path, self.jobs_directory, overwrite=False)

        other_job = self.new_job()
        other_job["job_number"] = "EMBEDDED-B"
        other_job["customer"] = "Do not overwrite"
        other_path = gui.save_job_data(other_job, self.jobs_directory, overwrite=False)
        other_bytes = other_path.read_bytes()

        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.JobTravelerApp(root, self.jobs_directory)
            app.current_job = gui.load_job_path(opened_path)
            app.current_path = opened_path
            candidate = copy.deepcopy(app.current_job)
            candidate["customer"] = "Updated opened file"
            with mock.patch.object(gui.messagebox, "showinfo"):
                self.assertTrue(app._persist_candidate(candidate))
        finally:
            root.destroy()

        self.assertEqual(
            gui.load_job_path(opened_path)["customer"], "Updated opened file"
        )
        self.assertEqual(other_path.read_bytes(), other_bytes)

    def test_save_failure_does_not_adopt_unsaved_candidate(self):
        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.JobTravelerApp(root, self.jobs_directory)
            app.current_job = self.new_job()
            app.current_path = self.jobs_directory / "GUI-1001.json"
            original = copy.deepcopy(app.current_job)
            candidate = copy.deepcopy(original)
            candidate["customer"] = "Unsaved change"
            with (
                mock.patch.object(gui, "save_job_to_path", side_effect=OSError("disk full")),
                mock.patch.object(gui.messagebox, "showerror") as show_error,
            ):
                self.assertFalse(app._persist_candidate(candidate))
            self.assertEqual(app.current_job, original)
            show_error.assert_called_once()
        finally:
            root.destroy()

    def test_withdrawn_gui_home_and_navigation_smoke(self):
        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.JobTravelerApp(root, self.jobs_directory)
            root.update_idletasks()
            self.assertEqual(root.title(), gui.APP_TITLE)
            app.show_create_job()
            root.update_idletasks()
            app.show_home()
            root.update_idletasks()
            app.current_job = self.new_job()
            app.show_job_detail()
            root.update_idletasks()
            app.show_programming()
            root.update_idletasks()
            app.show_standard_section("saw_cutting")
            root.update_idletasks()
            app.show_standard_section("deburr")
            root.update_idletasks()
            app.show_cnc_machining()
            root.update_idletasks()
            app.show_inspection()
            root.update_idletasks()
            app.show_first_article()
            root.update_idletasks()
            app.show_standard_section("packing")
            root.update_idletasks()
            app.show_standard_section("shipping")
            root.update_idletasks()
            app.show_traveler_preview()
            root.update_idletasks()
        finally:
            root.destroy()

    def test_legacy_programming_machine_infers_operation_in_form(self):
        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.JobTravelerApp(root, self.jobs_directory)
            app.current_job = self.new_job()
            app.current_job["programming"] = {
                "machine": terminal_app.TURNING_MACHINES[1],
                "status": "Pending",
            }
            app.show_programming()
            root.update_idletasks()

            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)

            combos = [
                widget
                for widget in descendants(root)
                if isinstance(widget, ttk.Combobox)
            ]
            self.assertEqual(combos[0].get(), "Turning")
            self.assertEqual(combos[1].get(), terminal_app.TURNING_MACHINES[1])
            self.assertEqual(tuple(combos[1].cget("values")), tuple(terminal_app.TURNING_MACHINES))

            app.current_job["programming"]["operation"] = "LegacyOp"
            app.show_programming()
            root.update_idletasks()
            combos = [
                widget
                for widget in descendants(root)
                if isinstance(widget, ttk.Combobox)
            ]
            self.assertEqual(combos[0].get(), "LegacyOp")
            self.assertEqual(combos[1].get(), terminal_app.TURNING_MACHINES[1])
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
