"""Multi-operation terminal and GUI business-workflow tests."""

import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import job_traveler as terminal_app
import job_traveler_gui as gui


class MultiOperationTravelerTests(unittest.TestCase):
    def setUp(self):
        self.repository = Path(__file__).resolve().parent
        self.header = {
            "job_number": "MULTI-OP-001",
            "customer": "Test Customer",
            "part_number": "TEST-MULTI",
            "description": "Multi-operation test",
            "qty_to_make": "20",
            "material": "Steel",
            "cut_length": "12",
        }

    def new_job(self):
        return gui.create_job_record(self.header)

    def programmed_job(self, count=2):
        job = self.new_job()
        rows = [
            {
                "operation_type": "Mill" if number == 1 else "Turning",
                "program_name": f"OP{number}",
                "revision": "A",
                "status": "Completed",
                "notes": "",
            }
            for number in range(1, count + 1)
        ]
        gui.apply_programming_update(
            job,
            {"programmer": "Jon", "operation_count": str(count), "operations": rows},
            timestamp="2026-07-15 10:00",
        )
        return job

    def cnc_update(self, job, number, machine, quantity, operator="Jon"):
        return gui.apply_cnc_update(
            job,
            {
                "operation_number": str(number),
                "operator": operator,
                "machine": machine,
                "qty_complete": str(quantity),
                "notes": f"CNC op {number}",
            },
            timestamp=f"2026-07-15 11:0{number}",
        )

    def test_one_operation_job_uses_normalized_lists(self):
        normalized = gui.normalized_job(self.new_job())
        self.assertEqual(normalized["programming"]["operation_count"], 1)
        self.assertEqual(normalized["programming"]["operations"][0]["operation_number"], 1)
        self.assertEqual(normalized["cnc_machining"]["operations"][0]["operation_number"], 1)

    def test_two_operations_are_sequential_mill_then_turning(self):
        job = self.programmed_job()
        operations = job["programming"]["operations"]
        self.assertEqual([row["operation_number"] for row in operations], [1, 2])
        self.assertEqual([row["operation_type"] for row in operations], ["Mill", "Turning"])
        self.assertNotIn("machine", job["programming"])
        self.assertTrue(all("machine" not in row for row in operations))

    def test_programming_terminal_never_prompts_for_machine(self):
        job = self.new_job()
        prompts = []
        answers = iter(["Jon", "", "1", "OP1-MILL", "A", "1", ""])

        def answer(prompt):
            prompts.append(prompt)
            return next(answers)

        with mock.patch("builtins.input", side_effect=answer), mock.patch.object(
            terminal_app, "save_job"
        ), redirect_stdout(io.StringIO()):
            terminal_app.update_programming(job)
        self.assertFalse(any("machine" in prompt.casefold() for prompt in prompts))

    def test_cnc_terminal_prompts_for_one_machine_for_selected_operation(self):
        job = self.programmed_job()
        prompts = []
        answers = iter(["2", "Mike", "1", "10", "Turning notes"])

        def answer(prompt):
            prompts.append(prompt)
            return next(answers)

        with mock.patch("builtins.input", side_effect=answer), mock.patch.object(
            terminal_app, "save_job"
        ), redirect_stdout(io.StringIO()):
            terminal_app.update_cnc_machining(job)
        machine_prompts = [prompt for prompt in prompts if "choose a machine" in prompt.casefold()]
        self.assertEqual(len(machine_prompts), 1)
        self.assertEqual(
            job["cnc_machining"]["operations"][1]["machine"],
            terminal_app.TURNING_MACHINES[0],
        )

    def test_official_machine_choices_are_filtered_by_operation(self):
        self.assertEqual(gui.machines_for_operation("Mill"), tuple(terminal_app.MILLING_MACHINES))
        self.assertEqual(
            gui.machines_for_operation("Turning"),
            tuple(terminal_app.TURNING_MACHINES),
        )
        self.assertIn("DNM 4500", gui.machines_for_operation("Mill"))
        self.assertIn("Lynx 2100LSY #1", gui.machines_for_operation("Turning"))

    def test_inspection_derives_type_and_machine_without_machine_prompt(self):
        job = self.programmed_job()
        self.cnc_update(job, 2, terminal_app.TURNING_MACHINES[0], 10, "Mike")
        prompts = []
        answers = iter(["2", "Ivy", "3", "n", "Looks good"])

        def answer(prompt):
            prompts.append(prompt)
            return next(answers)

        with mock.patch("builtins.input", side_effect=answer), mock.patch.object(
            terminal_app, "save_job"
        ), redirect_stdout(io.StringIO()):
            terminal_app.update_inspection(job)
        self.assertFalse(any("machine" in prompt.casefold() for prompt in prompts))
        record = job["inspection"]["records"][0]
        self.assertEqual(record["operation_number"], 2)
        self.assertEqual(record["operation_type"], "Turning")
        self.assertEqual(record["machine"], terminal_app.TURNING_MACHINES[0])

    def test_inspection_refuses_operation_without_cnc_machine(self):
        job = self.programmed_job()
        before = copy.deepcopy(job)
        output = io.StringIO()
        with mock.patch("builtins.input", return_value="2"), mock.patch.object(
            terminal_app, "save_job"
        ) as save, redirect_stdout(output):
            self.assertFalse(terminal_app.update_inspection(job))
        save.assert_not_called()
        self.assertEqual(job, before)
        self.assertIn("Assign the machine in CNC Machining first", output.getvalue())
        with self.assertRaisesRegex(gui.ValidationError, "Assign the machine"):
            gui.apply_inspection_update(
                job,
                {"operation_number": "2", "inspector": "Ivy", "status": "Pending"},
            )

    def test_cnc_first_article_is_absent_and_legacy_value_is_ignored(self):
        job = self.programmed_job()
        job["cnc_machining"]["first_article"] = "legacy"
        updated = self.cnc_update(job, 1, terminal_app.MILLING_MACHINES[2], 20)
        self.assertNotIn("first_article", updated)
        self.assertNotIn("first_article", job["cnc_machining"])

    def test_printed_traveler_has_operation_tables_and_no_first_article_row(self):
        job = self.programmed_job()
        self.cnc_update(job, 1, terminal_app.MILLING_MACHINES[2], 20)
        self.cnc_update(job, 2, terminal_app.TURNING_MACHINES[0], 10)
        text = gui.traveler_text(job)
        self.assertIn("OP | TYPE | PROGRAM", text)
        self.assertIn("OP | TYPE | OPERATOR | MACHINE", text)
        self.assertNotIn("First Article:", text)

    def test_cnc_status_is_derived_per_operation(self):
        job = self.programmed_job()
        first = self.cnc_update(job, 1, terminal_app.MILLING_MACHINES[2], 20)
        second = self.cnc_update(job, 2, terminal_app.TURNING_MACHINES[0], 10)
        self.assertEqual(first["status"], "Completed")
        self.assertEqual(second["status"], "In Progress")

    def test_operations_preserve_separate_cnc_records(self):
        job = self.programmed_job()
        self.cnc_update(job, 1, terminal_app.MILLING_MACHINES[2], 20, "Jon")
        self.cnc_update(job, 2, terminal_app.TURNING_MACHINES[0], 10, "Mike")
        rows = job["cnc_machining"]["operations"]
        self.assertEqual((rows[0]["operator"], rows[0]["qty_complete"]), ("Jon", 20))
        self.assertEqual((rows[1]["operator"], rows[1]["qty_complete"]), ("Mike", 10))

    def test_increasing_operation_count_preserves_all_records(self):
        job = self.programmed_job()
        self.cnc_update(job, 1, terminal_app.MILLING_MACHINES[2], 20)
        grown = terminal_app.resize_operation_plan(job, 3)
        self.assertEqual(
            grown["cnc_machining"]["operations"][0]["machine"],
            terminal_app.MILLING_MACHINES[2],
        )
        self.assertEqual(grown["programming"]["operation_count"], 3)
        self.assertEqual(grown["programming"]["operations"][2]["operation_number"], 3)

    def test_unsafe_operation_reduction_is_refused(self):
        job = self.programmed_job()
        self.cnc_update(job, 2, terminal_app.TURNING_MACHINES[0], 1)
        with self.assertRaisesRegex(ValueError, "production or inspection data"):
            terminal_app.resize_operation_plan(job, 1, True)

    def test_safe_blank_operation_removal_requires_confirmation(self):
        job = self.programmed_job()
        with self.assertRaisesRegex(ValueError, "Confirm removal"):
            terminal_app.resize_operation_plan(job, 1)
        reduced = terminal_app.resize_operation_plan(job, 1, True)
        self.assertEqual(reduced["programming"]["operation_count"], 1)
        self.assertEqual(len(reduced["cnc_machining"]["operations"]), 1)

    def test_job_316_style_legacy_json_normalizes_as_operation_one(self):
        legacy = json.loads((self.repository / "jobs" / "316.json").read_text())
        normalized = gui.normalized_job(legacy)
        self.assertEqual(normalized["programming"]["operations"][0]["operation_number"], 1)
        self.assertEqual(normalized["programming"]["operations"][0]["operation_type"], "Mill")
        cnc = normalized["cnc_machining"]["operations"][0]
        self.assertEqual(cnc["machine"], "DNM 4500")
        self.assertEqual(cnc["qty_complete"], 20)

    def test_legacy_programming_machine_is_only_fallback_when_cnc_blank(self):
        legacy = {
            "job_number": "LEGACY",
            "qty_to_make": 5,
            "programming": {"machine": terminal_app.MILLING_MACHINES[0]},
            "cnc_machining": {"machine": ""},
        }
        normalized = gui.normalized_job(legacy)
        self.assertEqual(
            normalized["cnc_machining"]["operations"][0]["machine"],
            terminal_app.MILLING_MACHINES[0],
        )
        legacy["cnc_machining"]["machine"] = terminal_app.MILLING_MACHINES[1]
        normalized = gui.normalized_job(legacy)
        self.assertEqual(
            normalized["cnc_machining"]["operations"][0]["machine"],
            terminal_app.MILLING_MACHINES[1],
        )

    def test_existing_inspection_data_continues_printing(self):
        legacy = json.loads((self.repository / "jobs" / "316.json").read_text())
        text = gui.traveler_text(legacy)
        self.assertIn("Operation Number: 1", text)
        self.assertIn("Caliper", text)
        self.assertIn("5 | .005 | 5", text)

    def test_gui_update_helpers_target_selected_operation(self):
        job = self.programmed_job()
        self.cnc_update(job, 2, terminal_app.TURNING_MACHINES[1], 4, "Selected")
        self.assertEqual(job["cnc_machining"]["operations"][0]["operator"], "")
        self.assertEqual(job["cnc_machining"]["operations"][1]["operator"], "Selected")
        record = gui.apply_inspection_update(
            job,
            {
                "operation_number": "2",
                "inspector": "Ivy",
                "report_type": "In Process",
                "status": "Completed",
                "notes": "Pass",
            },
        )
        self.assertEqual(record["operation_number"], 2)
        self.assertEqual(record["machine"], terminal_app.TURNING_MACHINES[1])

    def test_dimensions_are_linked_to_selected_inspection_operation(self):
        job = self.programmed_job()
        self.cnc_update(job, 1, terminal_app.MILLING_MACHINES[2], 20)
        gui.apply_inspection_update(
            job,
            {"operation_number": "1", "inspector": "Ivy", "status": "Completed"},
        )
        dimension = gui.build_dimension(
            {
                "target_dimension": "1.000",
                "tolerance": "+/- .005",
                "finding": "1.001",
                "measurement_equipment_used": "Micrometer",
                "result": "Pass",
            },
            1,
        )
        gui.apply_dimensions_update(job, [dimension], 1)
        self.assertEqual(job["inspection"]["records"][0]["operation_number"], 1)
        self.assertEqual(job["inspection"]["records"][0]["dimensions"][0]["result"], "Pass")

    def test_unknown_fields_survive_normalization_and_updates(self):
        job = self.programmed_job()
        job["custom_metadata"] = {"keep": True}
        job["cnc_machining"]["private_key"] = "keep"
        self.cnc_update(job, 1, terminal_app.MILLING_MACHINES[0], 2)
        self.assertEqual(job["custom_metadata"], {"keep": True})
        self.assertEqual(job["cnc_machining"]["private_key"], "keep")

    def test_importing_modules_creates_no_window_or_prompt(self):
        result = subprocess.run(
            [sys.executable, "-c", "import job_traveler, job_traveler_gui; print('safe')"],
            cwd=self.repository,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "safe")

    def test_save_and_reopen_normalized_job(self):
        job = self.programmed_job()
        self.cnc_update(job, 1, terminal_app.MILLING_MACHINES[0], 20)
        with tempfile.TemporaryDirectory(dir=self.repository) as directory:
            path = gui.save_job_data(job, directory, overwrite=False)
            reopened = gui.load_job_path(path)
        self.assertEqual(reopened["programming"]["operation_count"], 2)
        self.assertNotIn("first_article", reopened["cnc_machining"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
