"""Native Tkinter interface for the Job Traveler workflow.

The GUI deliberately keeps the JSON contract owned by ``job_traveler.py``.
Business helpers in this module do not call terminal ``input()`` functions, so
they can also be exercised safely by automated tests.
"""

from __future__ import annotations

import copy
import io
import json
import os
import re
import tempfile
import tkinter as tk
from contextlib import redirect_stdout
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

import job_traveler as terminal_app


APP_TITLE = "JOB TRAVELER"
BASE_DIR = Path(__file__).resolve().parent
HEADER_FIELDS = (
    "job_number",
    "customer",
    "part_number",
    "description",
    "qty_to_make",
    "material",
    "cut_length",
)
INVALID_JOB_NUMBER = re.compile(r'[<>:"/\\|?*]')
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class ValidationError(ValueError):
    """Raised when operator-entered data cannot be saved safely."""


def get_jobs_directory(jobs_directory=None):
    """Return the configured traveler directory as an absolute Path."""
    if jobs_directory is not None:
        return Path(jobs_directory).resolve()

    override = os.environ.get("JOB_TRAVELER_JOBS_FOLDER")
    if override:
        return Path(override).resolve()

    return (BASE_DIR / terminal_app.JOBS_FOLDER).resolve()


def clean_text(value):
    """Return stripped text without turning None into the word 'None'."""
    if value is None:
        return ""
    return str(value).strip()


def validate_job_number(value):
    """Validate a job number before it becomes part of a Windows filename."""
    job_number = clean_text(value)
    if not job_number:
        raise ValidationError("Job Number is required.")
    if job_number in {".", ".."} or INVALID_JOB_NUMBER.search(job_number):
        raise ValidationError(
            "Job Number cannot contain Windows filename characters: "
            '< > : " / \\ | ? *'
        )
    if job_number.endswith((" ", ".")):
        raise ValidationError("Job Number cannot end with a space or period.")
    if job_number.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        raise ValidationError("Job Number is a reserved Windows filename.")
    return job_number


def parse_positive_integer(value, field_name):
    """Return a positive whole number or raise ValidationError."""
    text = clean_text(value)
    try:
        number = int(text)
    except (TypeError, ValueError):
        raise ValidationError(f"{field_name} must be a positive whole number.")
    if number <= 0 or str(number) != text.lstrip("+"):
        raise ValidationError(f"{field_name} must be a positive whole number.")
    return number


def parse_nonnegative_integer(value, field_name):
    """Return a whole number of zero or greater or raise ValidationError."""
    text = clean_text(value)
    try:
        number = int(text)
    except (TypeError, ValueError):
        raise ValidationError(f"{field_name} must be a whole number of zero or greater.")
    if number < 0 or str(number) != text.lstrip("+"):
        raise ValidationError(f"{field_name} must be a whole number of zero or greater.")
    return number


def require_choice(value, field_name, allowed_values):
    """Validate a value selected from a controlled list."""
    choice = clean_text(value)
    if choice not in allowed_values:
        raise ValidationError(
            f"{field_name} must be one of: {', '.join(allowed_values)}."
        )
    return choice


def normalized_job(job):
    """Return a normalized deep copy with legacy jobs mapped to Operation 1."""
    if not isinstance(job, dict):
        raise ValidationError("The traveler file must contain a JSON object.")

    normalized = copy.deepcopy(job)
    for section in terminal_app.SECTIONS:
        if section not in normalized:
            normalized[section] = {}
        elif not isinstance(normalized[section], dict):
            raise ValidationError(
                f"Traveler section '{section}' must contain a JSON object."
            )
    return terminal_app.normalize_operations(normalized)


def create_job_record(values):
    """Build the canonical top-level traveler structure from GUI fields."""
    cleaned = {field: clean_text(values.get(field)) for field in HEADER_FIELDS}
    cleaned["job_number"] = validate_job_number(cleaned["job_number"])

    labels = {
        "customer": "Customer",
        "part_number": "Part Number",
        "description": "Description",
        "material": "Material",
        "cut_length": "Cut Length",
    }
    for field, label in labels.items():
        if not cleaned[field]:
            raise ValidationError(f"{label} is required.")

    cleaned["qty_to_make"] = parse_positive_integer(
        cleaned["qty_to_make"], "Quantity to Make"
    )
    for section in terminal_app.SECTIONS:
        cleaned[section] = {}
    return cleaned


def traveler_path(job_number, jobs_directory=None):
    """Return the validated JSON path for a job number."""
    safe_number = validate_job_number(job_number)
    return get_jobs_directory(jobs_directory) / f"{safe_number}.json"


def save_job_to_path(job, destination, jobs_directory=None, overwrite=True):
    """Atomically save a traveler to an existing in-scope JSON path."""
    data = terminal_app.canonical_job(normalized_job(job))
    directory = get_jobs_directory(jobs_directory)
    destination = Path(destination).resolve()
    if destination.parent != directory or destination.suffix.casefold() != ".json":
        raise ValidationError("Traveler destination must be a JSON file in the jobs folder.")
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_name = Path(temporary_file.name)
            # Match the TUI's ASCII-safe JSON output so its default-encoding
            # reader remains compatible on every supported Windows locale.
            json.dump(data, temporary_file, indent=4, ensure_ascii=True)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_name, destination)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                temporary_name.unlink()
            except OSError:
                pass

    return destination


def save_job_data(job, jobs_directory=None, overwrite=True):
    """Atomically save a traveler under its validated job-number filename."""
    destination = traveler_path(job.get("job_number"), jobs_directory)
    return save_job_to_path(job, destination, jobs_directory, overwrite)


