"""Native Tkinter interface for the Job Traveler workflow.

The GUI deliberately keeps the JSON contract owned by ``job_traveler.py``.
Business helpers in this module do not call terminal ``input()`` functions, so
they can also be exercised safely by automated tests.
"""

from __future__ import annotations

import copy
import html
import io
import json
import os
import re
import tempfile
import tkinter as tk
import webbrowser
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from zoneinfo import ZoneInfo

import job_traveler as terminal_app
from gui_theme import (
    BACKGROUND,
    BORDER,
    FAIL,
    MUTED_TEXT,
    PASS,
    PREVIEW_TABLE_HEIGHT,
    SPACE_CONTROL,
    SPACE_GROUP,
    SPACE_SECTION,
    SPACE_TIGHT,
    apply_shopos_theme,
    style_text_widget,
)


# Search for "CUSTOMIZE:" in this file and gui_theme.py to find the editable
# appearance settings without changing traveler workflow logic.


APP_TITLE = "JOB TRAVELER"
BASE_DIR = Path(__file__).resolve().parent
SHOP_TIMEZONE = ZoneInfo("America/New_York")
REPORT_COMPANY_NAME = "Red Ribbon ShopOS"
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


def status_label_style(value):
    """Return a semantic label style without changing saved status values."""
    normalized = clean_text(value).casefold()
    if normalized in {"completed", "complete", "pass", "passed"}:
        return "Success.TLabel"
    if normalized in {"failed", "fail", "rejected"}:
        return "Fail.TLabel"
    return "Status.TLabel"


def status_badge_style(value):
    """Map saved workflow labels to TradingView-style status badges."""
    normalized = clean_text(value).casefold()
    if normalized in {"completed", "complete", "pass", "passed"}:
        return "Completed.Badge.TLabel"
    if normalized in {"in progress", "active", "running"}:
        return "Progress.Badge.TLabel"
    if normalized in {"failed", "fail", "rejected", "blocked"}:
        return "Failed.Badge.TLabel"
    if normalized in {"pending", "warning"}:
        return "Pending.Badge.TLabel"
    return "Unknown.Badge.TLabel"


def display_value(value, missing="—"):
    """Return a compact GUI value without terminal underscore placeholders."""
    if value is None or value == "" or value == terminal_app.BLANK:
        return missing
    return str(value)


def format_shop_timestamp(value):
    """Format aware UTC or legacy Florida-local timestamps for GUI display."""
    text = clean_text(value)
    if not text:
        return "—"
    parsed = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        return "—"
    # Legacy Job Traveler timestamps came from datetime.now() on the Florida
    # workstation, so naive values represent Eastern wall time rather than UTC.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHOP_TIMEZONE)
    else:
        parsed = parsed.astimezone(SHOP_TIMEZONE)
    hour = parsed.strftime("%I").lstrip("0") or "12"
    return f"{parsed:%b} {parsed.day}, {parsed.year} at {hour}:{parsed:%M %p %Z}"


def format_shop_date(value):
    """Format ISO ship dates while preserving already-readable operator text."""
    text = clean_text(value)
    if not text:
        return "—"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(SHOP_TIMEZONE)
    return f"{parsed:%b} {parsed.day}, {parsed.year}"


def _print_value(value, missing="Not recorded"):
    """Escape one job-data value for safe insertion into the print HTML."""
    if value == "—":
        value = None
    shown = display_value(value, missing)
    return html.escape(shown, quote=True)


def _print_timestamp(value):
    shown = format_shop_timestamp(value)
    return _print_value(None if shown == "—" else shown)


def _print_status(value, missing="Pending"):
    """Render an escaped textual status that remains clear in grayscale."""
    shown = display_value(value, missing)
    normalized = clean_text(shown).casefold()
    if normalized == "completed":
        css_class = "status-completed"
    elif normalized == "in progress":
        css_class = "status-progress"
    elif normalized in {"failed", "fail", "rejected", "blocked"}:
        css_class = "status-failed"
    else:
        css_class = "status-pending"
    return f'<span class="status {css_class}">{html.escape(shown, quote=True)}</span>'


def _print_field_grid(fields):
    return "".join(
        '<div class="field"><div class="field-label">'
        f"{html.escape(label, quote=True)}</div>"
        f'<div class="field-value">{_print_value(value)}</div></div>'
        for label, value in fields
    )