def load_job_path(path):
    """Load and normalize one TUI- or GUI-created traveler."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as traveler_file:
        job = json.load(traveler_file)
    return normalized_job(job)


def load_job_data(job_number, jobs_directory=None):
    """Load one traveler by its validated job number."""
    return load_job_path(traveler_path(job_number, jobs_directory))


def current_job_status(job):
    """Derive a display-only overall status without changing the JSON schema."""
    statuses = [terminal_app.status_if_missing(job, section) for section in terminal_app.SECTIONS]
    if statuses and all(status == "Completed" for status in statuses):
        return "Completed"
    if any(status in {"In Progress", "Completed"} for status in statuses):
        return "In Progress"
    return "Pending"


def list_saved_jobs(jobs_directory=None):
    """Return valid traveler summaries plus unreadable-file diagnostics."""
    directory = get_jobs_directory(jobs_directory)
    if not directory.exists():
        return [], []

    travelers = []
    errors = []
    for path in sorted(directory.glob("*.json"), key=lambda item: item.name.casefold()):
        try:
            job = load_job_path(path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as error:
            errors.append((path, str(error)))
            continue

        travelers.append(
            {
                "path": path,
                "job": job,
                "job_number": job.get("job_number", path.stem),
                "customer": job.get("customer", ""),
                "part_number": job.get("part_number", ""),
                "quantity": job.get("qty_to_make", ""),
                "status": current_job_status(job),
            }
        )
    return travelers, errors


def merge_section(job, section_name, changes, timestamp=None):
    """Merge section changes so legacy, private, and unknown keys survive."""
    if section_name not in terminal_app.SECTIONS:
        raise ValidationError(f"Unknown traveler section: {section_name}")
    if section_name not in job:
        current = {}
    else:
        current = job[section_name]
        if not isinstance(current, dict):
            raise ValidationError(
                f"Traveler section '{section_name}' must contain a JSON object."
            )
    updated = copy.deepcopy(current)
    updated.update(changes)
    updated["last_updated"] = timestamp or terminal_app.get_timestamp()
    job[section_name] = updated
    return updated


def operation_for_machine(machine, fallback=""):
    """Infer a legacy Programming operation from its official machine."""
    if machine in terminal_app.MILLING_MACHINES:
        return "Mill"
    if machine in terminal_app.TURNING_MACHINES:
        return "Turning"
    return fallback


def machines_for_operation(operation):
    """Return the official machine list for one operation."""
    operation = require_choice(
        operation, "Operation", terminal_app.ALLOWED_OPERATIONS
    )
    if operation == "Mill":
        return tuple(terminal_app.MILLING_MACHINES)
    return tuple(terminal_app.TURNING_MACHINES)


def apply_programming_update(job, values, timestamp=None):
    """Validate and replace a sequential Programming operation plan."""
    operation_count = parse_positive_integer(
        values.get("operation_count"), "Number of Operations Required"
    )
    raw_operations = values.get("operations")
    if not isinstance(raw_operations, list) or len(raw_operations) != operation_count:
        raise ValidationError("Provide one Programming record for each operation.")
    try:
        candidate = terminal_app.resize_operation_plan(
            job, operation_count, bool(values.get("confirm_removal"))
        )
    except ValueError as error:
        raise ValidationError(str(error))
    updated_operations = []
    updated_at = timestamp or terminal_app.get_timestamp()
    for number, raw in enumerate(raw_operations, start=1):
        if not isinstance(raw, dict):
            raise ValidationError(f"Operation {number} must be a record.")
        existing = candidate["programming"]["operations"][number - 1]
        operation = copy.deepcopy(existing)
        operation.update(
            {
                "operation_number": number,
                "operation_type": require_choice(
                    raw.get("operation_type"),
                    f"Operation {number} Type",
                    terminal_app.ALLOWED_OPERATIONS,
                ),
                "program_name": clean_text(raw.get("program_name")),
                "revision": clean_text(raw.get("revision")),
                "status": require_choice(
                    raw.get("status"),
                    f"Operation {number} Status",
                    terminal_app.ALLOWED_STATUSES,
                ),
                "last_updated": updated_at,
                "notes": clean_text(raw.get("notes")),
            }
        )
        operation.pop("machine", None)
        operation.pop("operation", None)
        updated_operations.append(operation)
    programming = candidate["programming"]
    programming["programmer"] = clean_text(values.get("programmer"))
    programming["operation_count"] = operation_count
    programming["operations"] = updated_operations
    for key in (
        "program_name",
        "revision",
        "operation",
        "machine",
        "status",
        "last_updated",
        "notes",
    ):
        programming.pop(key, None)
    job.clear()
    job.update(candidate)
    return programming


STANDARD_SECTION_RULES = {
    "saw_cutting": {
        "text": ("employee", "cut_length", "notes"),
        "integer": (("qty_cut", "Qty Cut"), ("scrap_qty", "Scrap Qty")),
    },
    "deburr": {
        "text": ("employee", "deburr_needed", "notes"),
        "integer": (("qty_deburred", "Qty Deburred"),),
    },
    "packing": {
        "text": ("employee", "notes"),
        "integer": (("qty_packed", "Qty Packed"), ("box_count", "Box Count")),
    },
    "shipping": {
        "text": ("employee", "ship_date", "carrier", "tracking", "notes"),
        "integer": (),
    },
}


def apply_standard_section_update(job, section_name, values, timestamp=None):
    """Validate and merge one of the straightforward TUI sections."""
    if section_name not in STANDARD_SECTION_RULES:
        raise ValidationError(f"Unsupported standard section: {section_name}")
    rules = STANDARD_SECTION_RULES[section_name]
    changes = {key: clean_text(values.get(key)) for key in rules["text"]}
    for key, label in rules["integer"]:
        changes[key] = parse_nonnegative_integer(values.get(key), label)
    changes["status"] = require_choice(
        values.get("status"), "Status", terminal_app.ALLOWED_STATUSES
    )
    return merge_section(job, section_name, changes, timestamp)


def resolve_cnc_machine(job, operation_number=1):
    """Return a selected operation's saved CNC machine, including legacy fallback."""
    normalized = normalized_job(job)
    operation = terminal_app.operation_by_number(
        normalized["cnc_machining"]["operations"], operation_number
    )
    return operation.get("machine", "") if operation else ""


def apply_cnc_update(job, values, timestamp=None):
    """Update one CNC operation and derive its status from completed quantity."""
    candidate = normalized_job(job)
    operation_number = parse_positive_integer(values.get("operation_number"), "Operation")
    programming_operation = terminal_app.operation_by_number(
        candidate["programming"]["operations"], operation_number
    )
    if programming_operation is None:
        raise ValidationError("Select a configured operation.")
    operation_type = require_choice(
        programming_operation.get("operation_type"),
        "Operation Type",
        terminal_app.ALLOWED_OPERATIONS,
    )
    machine = require_choice(
        values.get("machine"), "Machine", machines_for_operation(operation_type)
    )
    qty_complete = parse_nonnegative_integer(
        values.get("qty_complete", values.get("qty_completed")),
        "Quantity Pieces Completed",
    )
    updated = terminal_app.operation_by_number(
        candidate["cnc_machining"]["operations"], operation_number
    )
    updated.update(
        {
            "operation_number": operation_number,
            "operator": clean_text(values.get("operator")),
            "machine": machine,
            "qty_complete": qty_complete,
            "status": terminal_app.get_cnc_status(
                candidate, qty_complete, updated.get("status")
            ),
            "last_updated": timestamp or terminal_app.get_timestamp(),
            "notes": clean_text(values.get("notes")),
        }
    )
    updated.pop("qty_completed", None)
    updated.pop("first_article", None)
    cnc = candidate["cnc_machining"]
    for key in (
        "operator",
        "machine",
        "qty_completed",
        "qty_complete",
        "status",
        "last_updated",
        "notes",
        "first_article",
    ):
        cnc.pop(key, None)
    job.clear()
    job.update(candidate)
    return updated


def apply_inspection_update(job, values, timestamp=None):
    """Update one inspection record using Programming/CNC-derived snapshots."""
    candidate = normalized_job(job)
    operation_number = parse_positive_integer(values.get("operation_number"), "Operation")
    programming_operation = terminal_app.operation_by_number(
        candidate["programming"]["operations"], operation_number
    )
    cnc_operation = terminal_app.operation_by_number(
        candidate["cnc_machining"]["operations"], operation_number
    )
    if programming_operation is None:
        raise ValidationError("Select a configured operation.")
    machine = cnc_operation.get("machine", "") if cnc_operation else ""
    if not machine:
        raise ValidationError(
            "No CNC machine is assigned to this operation. "
            "Assign the machine in CNC Machining first."
        )
    inspection = candidate["inspection"]
    record = terminal_app.operation_by_number(inspection["records"], operation_number)
    if record is None:
        record = {"operation_number": operation_number, "dimensions": []}
        inspection["records"].append(record)
    if not isinstance(record.get("dimensions", []), list):
        raise ValidationError(
            "Saved inspection dimensions are not a list. The traveler was not changed."
        )
    record.update(
        {
            "operation_type": programming_operation.get("operation_type", ""),
            "machine": machine,
            "inspector": clean_text(values.get("inspector")),
            "report_type": clean_text(values.get("report_type")) or "First Article Inspection",
            "status": require_choice(
                values.get("status"), "Status", terminal_app.ALLOWED_STATUSES
            ),
            "last_updated": timestamp or terminal_app.get_timestamp(),
            "notes": clean_text(values.get("notes")),
        }
    )
    for key in (
        "inspector",
        "report_type",
        "operation",
        "machine",
        "status",
        "dimensions",
        "notes",
        "last_updated",
    ):
        inspection.pop(key, None)
    job.clear()
    job.update(candidate)
    return record


def next_dimension_number(dimensions):
    """Return the next positive dimension number without renumbering legacy rows."""
    numbers = []
    for dimension in dimensions:
        if not isinstance(dimension, dict):
            continue
        number = dimension.get("dimension_number")
        if isinstance(number, int) and not isinstance(number, bool) and number > 0:
            numbers.append(number)
    return max(numbers, default=0) + 1


def build_dimension(values, dimension_number):
    """Validate and build one schema-compatible First Article dimension."""
    fields = {
        "target_dimension": "Target Dimension",
        "tolerance": "Tolerance",
        "finding": "Finding / Actual Dimension",
        "measurement_equipment_used": "Measurement Equipment Used",
    }
    dimension = {"dimension_number": dimension_number}
    for key, label in fields.items():
        value = clean_text(values.get(key))
        if not value:
            raise ValidationError(f"{label} is required.")
        dimension[key] = value
    dimension["result"] = require_choice(
        values.get("result"), "Result", ("Pass", "Rejected")
    )
    return dimension


def apply_dimensions_update(job, dimensions, operation_number=1, timestamp=None):
    """Persist dimensions under one operation-linked inspection record."""
    if not isinstance(dimensions, list):
        raise ValidationError("Inspection dimensions must be a list.")
    candidate = normalized_job(job)
    record = terminal_app.operation_by_number(
        candidate["inspection"]["records"], operation_number
    )
    if record is None:
        raise ValidationError("Save the Inspection header before adding dimensions.")
    record["dimensions"] = copy.deepcopy(dimensions)
    record["last_updated"] = timestamp or terminal_app.get_timestamp()
    job.clear()
    job.update(candidate)
    return record


def traveler_text(job):
    """Render the existing public TUI traveler into text for GUI preview."""
    output = io.StringIO()
    with redirect_stdout(output):
        terminal_app.print_traveler(job)
    return output.getvalue().lstrip("\n")