def _print_table(headers, rows, status_index=None, status_missing="Pending"):
    head = "".join(f"<th>{html.escape(label, quote=True)}</th>" for label in headers)
    body_rows = []
    for row in rows:
        cells = []
        for index, value in enumerate(row):
            rendered = (
                _print_status(value, missing=status_missing)
                if index == status_index else _print_value(value)
            )
            cells.append(f"<td>{rendered}</td>")
        body_rows.append(f"<tr>{''.join(cells)}</tr>")
    if not body_rows:
        body_rows.append(
            f'<tr><td class="empty" colspan="{len(headers)}">Not recorded</td></tr>'
        )
    return (
        '<div class="table-wrap"><table><thead><tr>' + head + "</tr></thead><tbody>"
        + "".join(body_rows) + "</tbody></table></div>"
    )


def build_traveler_print_html(job, generated_at=None):
    """Build the single browser-preview and paper-print source document."""
    normalized = normalized_job(job)
    generated_at = generated_at or datetime.now(SHOP_TIMEZONE)
    generated_text = format_shop_timestamp(
        generated_at.isoformat() if isinstance(generated_at, datetime) else generated_at
    )
    job_number = display_value(normalized.get("job_number"), "Unknown Job")
    programming = normalized["programming"]
    revisions = []
    for operation in programming["operations"]:
        revision = clean_text(operation.get("revision"))
        if revision and revision not in revisions:
            revisions.append(revision)
    revision_text = ", ".join(revisions) if revisions else None

    programming_rows = [
        (
            row.get("operation_number"), row.get("operation_type"),
            row.get("program_name"), row.get("revision"), row.get("status"),
            format_shop_timestamp(row.get("last_updated")), row.get("notes"),
        )
        for row in programming["operations"]
    ]
    programming_html = (
        '<section><div class="section-heading"><h2>Programming</h2>'
        f'{_print_status(terminal_app.status_if_missing(normalized, "programming"))}</div>'
        f'<div class="field-grid compact">{_print_field_grid((("Programmer", programming.get("programmer")), ("Operations Required", programming.get("operation_count"))))}</div>'
        + _print_table(
            ("Op", "Type", "Program", "Revision", "Status", "Last Updated", "Notes"),
            programming_rows,
            status_index=4,
        ) + "</section>"
    )

    cnc = normalized["cnc_machining"]
    programming_by_number = {
        row.get("operation_number"): row for row in programming["operations"]
    }
    cnc_rows = [
        (
            row.get("operation_number"),
            programming_by_number.get(row.get("operation_number"), {}).get("operation_type"),
            row.get("operator"), row.get("machine"), row.get("qty_complete"),
            row.get("status"), format_shop_timestamp(row.get("last_updated")), row.get("notes"),
        )
        for row in cnc["operations"]
    ]
    cnc_html = (
        '<section><div class="section-heading"><h2>CNC Machining</h2>'
        f'{_print_status(terminal_app.status_if_missing(normalized, "cnc_machining"))}</div>'
        + _print_table(
            ("Op", "Type", "Operator", "Machine", "Qty", "Status", "Last Updated", "Notes"),
            cnc_rows,
            status_index=5,
        ) + "</section>"
    )

    simple_specs = (
        ("Saw Cutting", "saw_cutting", (("Employee", "employee"), ("Quantity Cut", "qty_cut"),
         ("Cut Length", "cut_length"), ("Scrap Quantity", "scrap_qty"),
         ("Last Updated", "last_updated"), ("Notes", "notes"))),
        ("Deburr", "deburr", (("Employee", "employee"), ("Deburr Needed", "deburr_needed"),
         ("Quantity Deburred", "qty_deburred"), ("Last Updated", "last_updated"), ("Notes", "notes"))),
        ("Packing", "packing", (("Employee", "employee"), ("Quantity Packed", "qty_packed"),
         ("Box Count", "box_count"), ("Last Updated", "last_updated"), ("Notes", "notes"))),
        ("Shipping", "shipping", (("Employee", "employee"), ("Ship Date", "ship_date"),
         ("Carrier", "carrier"), ("Tracking", "tracking"),
         ("Last Updated", "last_updated"), ("Notes", "notes"))),
    )
    simple_html = {}
    for title, key, specs in simple_specs:
        section = normalized[key]
        fields = tuple(
            (
                label,
                format_shop_timestamp(section.get(field))
                if field == "last_updated"
                else format_shop_date(section.get(field))
                if field == "ship_date"
                else section.get(field),
            )
            for label, field in specs
        )
        simple_html[key] = (
            '<section><div class="section-heading">'
            f"<h2>{html.escape(title)}</h2>"
            f'{_print_status(terminal_app.status_if_missing(normalized, key))}</div>'
            f'<div class="field-grid">{_print_field_grid(fields)}</div></section>'
        )

    inspection = normalized["inspection"]
    inspection_parts = [
        '<section><div class="section-heading"><h2>Inspection</h2>',
        _print_status(terminal_app.status_if_missing(normalized, "inspection")),
        "</div>",
    ]
    records = inspection.get("records", [])
    if not records:
        inspection_parts.append('<p class="empty">Not recorded</p>')
    for record in records:
        inspection_parts.append(
            '<div class="inspection-record"><h3>Operation '
            f'{_print_value(record.get("operation_number"))}</h3><div class="field-grid">'
            + _print_field_grid(
                (("Operation Type", record.get("operation_type")), ("Machine", record.get("machine")),
                 ("Inspector", record.get("inspector")), ("Report Type", record.get("report_type")),
                 ("Status", record.get("status")),
                 ("Last Updated", format_shop_timestamp(record.get("last_updated"))),
                 ("Notes", record.get("notes")))
            ) + "</div>"
        )
        dimensions = []
        for dimension in record.get("dimensions", []):
            if isinstance(dimension, dict):
                dimensions.append(
                    (dimension.get("dimension_number"), dimension.get("target_dimension"),
                     dimension.get("tolerance"), dimension.get("finding"),
                     dimension.get("measurement_equipment_used", dimension.get("tool_used")),
                     dimension.get("result"))
                )
        inspection_parts.append(
            _print_table(
                ("Dim", "Target", "Tolerance", "Finding", "Equipment", "Result"),
                dimensions,
                status_index=5,
                status_missing="Not recorded",
            ) + "</div>"
        )
    inspection_parts.append("</section>")

    summary = _print_field_grid(
        (("Job Number", normalized.get("job_number")), ("Customer", normalized.get("customer")),
         ("Part Number", normalized.get("part_number")), ("Description", normalized.get("description")),
         ("Quantity to Make", normalized.get("qty_to_make")), ("Material", normalized.get("material")),
         ("Cut Length", normalized.get("cut_length")), ("Revision", revision_text),
         ("Generated", generated_text)))
    document_title = html.escape(f"Job Traveler {job_number}", quote=True)
    company = html.escape(REPORT_COMPANY_NAME, quote=True)
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{document_title}</title>
<style>
  @page {{ size: Letter; margin: 0.55in; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #17191f; color: #111; font: 10pt "Segoe UI", Arial, sans-serif; }}
  .toolbar {{ position: sticky; top: 0; z-index: 2; display: flex; gap: 10px; justify-content: center; padding: 12px; background: #11141b; border-bottom: 1px solid #30343d; }}
  .toolbar button {{ border: 1px solid #4b505c; background: #272b35; color: #fff; padding: 8px 18px; font: 600 10pt "Segoe UI", Arial, sans-serif; cursor: pointer; }}
  .toolbar .print {{ background: #2962ff; border-color: #2962ff; }}
  .sheet {{ width: 8.5in; min-height: 11in; margin: 24px auto; padding: 0.55in; background: #fff; box-shadow: 0 2px 18px rgba(0,0,0,.45); }}
  .report-header {{ display: flex; justify-content: space-between; gap: 20px; align-items: end; padding-bottom: 10px; border-bottom: 2px solid #111; }}
  .company {{ color: #555; font-size: 9pt; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }}
  h1 {{ margin: 2px 0 0; font-size: 21pt; letter-spacing: .04em; }}
  .job-mark {{ text-align: right; font-size: 11pt; font-weight: 700; }}
  .field-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px 16px; margin: 10px 0; }}
  .field-grid.compact {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .field {{ min-width: 0; }}
  .field-label {{ color: #555; font-size: 7.5pt; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }}
  .field-value {{ margin-top: 2px; overflow-wrap: anywhere; white-space: pre-wrap; }}
  section {{ margin-top: 14px; break-inside: auto; }}
  .section-heading {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px; padding: 5px 7px; background: #e7e7e7; border: 1px solid #777; break-after: avoid; }}
  h2 {{ margin: 0; font-size: 11pt; text-transform: uppercase; letter-spacing: .04em; }}
  h3 {{ margin: 10px 0 5px; font-size: 9.5pt; break-after: avoid; }}
  .status {{ display: inline-block; border: 1px solid #555; padding: 1px 5px; font-size: 7.5pt; font-weight: 700; text-transform: uppercase; white-space: nowrap; }}
  .status-completed {{ background: #ddd; }}
  .status-progress {{ background: #eee; border-style: double; }}
  .status-pending {{ background: #fff; border-style: dashed; }}
  .status-failed {{ background: #222; color: #fff; border-color: #000; }}
  .table-wrap {{ width: 100%; overflow: visible; }}
  table {{ width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 8pt; }}
  thead {{ display: table-header-group; }}
  th {{ background: #ededed; font-weight: 700; text-align: left; }}
  th, td {{ border: 1px solid #888; padding: 4px 5px; vertical-align: top; overflow-wrap: anywhere; white-space: pre-wrap; }}
  tr {{ break-inside: avoid; page-break-inside: avoid; }}
  .inspection-record {{ break-inside: auto; }}
  .empty {{ color: #666; font-style: italic; text-align: center; }}
  @media print {{
    html, body {{ background: #fff !important; }}
    .toolbar {{ display: none !important; }}
    .sheet {{ width: auto; min-height: auto; margin: 0; padding: 0; box-shadow: none; }}
    section, table {{ orphans: 2; widows: 2; }}
  }}
</style>
</head>
<body>
<div class="toolbar" aria-label="Print preview controls">
  <button class="print" type="button" onclick="window.print()">Print Report</button>
  <button type="button" onclick="window.close()">Close</button>
</div>
<main class="sheet">
  <header class="report-header"><div><div class="company">{company}</div><h1>JOB TRAVELER</h1></div><div class="job-mark">Job {_print_value(normalized.get("job_number"))}<br>{_print_status(current_job_status(normalized))}</div></header>
  <div class="field-grid">{summary}</div>
  {programming_html}
  {simple_html["saw_cutting"]}
  {cnc_html}
  {simple_html["deburr"]}
  {''.join(inspection_parts)}
  {simple_html["packing"]}
  {simple_html["shipping"]}
</main>
</body>
</html>'''


def write_traveler_print_preview(job, directory=None, generated_at=None):
    """Write a browser-safe temporary preview without modifying traveler JSON."""
    job_number = re.sub(
        r"[^A-Za-z0-9_.-]+", "-", display_value(job.get("job_number"), "job")
    )[:80] or "job"
    destination_directory = Path(directory) if directory is not None else Path(tempfile.gettempdir())
    destination_directory.mkdir(parents=True, exist_ok=True)
    destination = destination_directory / f"Job-Traveler-{job_number}.html"
    destination.write_text(
        build_traveler_print_html(job, generated_at=generated_at), encoding="utf-8"
    )
    return destination


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
        self.canvas = tk.Canvas(self, highlightthickness=0, background=BACKGROUND)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.content = ttk.Frame(
            self.canvas, style="App.TFrame", padding=(SPACE_SECTION, SPACE_GROUP)
        )
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
        # ===== CUSTOMIZE: MAIN WINDOW =====
        # CUSTOMIZE: Geometry is width x height; minsize prevents form clipping.
        self.root.geometry("1100x760")
        self.root.minsize(900, 620)
        self.root.configure(background=BACKGROUND)
        self.root.protocol("WM_DELETE_WINDOW", self.close_application)
        self._configure_styles()
        self.show_home()

    def _configure_styles(self):
        self.style = apply_shopos_theme(self.root)

    def clear_screen(self):
        for child in self.root.winfo_children():
            child.destroy()

    def page_header(self, parent, title, subtitle=""):
        ttk.Label(parent, text=title, style="Heading.TLabel").pack(anchor="w")
        if subtitle:
            ttk.Label(parent, text=subtitle, style="Subheading.TLabel").pack(
                anchor="w", pady=(4, SPACE_SECTION)
            )
        else:
            ttk.Separator(parent).pack(fill="x", pady=(SPACE_TIGHT, SPACE_SECTION))

    def close_application(self):
        self.root.destroy()

    def show_home(self):
        self.clear_screen()
        frame = ttk.Frame(self.root, style="App.TFrame", padding=SPACE_SECTION)
        frame.pack(fill="both", expand=True)
        center = ttk.Frame(frame, style="App.TFrame")
        center.place(relx=0.5, rely=0.44, anchor="center")
        ttk.Label(center, text=APP_TITLE, style="Title.TLabel").pack(pady=(0, 8))
        ttk.Label(
            center,
            text="Create and manage CNC shop travelers",
            style="Subheading.TLabel",
        ).pack(pady=(0, SPACE_SECTION))
        ttk.Button(
            center,
            text="Create New Job",
            command=self.show_create_job,
            style="Primary.TButton",
            width=28,
        ).pack(fill="x", pady=6)
        ttk.Button(
            center,
            text="Open Existing Job",
            command=self.show_job_list,
            style="Primary.TButton",
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
        form = ttk.LabelFrame(content, text="Job Information", padding=SPACE_GROUP)
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
            style="Primary.TButton",
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
        frame = ttk.Frame(self.root, style="App.TFrame", padding=SPACE_SECTION)
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
                style="Fail.TLabel",
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
            buttons, text="Open Selected", style="Primary.TButton", command=open_selected
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

    def traveler_view_actions(self):
        """Keep screen viewing and paper preview as explicitly separate actions."""
        return (
            ("View Traveler", self.show_traveler_preview),
            ("Print Traveler", self.print_traveler),
        )

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
            value_style = status_label_style(value) if key is None else "Value.TLabel"
            ttk.Label(cell, text=str(value), style=value_style, wraplength=330).pack(
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
        ) + self.traveler_view_actions()
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
            section_status = terminal_app.status_if_missing(self.current_job, section)
            ttk.Label(
                statuses,
                text=section_status,
                style=status_label_style(section_status),
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
            style="Primary.TButton",
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
        form = ttk.LabelFrame(content, text=title, padding=SPACE_GROUP)
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
                widget = tk.Text(form, height=5, width=62, wrap="word")
                style_text_widget(widget)
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
            buttons, text="Save Section", style="Primary.TButton", command=save_section
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
        form = ttk.LabelFrame(content, text="Programming", padding=SPACE_GROUP)
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
                        )
                        style_text_widget(widget)
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
            buttons, text="Save Section", style="Primary.TButton", command=save_section
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

        form = ttk.LabelFrame(frame, text="CNC Workflow", padding=SPACE_GROUP)
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
        notes = tk.Text(form, height=4, width=60, wrap="word")
        style_text_widget(notes)
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
            saved_status = saved.get("status", "Pending")
            status_value.configure(
                text=saved_status, style=status_label_style(saved_status)
            )
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
            buttons, text="Save Section", style="Primary.TButton", command=save_section
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
        form = ttk.LabelFrame(content, text="Inspection Header", padding=SPACE_GROUP)
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
        warning_value = ttk.Label(form, text="", style="Warning.TLabel")
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
        notes = tk.Text(form, height=6, width=60, wrap="word")
        style_text_widget(notes)
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
            buttons, text="Save Section", style="Primary.TButton", command=save_section
        ).pack(side="right")
        ttk.Button(
            buttons,
            text="Save & Edit Dimensions",
            style="Primary.TButton",
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
        tree.tag_configure("pass", foreground=PASS)
        tree.tag_configure("rejected", foreground=FAIL)
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
                result_value = row.get("result", "")
                result_tag = clean_text(result_value).casefold()
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
                        result_value,
                    ),
                    tags=(result_tag,) if result_tag in {"pass", "rejected"} else (),
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
            dimension_buttons,
            text="Add Dimension",
            style="Primary.TButton",
            command=add_dimension,
        ).pack(side="left")
        ttk.Button(
            dimension_buttons,
            text="Remove Selected",
            style="Danger.TButton",
            command=remove_selected,
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
            style="Primary.TButton",
            command=save_dimensions,
        ).pack(side="right")
        ttk.Button(
            buttons,
            text="Cancel Changes",
            style="Action.TButton",
            command=self.show_job_detail,
        ).pack(side="right", padx=(0, 10))

    def _preview_panel(self, parent, title, status):
        """Create one dense traveler department panel."""
        panel = ttk.Frame(parent, style="Panel.TFrame", padding=SPACE_GROUP)
        panel.pack(fill="x", pady=(0, SPACE_CONTROL))
        heading = ttk.Frame(panel, style="Surface.TFrame")
        heading.pack(fill="x", pady=(0, SPACE_CONTROL))
        ttk.Label(heading, text=title, style="PanelTitle.TLabel").pack(side="left")
        ttk.Label(
            heading, text=display_value(status, "Pending"), style=status_badge_style(status)
        ).pack(side="right")
        return panel

    def _preview_fields(self, parent, fields, columns=2):
        """Render label/value pairs with wrapping for long traveler content."""
        grid = ttk.Frame(parent, style="Surface.TFrame")
        grid.pack(fill="x")
        for index, (label, value) in enumerate(fields):
            row, column = divmod(index, columns)
            cell = ttk.Frame(grid, style="Surface.TFrame", padding=(0, 0, SPACE_GROUP, SPACE_CONTROL))
            cell.grid(row=row, column=column, sticky="nsew")
            ttk.Label(cell, text=label.upper(), style="PanelHeading.TLabel").pack(anchor="w")
            shown = display_value(value)
            ttk.Label(
                cell,
                text=shown,
                style="Muted.TLabel" if shown == "—" else "Value.TLabel",
                wraplength=430,
                justify="left",
            ).pack(anchor="w", pady=(3, 0))
        for column in range(columns):
            grid.columnconfigure(column, weight=1, uniform="preview_fields")
        return grid

    def _enable_tree_hover(self, tree):
        """Highlight the table row beneath the pointer without changing selection."""
        tree.tag_configure("hover", background=BORDER)
        hovered = {"item": None}

        def move(event):
            item = tree.identify_row(event.y)
            if item == hovered["item"]:
                return
            if hovered["item"] and tree.exists(hovered["item"]):
                tags = tuple(tag for tag in tree.item(hovered["item"], "tags") if tag != "hover")
                tree.item(hovered["item"], tags=tags)
            hovered["item"] = item or None
            if item:
                tree.item(item, tags=(*tree.item(item, "tags"), "hover"))

        def leave(_event=None):
            if hovered["item"] and tree.exists(hovered["item"]):
                tags = tuple(tag for tag in tree.item(hovered["item"], "tags") if tag != "hover")
                tree.item(hovered["item"], tags=tags)
            hovered["item"] = None

        tree.bind("<Motion>", move, add="+")
        tree.bind("<Leave>", leave, add="+")

    def _preview_table(self, parent, columns, rows, *, status_index=None, horizontal=True):
        """Render structured rows in a keyboard-focusable ttk table."""
        table_frame = ttk.Frame(parent, style="Surface.TFrame")
        table_frame.pack(fill="x", pady=(SPACE_TIGHT, 0))
        keys = tuple(f"column_{index}" for index in range(len(columns)))
        # ===== CUSTOMIZE: PREVIEW TABLES =====
        # CUSTOMIZE: PREVIEW_TABLE_HEIGHT controls visible rows before page scrolling.
        tree = ttk.Treeview(
            table_frame,
            columns=keys,
            show="headings",
            height=max(1, min(PREVIEW_TABLE_HEIGHT, len(rows) or 1)),
            selectmode="browse",
        )
        for key, (heading, width) in zip(keys, columns):
            tree.heading(key, text=heading)
            tree.column(key, width=width, minwidth=60, stretch=heading in {"Notes", "Description"})
        tree.tag_configure("completed", foreground=PASS)
        tree.tag_configure("failed", foreground=FAIL)
        tree.tag_configure("pending", foreground=MUTED_TEXT)
        tree.tag_configure("progress", foreground="#D1D4DC")
        for raw_row in rows:
            shown = tuple(display_value(value) for value in raw_row)
            tags = ()
            if status_index is not None and status_index < len(raw_row):
                status = clean_text(raw_row[status_index]).casefold()
                if status in {"completed", "pass", "passed"}:
                    tags = ("completed",)
                elif status in {"failed", "fail", "rejected", "blocked"}:
                    tags = ("failed",)
                elif status == "in progress":
                    tags = ("progress",)
                else:
                    tags = ("pending",)
            tree.insert("", "end", values=shown, tags=tags)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=vertical.set)
        if horizontal:
            horizontal_bar = ttk.Scrollbar(table_frame, orient="horizontal", command=tree.xview)
            horizontal_bar.grid(row=1, column=0, sticky="ew")
            tree.configure(xscrollcommand=horizontal_bar.set)
        table_frame.columnconfigure(0, weight=1)
        self._enable_tree_hover(tree)
        return tree

    def show_traveler_preview(self):
        """Render the traveler from structured data; printing still uses traveler_text()."""
        self.clear_screen()
        normalized = normalized_job(self.current_job)
        scroll = ScrollableFrame(self.root)
        scroll.pack(fill="both", expand=True)
        content = scroll.content

        # ===== CUSTOMIZE: TRAVELER HEADER =====
        # CUSTOMIZE: Header padding and spacing control the preview's overall density.
        header = ttk.Frame(content, style="Panel.TFrame", padding=SPACE_GROUP)
        header.pack(fill="x", pady=(0, SPACE_CONTROL))
        title_row = ttk.Frame(header, style="Surface.TFrame")
        title_row.pack(fill="x")
        ttk.Label(title_row, text="JOB TRAVELER", style="PanelTitle.TLabel").pack(side="left")
        overall_status = current_job_status(normalized)
        ttk.Label(
            title_row, text=overall_status, style=status_badge_style(overall_status)
        ).pack(side="right")
        ttk.Label(
            header,
            text=f"JOB {display_value(normalized.get('job_number'))}",
            style="PreviewTitle.TLabel",
        ).pack(anchor="w", pady=(SPACE_CONTROL, 2))
        ttk.Label(
            header,
            text=f"Part {display_value(normalized.get('part_number'))}",
            style="PreviewSubtitle.TLabel",
        ).pack(anchor="w")
        self._preview_fields(
            header,
            (
                ("Customer", normalized.get("customer")),
                ("Description", normalized.get("description")),
                ("Quantity", normalized.get("qty_to_make")),
                ("Material", normalized.get("material")),
                ("Cut Length", normalized.get("cut_length")),
            ),
        )

        programming = normalized["programming"]
        panel = self._preview_panel(
            content, "Programming", terminal_app.status_if_missing(normalized, "programming")
        )
        self._preview_fields(
            panel,
            (("Programmer", programming.get("programmer")),
             ("Operations Required", programming.get("operation_count"))),
        )
        programming_rows = [
            (
                row.get("operation_number"), row.get("operation_type"),
                row.get("program_name"), row.get("revision"), row.get("status"),
                format_shop_timestamp(row.get("last_updated")), row.get("notes"),
            )
            for row in programming["operations"]
        ]
        self._preview_table(
            panel,
            (("Op", 60), ("Type", 100), ("Program", 150), ("Rev", 70),
             ("Status", 110), ("Updated", 210), ("Notes", 260)),
            programming_rows,
            status_index=4,
        )

        simple_sections = (
            ("Saw Cutting", "saw_cutting", (("Employee", "employee"), ("Quantity Cut", "qty_cut"),
             ("Cut Length", "cut_length"), ("Scrap Quantity", "scrap_qty"),
             ("Last Updated", "last_updated"), ("Notes", "notes"))),
            ("Deburr", "deburr", (("Employee", "employee"), ("Deburr Needed", "deburr_needed"),
             ("Quantity Deburred", "qty_deburred"), ("Last Updated", "last_updated"),
             ("Notes", "notes"))),
            ("Packing", "packing", (("Employee", "employee"), ("Quantity Packed", "qty_packed"),
             ("Box Count", "box_count"), ("Last Updated", "last_updated"), ("Notes", "notes"))),
            ("Shipping", "shipping", (("Employee", "employee"), ("Ship Date", "ship_date"),
             ("Carrier", "carrier"), ("Tracking", "tracking"),
             ("Last Updated", "last_updated"), ("Notes", "notes"))),
        )
        for title, key, field_specs in simple_sections[:1]:
            section = normalized[key]
            panel = self._preview_panel(content, title, terminal_app.status_if_missing(normalized, key))
            self._preview_fields(panel, tuple(
                (label, format_shop_timestamp(section.get(field)) if field == "last_updated" else section.get(field))
                for label, field in field_specs
            ))

        cnc = normalized["cnc_machining"]
        panel = self._preview_panel(
            content, "CNC Machining", terminal_app.status_if_missing(normalized, "cnc_machining")
        )
        programming_by_number = {
            row.get("operation_number"): row for row in programming["operations"]
        }
        cnc_rows = [
            (
                row.get("operation_number"),
                programming_by_number.get(row.get("operation_number"), {}).get("operation_type"),
                row.get("operator"), row.get("machine"), row.get("qty_complete"),
                row.get("status"), format_shop_timestamp(row.get("last_updated")), row.get("notes"),
            )
            for row in cnc["operations"]
        ]
        self._preview_table(
            panel,
            (("Op", 60), ("Type", 90), ("Operator", 130), ("Machine", 170),
             ("Qty", 75), ("Status", 110), ("Updated", 210), ("Notes", 250)),
            cnc_rows,
            status_index=5,
        )

        for title, key, field_specs in simple_sections[1:2]:
            section = normalized[key]
            panel = self._preview_panel(content, title, terminal_app.status_if_missing(normalized, key))
            self._preview_fields(panel, tuple(
                (label, format_shop_timestamp(section.get(field)) if field == "last_updated" else section.get(field))
                for label, field in field_specs
            ))

        inspection = normalized["inspection"]
        panel = self._preview_panel(
            content, "Inspection", terminal_app.status_if_missing(normalized, "inspection")
        )
        records = inspection.get("records", [])
        if not records:
            ttk.Label(panel, text="No inspection recorded", style="Muted.TLabel").pack(anchor="w")
        for record_index, record in enumerate(records):
            if record_index:
                ttk.Separator(panel).pack(fill="x", pady=SPACE_CONTROL)
            self._preview_fields(
                panel,
                (("Operation", record.get("operation_number")),
                 ("Operation Type", record.get("operation_type")),
                 ("Machine", record.get("machine")), ("Inspector", record.get("inspector")),
                 ("Report Type", record.get("report_type")), ("Status", record.get("status")),
                 ("Last Updated", format_shop_timestamp(record.get("last_updated"))),
                 ("Notes", record.get("notes"))),
            )
            dimension_rows = []
            for dimension in record.get("dimensions", []):
                if not isinstance(dimension, dict):
                    continue
                dimension_rows.append(
                    (dimension.get("dimension_number"), dimension.get("target_dimension"),
                     dimension.get("tolerance"), dimension.get("finding"),
                     dimension.get("measurement_equipment_used", dimension.get("tool_used")),
                     dimension.get("result"))
                )
            if dimension_rows:
                self._preview_table(
                    panel,
                    (("Dim", 60), ("Target", 130), ("Tolerance", 130),
                     ("Finding", 130), ("Equipment", 190), ("Result", 100)),
                    dimension_rows,
                    status_index=5,
                    horizontal=False,
                )

        for title, key, field_specs in simple_sections[2:]:
            section = normalized[key]
            panel = self._preview_panel(content, title, terminal_app.status_if_missing(normalized, key))
            self._preview_fields(panel, tuple(
                (label, format_shop_timestamp(section.get(field)) if field == "last_updated" else section.get(field))
                for label, field in field_specs
            ))

        ttk.Button(
            content,
            text="Return to Job",
            style="Action.TButton",
            command=self.show_job_detail,
        ).pack(anchor="e", pady=(SPACE_TIGHT, SPACE_SECTION))

    def print_traveler(self):
        """Open the exact paper source in the default browser for preview/printing."""
        if self.current_job is None:
            return
        try:
            preview_path = write_traveler_print_preview(self.current_job)
            opened = webbrowser.open_new_tab(preview_path.resolve().as_uri())
            if not opened:
                raise OSError("The default browser did not accept the print preview.")
        except (OSError, TypeError, ValueError) as error:
            messagebox.showerror(
                "Print Preview Error",
                f"Could not open the traveler print preview:\n\n{error}",
                parent=self.root,
            )


def main():
    """Create and run the native Windows Job Traveler GUI."""
    root = tk.Tk()
    JobTravelerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