class ScrollableFrame(ttk.Frame):
    """A vertically scrollable ttk container for long shop forms."""

    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, background="#f4f6f8")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.content = ttk.Frame(self.canvas, style="App.TFrame", padding=(28, 18))
        self.window_id = self.canvas.create_window(
            (0, 0), window=self.content, anchor="nw"
        )
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.content.bind("<Configure>", self._update_scroll_region)
        self.canvas.bind("<Configure>", self._resize_content)
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

    def _update_scroll_region(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_content(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _bind_mousewheel(self, _event=None):
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, _event=None):
        self.canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def destroy(self):
        self.canvas.unbind_all("<MouseWheel>")
        super().destroy()


class JobTravelerApp:
    """Single-window, navigable Job Traveler GUI controller."""

    def __init__(self, root, jobs_directory=None):
        self.root = root
        self.jobs_directory = get_jobs_directory(jobs_directory)
        self.current_job = None
        self.current_path = None
        self.root.title(APP_TITLE)
        self.root.geometry("1100x760")
        self.root.minsize(900, 620)
        self.root.configure(background="#f4f6f8")
        self.root.protocol("WM_DELETE_WINDOW", self.close_application)
        self._configure_styles()
        self.show_home()

    def _configure_styles(self):
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("App.TFrame", background="#f4f6f8")
        style.configure(
            "Title.TLabel",
            background="#f4f6f8",
            foreground="#17324d",
            font=("Segoe UI", 26, "bold"),
        )
        style.configure(
            "Heading.TLabel",
            background="#f4f6f8",
            foreground="#17324d",
            font=("Segoe UI", 17, "bold"),
        )
        style.configure(
            "Subheading.TLabel",
            background="#f4f6f8",
            foreground="#44596d",
            font=("Segoe UI", 10),
        )
        style.configure("Action.TButton", font=("Segoe UI", 11), padding=(14, 10))
        style.configure("Section.TButton", font=("Segoe UI", 10), padding=(10, 9))
        style.configure("Field.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Value.TLabel", font=("Segoe UI", 10))
        style.configure("Status.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Treeview", rowheight=28, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))

    def clear_screen(self):
        for child in self.root.winfo_children():
            child.destroy()

    def page_header(self, parent, title, subtitle=""):
        ttk.Label(parent, text=title, style="Heading.TLabel").pack(anchor="w")
        if subtitle:
            ttk.Label(parent, text=subtitle, style="Subheading.TLabel").pack(
                anchor="w", pady=(4, 18)
            )
        else:
            ttk.Separator(parent).pack(fill="x", pady=(8, 18))

    def close_application(self):
        self.root.destroy()

    def show_home(self):
        self.clear_screen()
        frame = ttk.Frame(self.root, style="App.TFrame", padding=40)
        frame.pack(fill="both", expand=True)
        center = ttk.Frame(frame, style="App.TFrame")
        center.place(relx=0.5, rely=0.44, anchor="center")
        ttk.Label(center, text=APP_TITLE, style="Title.TLabel").pack(pady=(0, 8))
        ttk.Label(
            center,
            text="Create and manage CNC shop travelers",
            style="Subheading.TLabel",
        ).pack(pady=(0, 28))
        ttk.Button(
            center,
            text="Create New Job",
            command=self.show_create_job,
            style="Action.TButton",
            width=28,
        ).pack(fill="x", pady=6)
        ttk.Button(
            center,
            text="Open Existing Job",
            command=self.show_job_list,
            style="Action.TButton",
            width=28,
        ).pack(fill="x", pady=6)
        ttk.Button(
            center,
            text="Exit",
            command=self.close_application,
            style="Action.TButton",
            width=28,
        ).pack(fill="x", pady=6)

    def show_create_job(self):
        self.clear_screen()
        scroll = ScrollableFrame(self.root)
        scroll.pack(fill="both", expand=True)
        content = scroll.content
        self.page_header(
            content,
            "Create New Job",
            "Required fields match the existing terminal traveler format.",
        )
        form = ttk.LabelFrame(content, text="Job Information", padding=20)
        form.pack(fill="x")
        labels = (
            ("job_number", "Job Number"),
            ("customer", "Customer"),
            ("part_number", "Part Number"),
            ("description", "Description"),
            ("qty_to_make", "Quantity to Make"),
            ("material", "Material"),
            ("cut_length", "Cut Length"),
        )
        entries = {}
        for row, (key, label) in enumerate(labels):
            ttk.Label(form, text=f"{label}:", style="Field.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 16), pady=7
            )
            entry = ttk.Entry(form, width=60)
            entry.grid(row=row, column=1, sticky="ew", pady=7)
            entries[key] = entry
        form.columnconfigure(1, weight=1)
        entries["job_number"].focus_set()

        buttons = ttk.Frame(content)
        buttons.pack(fill="x", pady=(20, 0))
        ttk.Button(
            buttons,
            text="Create and Open Job",
            style="Action.TButton",
            command=lambda: self._create_job(entries),
        ).pack(side="right")
        ttk.Button(
            buttons, text="Back", style="Action.TButton", command=self.show_home
        ).pack(side="right", padx=(0, 10))

    def _create_job(self, entries):
        values = {key: widget.get() for key, widget in entries.items()}
        try:
            job = create_job_record(values)
            path = traveler_path(job["job_number"], self.jobs_directory)
        except ValidationError as error:
            messagebox.showerror("Invalid Job", str(error), parent=self.root)
            return

        overwrite = False
        if path.exists():
            overwrite = messagebox.askyesno(
                "Traveler Already Exists",
                f"Job {job['job_number']} already exists. Replace that traveler?",
                icon="warning",
                parent=self.root,
            )
            if not overwrite:
                return

        try:
            self.current_path = save_job_data(
                job, self.jobs_directory, overwrite=overwrite
            )
        except (OSError, TypeError, ValueError) as error:
            messagebox.showerror(
                "Save Error", f"Could not save the traveler:\n\n{error}", parent=self.root
            )
            return

        self.current_job = job
        self.show_job_detail()

    def show_job_list(self):
        self.clear_screen()
        frame = ttk.Frame(self.root, style="App.TFrame", padding=28)
        frame.pack(fill="both", expand=True)
        self.page_header(
            frame, "Open Existing Job", f"Traveler folder: {self.jobs_directory}"
        )
        try:
            travelers, errors = list_saved_jobs(self.jobs_directory)
        except OSError as error:
            messagebox.showerror(
                "Open Error", f"Could not read the traveler folder:\n\n{error}", parent=self.root
            )
            travelers, errors = [], []

        columns = ("job_number", "customer", "part_number", "quantity", "status")
        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "job_number": "Job Number",
            "customer": "Customer",
            "part_number": "Part Number",
            "quantity": "Quantity",
            "status": "Current Status",
        }
        widths = (150, 220, 180, 100, 140)
        for key, width in zip(columns, widths):
            tree.heading(key, text=headings[key])
            tree.column(key, width=width, anchor="w")
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        row_paths = {}
        for traveler in travelers:
            item = tree.insert(
                "",
                "end",
                values=tuple(traveler[key] for key in columns),
            )
            row_paths[item] = traveler["path"]

        if not travelers:
            ttk.Label(
                frame,
                text="No saved job travelers were found.",
                style="Subheading.TLabel",
            ).pack(anchor="w", pady=(14, 0))
        if errors:
            ttk.Label(
                frame,
                text=f"Skipped {len(errors)} unreadable traveler file(s).",
                foreground="#9c2f2f",
            ).pack(anchor="w", pady=(8, 0))

        def open_selected(_event=None):
            selection = tree.selection()
            if not selection:
                messagebox.showinfo(
                    "Open Job", "Select a traveler first.", parent=self.root
                )
                return
            path = row_paths[selection[0]]
            try:
                self.current_job = load_job_path(path)
            except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as error:
                messagebox.showerror(
                    "Open Error", f"Could not open the traveler:\n\n{error}", parent=self.root
                )
                return
            self.current_path = path
            self.show_job_detail()

        tree.bind("<Double-1>", open_selected)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(18, 0))
        ttk.Button(
            buttons, text="Open Selected", style="Action.TButton", command=open_selected
        ).pack(side="right")
        ttk.Button(
            buttons, text="Home", style="Action.TButton", command=self.show_home
        ).pack(side="left")

    def _persist_candidate(self, candidate, confirmation=None):
        """Persist a copy and adopt it only after the disk write succeeds."""
        if candidate is None:
            return False
        try:
            destination = self.current_path or traveler_path(
                candidate.get("job_number"), self.jobs_directory
            )
            saved_path = save_job_to_path(
                candidate,
                destination,
                self.jobs_directory,
                overwrite=True,
            )
        except (OSError, TypeError, ValueError) as error:
            messagebox.showerror(
                "Save Error", f"Could not save the traveler:\n\n{error}", parent=self.root
            )
            return False
        self.current_job = candidate
        self.current_path = saved_path
        if confirmation:
            messagebox.showinfo("Saved", confirmation, parent=self.root)
        return True

    def _persist_current(self, confirmation=None):
        if self.current_job is None:
            return False
        return self._persist_candidate(copy.deepcopy(self.current_job), confirmation)

    def show_job_detail(self):
        if self.current_job is None:
            self.show_home()
            return
        self.clear_screen()
        scroll = ScrollableFrame(self.root)
        scroll.pack(fill="both", expand=True)
        content = scroll.content
        job_number = self.current_job.get("job_number", terminal_app.BLANK)
        self.page_header(content, f"Job {job_number}", "Job detail and section workflow")

        header = ttk.LabelFrame(content, text="Job Header", padding=16)
        header.pack(fill="x")
        header_items = (
            ("Customer", "customer"),
            ("Part Number", "part_number"),
            ("Description", "description"),
            ("Quantity to Make", "qty_to_make"),
            ("Material", "material"),
            ("Cut Length", "cut_length"),
            ("Current Status", None),
        )
        for index, (label, key) in enumerate(header_items):
            row, column = divmod(index, 2)
            value = (
                current_job_status(self.current_job)
                if key is None
                else self.current_job.get(key, "")
            )
            cell = ttk.Frame(header)
            cell.grid(row=row, column=column, sticky="ew", padx=8, pady=6)
            ttk.Label(cell, text=f"{label}:", style="Field.TLabel").pack(side="left")
            ttk.Label(cell, text=str(value), style="Value.TLabel", wraplength=330).pack(
                side="left", padx=(8, 0)
            )
        header.columnconfigure(0, weight=1)
        header.columnconfigure(1, weight=1)

        sections = ttk.LabelFrame(content, text="Traveler Sections", padding=16)
        sections.pack(fill="x", pady=(18, 0))
        actions = (
            ("Programming", self.show_programming),
            ("Saw Cutting", lambda: self.show_standard_section("saw_cutting")),
            ("CNC Machining", self.show_cnc_machining),
            ("Deburr", lambda: self.show_standard_section("deburr")),
            ("Inspection", self.show_inspection),
            ("Packing", lambda: self.show_standard_section("packing")),
            ("Shipping", lambda: self.show_standard_section("shipping")),
            ("Print / View Traveler", self.show_traveler_preview),
        )
        for index, (label, command) in enumerate(actions):
            row, column = divmod(index, 3)
            ttk.Button(
                sections,
                text=label,
                command=command,
                style="Section.TButton",
            ).grid(row=row, column=column, sticky="ew", padx=6, pady=6)
        for column in range(3):
            sections.columnconfigure(column, weight=1)

        statuses = ttk.LabelFrame(content, text="Section Status", padding=16)
        statuses.pack(fill="x", pady=(18, 0))
        for index, section in enumerate(terminal_app.SECTIONS):
            label = section.replace("_", " ").title()
            ttk.Label(statuses, text=f"{label}:", style="Field.TLabel").grid(
                row=index // 4, column=(index % 4) * 2, sticky="w", padx=(6, 4), pady=5
            )
            ttk.Label(
                statuses,
                text=terminal_app.status_if_missing(self.current_job, section),
                style="Status.TLabel",
            ).grid(
                row=index // 4,
                column=(index % 4) * 2 + 1,
                sticky="w",
                padx=(0, 18),
                pady=5,
            )

        buttons = ttk.Frame(content)
        buttons.pack(fill="x", pady=(20, 0))
        ttk.Button(
            buttons,
            text="Save",
            style="Action.TButton",
            command=lambda: self._persist_current("Traveler saved."),
        ).pack(side="right")
        ttk.Button(
            buttons,
            text="Return to Job List",
            style="Action.TButton",
            command=self.show_job_list,
        ).pack(side="left")
        ttk.Button(
            buttons, text="Home", style="Action.TButton", command=self.show_home
        ).pack(side="left", padx=(10, 0))

    def _show_section_form(self, title, section_name, field_specs, save_callback):
        self.clear_screen()
        scroll = ScrollableFrame(self.root)
        scroll.pack(fill="both", expand=True)
        content = scroll.content
        self.page_header(content, title, "Existing values are loaded and preserved.")
        section = self.current_job.get(section_name, {})
        if not isinstance(section, dict):
            section = {}
        form = ttk.LabelFrame(content, text=title, padding=20)
        form.pack(fill="x")
        widgets = {}
        for row, spec in enumerate(field_specs):
            key, label, kind = spec[:3]
            options = spec[3] if len(spec) > 3 else ()
            ttk.Label(form, text=f"{label}:", style="Field.TLabel").grid(
                row=row, column=0, sticky="nw", padx=(0, 16), pady=7
            )
            current = section.get(key, "")
            if current is None:
                current = ""
            if kind == "notes":
                widget = tk.Text(form, height=5, width=62, wrap="word", font=("Segoe UI", 10))
                widget.insert("1.0", str(current))
            elif kind == "combo":
                widget = ttk.Combobox(form, values=options, state="readonly", width=58)
                if current != "":
                    # Show legacy values as saved. Validation requires an
                    # explicit supported replacement instead of silently
                    # substituting a default when the section is saved.
                    widget.set(str(current))
            else:
                widget = ttk.Entry(form, width=62)
                widget.insert(0, str(current))
            widget.grid(row=row, column=1, sticky="ew", pady=7)
            widgets[key] = widget
        form.columnconfigure(1, weight=1)

        def collect_values():
            result = {}
            for spec in field_specs:
                key, _label, kind = spec[:3]
                widget = widgets[key]
                if kind == "notes":
                    result[key] = widget.get("1.0", "end-1c")
                else:
                    result[key] = widget.get()
            return result

        def save_section():
            candidate = copy.deepcopy(self.current_job)
            try:
                save_callback(candidate, collect_values())
            except ValidationError as error:
                messagebox.showerror("Invalid Section", str(error), parent=self.root)
                return
            if self._persist_candidate(candidate, f"{title} saved."):
                self.show_job_detail()

        buttons = ttk.Frame(content)
        buttons.pack(fill="x", pady=(20, 0))
        ttk.Button(
            buttons, text="Save Section", style="Action.TButton", command=save_section
        ).pack(side="right")
        ttk.Button(
            buttons,
            text="Cancel",
            style="Action.TButton",
            command=self.show_job_detail,
        ).pack(side="right", padx=(0, 10))

    def show_programming(self):
        self.clear_screen()
        scroll = ScrollableFrame(self.root)
        scroll.pack(fill="both", expand=True)
        content = scroll.content
        self.page_header(
            content,
            "Programming",
            "Define the machining plan. Machines are assigned later in CNC Machining.",
        )
        normalized = normalized_job(self.current_job)
        programming = normalized["programming"]
        form = ttk.LabelFrame(content, text="Programming", padding=20)
        form.pack(fill="x")
        ttk.Label(form, text="Programmer Name:", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 16), pady=7
        )
        programmer = ttk.Entry(form, width=62)
        programmer.insert(0, str(programming.get("programmer", "")))
        programmer.grid(row=0, column=1, sticky="ew", pady=7)
        ttk.Label(form, text="Number of Operations:", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 16), pady=7
        )
        count = ttk.Entry(form, width=16)
        count.insert(0, str(programming["operation_count"]))
        count.grid(row=1, column=1, sticky="w", pady=7)
        form.columnconfigure(1, weight=1)

        operations_frame = ttk.Frame(content, style="App.TFrame")
        operations_frame.pack(fill="x", pady=(16, 0))
        operation_widgets = []

        def collect_operations():
            rows = []
            for widgets in operation_widgets:
                rows.append(
                    {
                        "operation_type": widgets["operation_type"].get(),
                        "program_name": widgets["program_name"].get(),
                        "revision": widgets["revision"].get(),
                        "status": widgets["status"].get(),
                        "notes": widgets["notes"].get("1.0", "end-1c"),
                    }
                )
            return rows

        def render_operations(desired_count, seed_rows=None):
            for child in operations_frame.winfo_children():
                child.destroy()
            operation_widgets.clear()
            seed_rows = seed_rows or programming["operations"]
            for index in range(desired_count):
                saved = seed_rows[index] if index < len(seed_rows) else {}
                card = ttk.LabelFrame(
                    operations_frame, text=f"Operation {index + 1}", padding=16
                )
                card.pack(fill="x", pady=(0, 12))
                widgets = {}
                specs = (
                    ("operation_type", "Operation Type", "combo", terminal_app.ALLOWED_OPERATIONS),
                    ("program_name", "Program Name", "entry", ()),
                    ("revision", "Revision", "entry", ()),
                    ("status", "Status", "combo", terminal_app.ALLOWED_STATUSES),
                    ("notes", "Notes", "notes", ()),
                )
                for row, (key, label, kind, choices) in enumerate(specs):
                    ttk.Label(card, text=f"{label}:", style="Field.TLabel").grid(
                        row=row, column=0, sticky="nw", padx=(0, 16), pady=5
                    )
                    if kind == "combo":
                        widget = ttk.Combobox(card, values=choices, state="readonly", width=55)
                        widget.set(str(saved.get(key, "")))
                    elif kind == "notes":
                        widget = tk.Text(
                            card,
                            height=3,
                            width=59,
                            wrap="word",
                            font=("Segoe UI", 10),
                        )
                        widget.insert("1.0", str(saved.get(key, "")))
                    else:
                        widget = ttk.Entry(card, width=59)
                        widget.insert(0, str(saved.get(key, "")))
                    widget.grid(row=row, column=1, sticky="ew", pady=5)
                    widgets[key] = widget
                card.columnconfigure(1, weight=1)
                operation_widgets.append(widgets)

        def refresh_editor():
            try:
                desired_count = parse_positive_integer(
                    count.get(), "Number of Operations Required"
                )
            except ValidationError as error:
                messagebox.showerror("Invalid Programming", str(error), parent=self.root)
                return
            render_operations(desired_count, collect_operations())

        ttk.Button(form, text="Update Operation Editor", command=refresh_editor).grid(
            row=1, column=1, sticky="e", pady=7
        )
        render_operations(programming["operation_count"])

        def save_section():
            candidate = copy.deepcopy(self.current_job)
            try:
                desired_count = parse_positive_integer(
                    count.get(), "Number of Operations Required"
                )
            except ValidationError as error:
                messagebox.showerror("Invalid Programming", str(error), parent=self.root)
                return
            if desired_count != len(operation_widgets):
                messagebox.showerror(
                    "Invalid Programming",
                    "Update the operation editor after changing the operation count.",
                    parent=self.root,
                )
                return
            confirm_removal = False
            if desired_count < programming["operation_count"]:
                confirm_removal = messagebox.askyesno(
                    "Reduce Operations",
                    "Remove the unused operation(s)? Production and inspection "
                    "data will never be deleted.",
                    parent=self.root,
                )
                if not confirm_removal:
                    return
            try:
                apply_programming_update(
                    candidate,
                    {
                        "programmer": programmer.get(),
                        "operation_count": str(desired_count),
                        "operations": collect_operations(),
                        "confirm_removal": confirm_removal,
                    },
                )
            except ValidationError as error:
                messagebox.showerror("Invalid Programming", str(error), parent=self.root)
                return
            if self._persist_candidate(candidate, "Programming saved."):
                self.show_job_detail()

        buttons = ttk.Frame(content)
        buttons.pack(fill="x", pady=(20, 0))
        ttk.Button(
            buttons, text="Save Section", style="Action.TButton", command=save_section
        ).pack(side="right")
        ttk.Button(
            buttons,
            text="Cancel",
            style="Action.TButton",
            command=self.show_job_detail,
        ).pack(side="right", padx=(0, 10))

    def show_standard_section(self, section_name):
        configurations = {
            "saw_cutting": (
                "Saw Cutting",
                (
                    ("employee", "Employee", "entry"),
                    ("qty_cut", "Qty Cut", "entry"),
                    ("cut_length", "Cut Length", "entry"),
                    ("scrap_qty", "Scrap Qty (private)", "entry"),
                    ("status", "Status", "combo", terminal_app.ALLOWED_STATUSES),
                    ("notes", "Notes", "notes"),
                ),
            ),
            "deburr": (
                "Deburr",
                (
                    ("employee", "Employee", "entry"),
                    ("deburr_needed", "Deburr Needed", "entry"),
                    ("qty_deburred", "Qty Deburred", "entry"),
                    ("status", "Status", "combo", terminal_app.ALLOWED_STATUSES),
                    ("notes", "Notes", "notes"),
                ),
            ),
            "packing": (
                "Packing",
                (
                    ("employee", "Employee", "entry"),
                    ("qty_packed", "Qty Packed", "entry"),
                    ("box_count", "Box Count", "entry"),
                    ("status", "Status", "combo", terminal_app.ALLOWED_STATUSES),
                    ("notes", "Notes", "notes"),
                ),
            ),
            "shipping": (
                "Shipping",
                (
                    ("employee", "Employee", "entry"),
                    ("ship_date", "Ship Date", "entry"),
                    ("carrier", "Carrier", "entry"),
                    ("tracking", "Tracking", "entry"),
                    ("status", "Status", "combo", terminal_app.ALLOWED_STATUSES),
                    ("notes", "Notes", "notes"),
                ),
            ),
        }
        title, specs = configurations[section_name]
        self._show_section_form(
            title,
            section_name,
            specs,
            lambda job, values: apply_standard_section_update(
                job, section_name, values
            ),
        )

    def show_cnc_machining(self):
        self.clear_screen()
        scroll = ScrollableFrame(self.root)
        scroll.pack(fill="both", expand=True)
        frame = scroll.content
        self.page_header(
            frame,
            "CNC Machining",
            "Status is calculated automatically from completed and required quantities.",
        )
        normalized = normalized_job(self.current_job)
        programming_operations = normalized["programming"]["operations"]
        cnc_operations = normalized["cnc_machining"]["operations"]

        form = ttk.LabelFrame(frame, text="CNC Workflow", padding=20)
        form.pack(fill="x")
        ttk.Label(form, text="Operation:", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 16), pady=8
        )
        operation = ttk.Combobox(
            form,
            values=[
                f"Operation {row['operation_number']} - "
                f"{row.get('operation_type') or terminal_app.BLANK}"
                for row in programming_operations
            ],
            state="readonly",
            width=58,
        )
        operation.grid(row=0, column=1, sticky="ew", pady=8)
        ttk.Label(form, text="Operation Type:", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 16), pady=8
        )
        operation_type_value = ttk.Label(form, text="", style="Value.TLabel")
        operation_type_value.grid(row=1, column=1, sticky="w", pady=8)
        ttk.Label(form, text="Operator Name:", style="Field.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 16), pady=8
        )
        operator = ttk.Entry(form, width=60)
        operator.grid(row=2, column=1, sticky="ew", pady=8)

        ttk.Label(form, text="Machine:", style="Field.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 16), pady=8
        )
        machine = ttk.Combobox(form, state="readonly", width=58)
        machine.grid(row=3, column=1, sticky="ew", pady=8)

        ttk.Label(
            form, text="Quantity Pieces Completed:", style="Field.TLabel"
        ).grid(row=4, column=0, sticky="w", padx=(0, 16), pady=8)
        quantity = ttk.Entry(form, width=60)
        quantity.grid(row=4, column=1, sticky="ew", pady=8)
        ttk.Label(form, text="Required Quantity:", style="Field.TLabel").grid(
            row=5, column=0, sticky="w", padx=(0, 16), pady=8
        )
        ttk.Label(
            form, text=str(self.current_job.get("qty_to_make", "")), style="Value.TLabel"
        ).grid(row=5, column=1, sticky="w", pady=8)
        ttk.Label(form, text="Current Status:", style="Field.TLabel").grid(
            row=6, column=0, sticky="w", padx=(0, 16), pady=8
        )
        status_value = ttk.Label(form, text="Pending", style="Status.TLabel")
        status_value.grid(row=6, column=1, sticky="w", pady=8)
        ttk.Label(form, text="Notes:", style="Field.TLabel").grid(
            row=7, column=0, sticky="nw", padx=(0, 16), pady=8
        )
        notes = tk.Text(form, height=4, width=60, wrap="word", font=("Segoe UI", 10))
        notes.grid(row=7, column=1, sticky="ew", pady=8)
        form.columnconfigure(1, weight=1)

        def selected_number():
            index = operation.current()
            return programming_operations[index]["operation_number"] if index >= 0 else None

        def refresh_operation(_event=None):
            number = selected_number()
            if number is None:
                return
            plan = terminal_app.operation_by_number(programming_operations, number)
            saved = terminal_app.operation_by_number(cnc_operations, number)
            operation_type = plan.get("operation_type", "")
            operation_type_value.configure(text=operation_type)
            choices = (
                machines_for_operation(operation_type)
                if operation_type in terminal_app.ALLOWED_OPERATIONS
                else ()
            )
            machine.configure(values=choices)
            machine.set(saved.get("machine", ""))
            operator.delete(0, "end")
            operator.insert(0, str(saved.get("operator", "")))
            quantity.delete(0, "end")
            quantity.insert(0, str(saved.get("qty_complete", 0)))
            status_value.configure(text=saved.get("status", "Pending"))
            notes.delete("1.0", "end")
            notes.insert("1.0", str(saved.get("notes", "")))

        operation.bind("<<ComboboxSelected>>", refresh_operation)
        if programming_operations:
            operation.current(0)
            refresh_operation()

        def save_section():
            candidate = copy.deepcopy(self.current_job)
            try:
                updated = apply_cnc_update(
                    candidate,
                    {
                        "operator": operator.get(),
                        "machine": machine.get(),
                        "qty_complete": quantity.get(),
                        "operation_number": selected_number(),
                        "notes": notes.get("1.0", "end-1c"),
                    },
                )
            except ValidationError as error:
                messagebox.showerror("Invalid CNC Update", str(error), parent=self.root)
                return
            if self._persist_candidate(
                candidate,
                f"CNC Machining saved with status: {updated['status']}."
            ):
                self.show_job_detail()

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(20, 0))
        ttk.Button(
            buttons, text="Save Section", style="Action.TButton", command=save_section
        ).pack(side="right")
        ttk.Button(
            buttons,
            text="Cancel",
            style="Action.TButton",
            command=self.show_job_detail,
        ).pack(side="right", padx=(0, 10))

    def show_inspection(self):
        self.clear_screen()
        scroll = ScrollableFrame(self.root)
        scroll.pack(fill="both", expand=True)
        content = scroll.content
        self.page_header(
            content,
            "Inspection",
            "Select an operation; its type and CNC machine are filled automatically.",
        )
        normalized = normalized_job(self.current_job)
        programming_operations = normalized["programming"]["operations"]
        cnc_operations = normalized["cnc_machining"]["operations"]
        inspection_records = normalized["inspection"]["records"]
        form = ttk.LabelFrame(content, text="Inspection Header", padding=20)
        form.pack(fill="x")

        ttk.Label(form, text="Operation:", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 16), pady=7
        )
        operation = ttk.Combobox(
            form,
            values=[
                f"Operation {row['operation_number']} - "
                f"{row.get('operation_type') or terminal_app.BLANK}"
                for row in programming_operations
            ],
            state="readonly",
            width=58,
        )
        operation.grid(row=0, column=1, sticky="ew", pady=7)
        ttk.Label(form, text="Operation Type:", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 16), pady=7
        )
        operation_type_value = ttk.Label(form, text="", style="Value.TLabel")
        operation_type_value.grid(row=1, column=1, sticky="w", pady=7)
        ttk.Label(form, text="Machine:", style="Field.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 16), pady=7
        )
        machine_value = ttk.Label(form, text="", style="Value.TLabel")
        machine_value.grid(row=2, column=1, sticky="w", pady=7)
        warning_value = ttk.Label(form, text="", foreground="#a33a2b")
        warning_value.grid(row=3, column=1, sticky="w", pady=(0, 7))
        ttk.Label(form, text="Inspector:", style="Field.TLabel").grid(
            row=4, column=0, sticky="w", padx=(0, 16), pady=7
        )
        inspector = ttk.Entry(form, width=60)
        inspector.grid(row=4, column=1, sticky="ew", pady=7)
        ttk.Label(form, text="Report Type:", style="Field.TLabel").grid(
            row=5, column=0, sticky="w", padx=(0, 16), pady=7
        )
        report_type_value = ttk.Label(form, text="First Article Inspection", style="Value.TLabel")
        report_type_value.grid(row=5, column=1, sticky="w", pady=7)
        ttk.Label(form, text="Status:", style="Field.TLabel").grid(
            row=6, column=0, sticky="w", padx=(0, 16), pady=7
        )
        status = ttk.Combobox(
            form, values=terminal_app.ALLOWED_STATUSES, state="readonly", width=58
        )
        status.grid(row=6, column=1, sticky="ew", pady=7)
        ttk.Label(form, text="Notes:", style="Field.TLabel").grid(
            row=7, column=0, sticky="nw", padx=(0, 16), pady=7
        )
        notes = tk.Text(form, height=6, width=60, wrap="word", font=("Segoe UI", 10))
        notes.grid(row=7, column=1, sticky="ew", pady=7)
        form.columnconfigure(1, weight=1)

        def selected_number():
            index = operation.current()
            return programming_operations[index]["operation_number"] if index >= 0 else None

        def refresh_operation(_event=None):
            number = selected_number()
            if number is None:
                return
            plan = terminal_app.operation_by_number(programming_operations, number)
            cnc = terminal_app.operation_by_number(cnc_operations, number)
            record = terminal_app.operation_by_number(inspection_records, number) or {}
            operation_type_value.configure(text=plan.get("operation_type", ""))
            machine = cnc.get("machine", "") if cnc else ""
            machine_value.configure(text=machine or "Not assigned")
            warning_value.configure(
                text=(
                    ""
                    if machine
                    else "Assign the machine in CNC Machining before inspecting "
                    "this operation."
                )
            )
            inspector.delete(0, "end")
            inspector.insert(0, str(record.get("inspector", "")))
            report_type_value.configure(
                text=record.get("report_type") or "First Article Inspection"
            )
            status.set(str(record.get("status", "Pending")))
            notes.delete("1.0", "end")
            notes.insert("1.0", str(record.get("notes", "")))

        operation.bind("<<ComboboxSelected>>", refresh_operation)
        if programming_operations:
            operation.current(0)
            refresh_operation()

        def inspection_values():
            return {
                "inspector": inspector.get(),
                "operation_number": selected_number(),
                "report_type": report_type_value.cget("text"),
                "status": status.get(),
                "notes": notes.get("1.0", "end-1c"),
            }

        def validated_candidate():
            candidate = copy.deepcopy(self.current_job)
            apply_inspection_update(candidate, inspection_values())
            return candidate

        def save_section():
            try:
                candidate = validated_candidate()
            except ValidationError as error:
                messagebox.showerror("Invalid Inspection", str(error), parent=self.root)
                return
            if self._persist_candidate(candidate, "Inspection saved."):
                self.show_job_detail()

        def save_and_open_dimensions():
            try:
                candidate = validated_candidate()
            except ValidationError as error:
                messagebox.showerror("Invalid Inspection", str(error), parent=self.root)
                return
            if self._persist_candidate(candidate):
                self.show_dimensions(selected_number())

        buttons = ttk.Frame(content)
        buttons.pack(fill="x", pady=(20, 0))
        ttk.Button(
            buttons, text="Save Section", style="Action.TButton", command=save_section
        ).pack(side="right")
        ttk.Button(
            buttons,
            text="Save & Edit Dimensions",
            style="Action.TButton",
            command=save_and_open_dimensions,
        ).pack(side="right", padx=(0, 10))
        ttk.Button(
            buttons,
            text="Cancel",
            style="Action.TButton",
            command=self.show_job_detail,
        ).pack(side="left")

    def show_dimensions(self, operation_number=1):
        normalized = normalized_job(self.current_job)
        record = terminal_app.operation_by_number(
            normalized["inspection"]["records"], operation_number
        )
        if record is None:
            messagebox.showerror(
                "Invalid Inspection", "Save the Inspection header first.", parent=self.root
            )
            return
        saved_dimensions = record.get("dimensions", [])
        if not isinstance(saved_dimensions, list):
            messagebox.showerror(
                "Invalid Dimensions",
                "The saved inspection dimensions are not a list and cannot be edited safely.",
                parent=self.root,
            )
            return

        self.clear_screen()
        scroll = ScrollableFrame(self.root)
        scroll.pack(fill="both", expand=True)
        content = scroll.content
        self.page_header(
            content,
            f"Inspection Dimensions - Operation {operation_number}",
            "Add dimensions one at a time. Existing dimension numbers are preserved.",
        )
        dimensions = copy.deepcopy(saved_dimensions)

        table_frame = ttk.LabelFrame(content, text="Saved Dimensions", padding=12)
        table_frame.pack(fill="both", expand=True)
        columns = ("number", "target", "tolerance", "finding", "equipment", "result")
        tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=8)
        labels = ("Dim", "Target", "Tolerance", "Finding", "Equipment", "Result")
        widths = (55, 145, 130, 145, 210, 100)
        for key, label, width in zip(columns, labels, widths):
            tree.heading(key, text=label)
            tree.column(key, width=width, anchor="w")
        table_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=table_scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        table_scroll.pack(side="right", fill="y")

        def refresh_table():
            for item in tree.get_children():
                tree.delete(item)
            for index, dimension in enumerate(dimensions):
                row = dimension if isinstance(dimension, dict) else {}
                equipment = row.get(
                    "measurement_equipment_used", row.get("tool_used", "")
                )
                tree.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=(
                        row.get("dimension_number", ""),
                        row.get("target_dimension", ""),
                        row.get("tolerance", ""),
                        row.get("finding", ""),
                        equipment,
                        row.get("result", ""),
                    ),
                )

        refresh_table()

        add_frame = ttk.LabelFrame(content, text="Add Dimension", padding=16)
        add_frame.pack(fill="x", pady=(16, 0))
        dimension_fields = (
            ("target_dimension", "Target Dimension"),
            ("tolerance", "Tolerance"),
            ("finding", "Finding / Actual Dimension"),
            ("measurement_equipment_used", "Measurement Equipment Used"),
        )
        entries = {}
        for row, (key, label) in enumerate(dimension_fields):
            ttk.Label(add_frame, text=f"{label}:", style="Field.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 16), pady=6
            )
            entry = ttk.Entry(add_frame, width=58)
            entry.grid(row=row, column=1, sticky="ew", pady=6)
            entries[key] = entry
        ttk.Label(add_frame, text="Result:", style="Field.TLabel").grid(
            row=4, column=0, sticky="w", padx=(0, 16), pady=6
        )
        result = ttk.Combobox(
            add_frame, values=("Pass", "Rejected"), state="readonly", width=56
        )
        result.set("Pass")
        result.grid(row=4, column=1, sticky="ew", pady=6)
        add_frame.columnconfigure(1, weight=1)

        def add_dimension():
            values = {key: entry.get() for key, entry in entries.items()}
            values["result"] = result.get()
            try:
                dimension = build_dimension(
                    values, next_dimension_number(dimensions)
                )
            except ValidationError as error:
                messagebox.showerror("Invalid Dimension", str(error), parent=self.root)
                return
            dimensions.append(dimension)
            refresh_table()
            for entry in entries.values():
                entry.delete(0, "end")
            result.set("Pass")
            entries["target_dimension"].focus_set()

        def remove_selected():
            selection = tree.selection()
            if not selection:
                messagebox.showinfo(
                    "Remove Dimension", "Select a dimension first.", parent=self.root
                )
                return
            if not messagebox.askyesno(
                "Remove Dimension",
                "Remove the selected dimension? Existing numbers will not be renumbered.",
                parent=self.root,
            ):
                return
            index = int(selection[0])
            del dimensions[index]
            refresh_table()

        dimension_buttons = ttk.Frame(add_frame)
        dimension_buttons.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(
            dimension_buttons, text="Add Dimension", command=add_dimension
        ).pack(side="left")
        ttk.Button(
            dimension_buttons, text="Remove Selected", command=remove_selected
        ).pack(side="left", padx=(10, 0))

        def save_dimensions():
            candidate = copy.deepcopy(self.current_job)
            apply_dimensions_update(candidate, dimensions, operation_number)
            if self._persist_candidate(candidate, "Inspection dimensions saved."):
                self.show_job_detail()

        buttons = ttk.Frame(content)
        buttons.pack(fill="x", pady=(20, 0))
        ttk.Button(
            buttons,
            text="Save Inspection",
            style="Action.TButton",
            command=save_dimensions,
        ).pack(side="right")
        ttk.Button(
            buttons,
            text="Cancel Changes",
            style="Action.TButton",
            command=self.show_job_detail,
        ).pack(side="right", padx=(0, 10))

    def show_traveler_preview(self):
        self.clear_screen()
        frame = ttk.Frame(self.root, style="App.TFrame", padding=24)
        frame.pack(fill="both", expand=True)
        self.page_header(frame, "Print / View Traveler", "Public traveler preview")
        preview = scrolledtext.ScrolledText(
            frame,
            wrap="none",
            font=("Consolas", 10),
            padx=12,
            pady=12,
        )
        preview.pack(fill="both", expand=True)
        preview.insert("1.0", traveler_text(self.current_job))
        preview.configure(state="disabled")
        ttk.Button(
            frame,
            text="Return to Job",
            style="Action.TButton",
            command=self.show_job_detail,
        ).pack(anchor="e", pady=(16, 0))


def main():
    """Create and run the native Windows Job Traveler GUI."""
    root = tk.Tk()
    JobTravelerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
