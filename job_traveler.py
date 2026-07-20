# job_traveler.py
import copy
import getpass
import os
from datetime import datetime
from pathlib import Path

JOBS_FOLDER = "jobs"

from traveler_persistence import (
    MODE_ENV,
    PersistenceConflict,
    PersistenceError,
    PlanResizeConfirmationRequired,
    PlanResizeConflict,
    TravelerPersistence,
    build_persistence,
    set_conflict_value,
)
from desktop_session import (
    DesktopSessionManager,
    SessionConfigurationError,
    build_session_manager_from_environment,
)
from traveler_client import TravelerClientError

from traveler_domain import (  # compatibility re-exports for existing callers
    ALLOWED_OPERATIONS,
    ALLOWED_STATUSES,
    ALL_MACHINES,
    BLANK,
    MILLING_MACHINES,
    SECTIONS,
    TURNING_MACHINES,
    blank_cnc_operation,
    blank_if_missing,
    blank_programming_operation,
    canonical_job,
    get_cnc_status,
    get_required_quantity,
    infer_operation_type,
    job_field,
    normalize_operations,
    operation_by_number,
    operation_has_data,
    operation_if_missing,
    resize_operation_plan,
    status_if_missing,
)


_configured_persistence = None
_loaded_snapshots = {}


def configure_persistence(persistence):
    """Inject one explicit persistence implementation for the terminal session."""
    global _configured_persistence
    if not isinstance(persistence, TravelerPersistence):
        raise TypeError("persistence must implement TravelerPersistence")
    if _configured_persistence is not persistence:
        _loaded_snapshots.clear()
    _configured_persistence = persistence


def _jobs_directory():
    configured = Path(JOBS_FOLDER)
    return (
        configured
        if configured.is_absolute()
        else Path(__file__).resolve().parent / configured
    )


def _active_persistence(persistence=None):
    if persistence is not None:
        if not isinstance(persistence, TravelerPersistence):
            raise TypeError("persistence must implement TravelerPersistence")
        return persistence
    if _configured_persistence is not None:
        return _configured_persistence
    selected_mode = os.environ.get(MODE_ENV, "local").casefold()
    jobs_directory = _jobs_directory() if selected_mode == "local" else None
    return build_persistence(jobs_directory=jobs_directory)


def get_int(prompt):
    while True:
        raw = input(prompt).strip()
        try:
            return int(raw)
        except ValueError:
            print("Invalid number. Enter a whole number.")


def get_positive_int(prompt, current=None):
    while True:
        suffix = f" [current: {current}]" if current is not None else ""
        raw = input(f"{prompt}{suffix}: ").strip()
        if raw == "" and current is not None:
            return current
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value > 0 and str(value) == raw.lstrip("+"):
            return value
        print("Invalid number. Enter a positive whole number.")


def get_yes_or_no(prompt):
    while True:
        choice = input(prompt).strip().lower()

        if choice == "y":
            return True
        if choice == "n":
            return False

        print("Invalid choice. Please enter y or n.")


def get_timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def has_existing_value(section_data, key):
    value = section_data.get(key)
    return value != "" and value is not None


def get_existing_or_new(section_data, key, prompt):
    if has_existing_value(section_data, key):
        current_value = section_data[key]
        raw = input(f"{prompt} [current: {current_value}]: ").strip()

        if raw == "":
            return current_value

        return raw

    return input(f"{prompt}: ").strip()


def get_status(section_data):
    current_status = section_data.get("status")

    while True:
        print("\nStatus")

        if has_existing_value(section_data, "status"):
            print(f"Current status: {current_status}")

            if current_status in ALLOWED_STATUSES:
                print("Press Enter to keep the current status.")
            else:
                print("Choose one of the allowed statuses below.")

        print("1. Pending")
        print("2. In Progress")
        print("3. Completed")

        choice = input("Choose a status: ").strip()

        if choice == "" and current_status in ALLOWED_STATUSES:
            return current_status

        if choice == "1":
            return "Pending"
        if choice == "2":
            return "In Progress"
        if choice == "3":
            return "Completed"

        print("Invalid choice. Please choose 1, 2, or 3.")


def get_operation(section_data):
    current_operation = section_data.get("operation")

    while True:
        print("\nOperation")

        if has_existing_value(section_data, "operation"):
            print(f"Current operation: {current_operation}")

            if current_operation in ALLOWED_OPERATIONS:
                print("Press Enter to keep the current operation.")
            else:
                print("Choose one of the allowed operations below.")

        print("1. Mill")
        print("2. Turning")

        choice = input("Choose an operation: ").strip()

        if choice == "" and current_operation in ALLOWED_OPERATIONS:
            return current_operation

        if choice == "1":
            return "Mill"
        if choice == "2":
            return "Turning"

        print("Invalid choice. Please choose 1 or 2.")


def get_machine_for_operation(section_data, operation):
    current_machine = section_data.get("machine")

    if operation == "Mill":
        machines = MILLING_MACHINES
    else:
        machines = TURNING_MACHINES

    while True:
        print("\nMachine")

        if has_existing_value(section_data, "machine"):
            print(f"Current machine: {current_machine}")

            if current_machine in machines:
                print("Press Enter to keep the current machine.")
            else:
                print("Choose one of the allowed machines below.")

        for index, machine in enumerate(machines, start=1):
            print(f"{index}. {machine}")

        choice = input("Choose a machine: ").strip()

        if choice == "" and current_machine in machines:
            return current_machine

        try:
            choice_number = int(choice)
        except ValueError:
            print("Invalid choice. Please choose a number from the list.")
            continue

        if 1 <= choice_number <= len(machines):
            return machines[choice_number - 1]

        print("Invalid choice. Please choose a number from the list.")


def choose_configured_operation(programming_operations):
    while True:
        print("\nConfigured Operations")
        for operation in programming_operations:
            print(
                f"{operation['operation_number']}. Operation "
                f"{operation['operation_number']} - "
                f"{operation.get('operation_type') or BLANK}"
            )
        choice = input("Choose an operation: ").strip()
        try:
            number = int(choice)
        except ValueError:
            number = 0
        if operation_by_number(programming_operations, number):
            return number
        print("Invalid choice. Choose a configured operation number.")


def get_dimension_result():
    while True:
        print("\nResult")
        print("1. Pass")
        print("2. Rejected")

        choice = input("Choose a result: ").strip()

        if choice == "1":
            return "Pass"
        if choice == "2":
            return "Rejected"

        print("Invalid choice. Please choose 1 or 2.")


def get_existing_int_or_new(section_data, key, prompt):
    if has_existing_value(section_data, key):
        current_value = section_data[key]

        while True:
            raw = input(f"{prompt} [current: {current_value}]: ").strip()

            if raw == "":
                return current_value

            try:
                return int(raw)
            except ValueError:
                print("Invalid number. Enter a whole number.")

    return get_int(f"{prompt}: ")


def _shown_conflict_value(value):
    shown = repr(value)
    return shown if len(shown) <= 240 else shown[:237] + "..."


def _resolve_terminal_conflict(persistence, conflict, intended, *, action):
    active_conflict = conflict
    while True:
        for field in active_conflict.conflicts:
            print(f"\nSave conflict in {field.label}")
            print(f"Your value: {_shown_conflict_value(field.intended_value)}")
            print(
                "Current authoritative value: "
                f"{_shown_conflict_value(field.authoritative_value)}"
            )
            while True:
                choice = input(
                    "Keep authoritative [Enter/k], deliberately replace [r], "
                    "or cancel [c]: "
                ).strip().casefold()
                if choice in {"", "k"}:
                    set_conflict_value(intended, field.path, field.authoritative_value)
                    break
                if choice == "r":
                    break
                if choice == "c":
                    return None
                print("Invalid choice. The default is to keep the authoritative value.")
        try:
            return persistence.resolve_conflict(
                active_conflict, intended, action=action
            )
        except PersistenceConflict as changed_again:
            active_conflict = changed_again


def save_job(job, persistence=None, *, action="logical_save"):
    """Save through the configured boundary and report success only if confirmed."""
    active = _active_persistence(persistence)
    intended = copy.deepcopy(job)
    tracked = _loaded_snapshots.get(id(job))
    base = tracked[1] if tracked is not None and tracked[0] is job else None
    try:
        result = (
            active.save(base, intended, action=action)
            if base is not None
            else active.create(intended, overwrite=True)
        )
    except PersistenceConflict as conflict:
        try:
            result = _resolve_terminal_conflict(
                active, conflict, intended, action=action
            )
        except PersistenceError as error:
            print(f"\nJob traveler was not saved: {error}")
            return False
        if result is None:
            print("\nSave canceled. No change was written.")
            return False
    except PersistenceError as error:
        print(f"\nJob traveler was not saved: {error}")
        return False

    job.clear()
    job.update(copy.deepcopy(result.snapshot.traveler))
    _loaded_snapshots[id(job)] = (job, result.snapshot)
    destination = (
        str(result.snapshot.location)
        if result.snapshot.location is not None
        else "the authenticated ShopOS service"
    )
    print(f"\nSaved job traveler to {destination}")
    return True


def load_job(job_number, persistence=None):
    active = _active_persistence(persistence)
    try:
        snapshot = active.load(str(job_number))
    except PersistenceError as error:
        print(f"\nNo job traveler found for job number {job_number}: {error}")
        return None
    job = copy.deepcopy(snapshot.traveler)
    _loaded_snapshots[id(job)] = (job, snapshot)
    return job


def list_existing_jobs(persistence=None):
    active = _active_persistence(persistence)
    try:
        summaries, _errors = active.list_summaries_with_errors()
    except PersistenceError as error:
        print(f"\nSaved job travelers could not be listed: {error}")
        return
    if not summaries:
        print("\nNo saved job travelers found.")
        return

    print("\nExisting Job Travelers")
    print("-" * 30)
    print("Job Number | Customer | Part Number | Qty To Make")
    for summary in summaries:
        print(
            f"{summary.job_number or BLANK} | "
            f"{summary.customer or BLANK} | "
            f"{summary.part_number or BLANK} | "
            f"Qty: {summary.quantity if summary.quantity not in ('', None) else BLANK}"
        )


def create_new_job():
    print("\nCreate New Job Traveler")
    print("-" * 30)

    job = {
        "job_number": input("Job Number: ").strip(),
        "customer": input("Customer: ").strip(),
        "part_number": input("Part Number: ").strip(),
        "description": input("Description: ").strip(),
        "qty_to_make": get_int("Qty To Make: "),
        "material": input("Material: ").strip(),
        "cut_length": input("Cut Length: ").strip(),
        "programming": {},
        "saw_cutting": {},
        "cnc_machining": {},
        "deburr": {},
        "inspection": {},
        "packing": {},
        "shipping": {},
    }

    return job if save_job(job) else None


def print_traveler(job):
    job = normalize_operations(job)
    print("\n" + "=" * 60)
    print("JOB TRAVELER")
    print("=" * 60)

    print(f"Job Number:   {job_field(job, 'job_number')}")
    print(f"Customer:     {job_field(job, 'customer')}")
    print(f"Part Number:  {job_field(job, 'part_number')}")
    print(f"Description:  {job_field(job, 'description')}")
    print(f"Qty To Make:  {job_field(job, 'qty_to_make')}")
    print(f"Material:     {job_field(job, 'material')}")
    print(f"Cut Length:   {job_field(job, 'cut_length')}")

    print("\n" + "-" * 60)
    print("PROGRAMMING")
    print("-" * 60)
    programming = job["programming"]
    print(f"Programmer:          {programming.get('programmer') or BLANK}")
    print(f"Operations Required: {programming['operation_count']}")
    print("OP | TYPE | PROGRAM | REVISION | STATUS | LAST UPDATED | NOTES")
    for operation in programming["operations"]:
        print(
            f"{operation['operation_number']} | "
            f"{operation.get('operation_type') or BLANK} | "
            f"{operation.get('program_name') or BLANK} | "
            f"{operation.get('revision') or BLANK} | "
            f"{operation.get('status') or 'Pending'} | "
            f"{operation.get('last_updated') or BLANK} | "
            f"{operation.get('notes') or BLANK}"
        )

    print("\n" + "-" * 60)
    print("SAW CUTTING")
    print("-" * 60)
    print(f"Employee:      {blank_if_missing(job, 'saw_cutting', 'employee')}")
    print(f"Qty Cut:       {blank_if_missing(job, 'saw_cutting', 'qty_cut')}")
    print(f"Cut Length:    {blank_if_missing(job, 'saw_cutting', 'cut_length')}")
    print(f"Status:        {status_if_missing(job, 'saw_cutting')}")
    print(f"Last Updated:  {blank_if_missing(job, 'saw_cutting', 'last_updated')}")
    print(f"Notes:         {blank_if_missing(job, 'saw_cutting', 'notes')}")

    print("\n" + "-" * 60)
    print("CNC MACHINING")
    print("-" * 60)
    print("OP | TYPE | OPERATOR | MACHINE | QTY COMPLETE | STATUS | LAST UPDATED | NOTES")
    for operation in job["cnc_machining"]["operations"]:
        programming_operation = operation_by_number(
            programming["operations"], operation["operation_number"]
        ) or {}
        print(
            f"{operation['operation_number']} | "
            f"{programming_operation.get('operation_type') or BLANK} | "
            f"{operation.get('operator') or BLANK} | "
            f"{operation.get('machine') or BLANK} | "
            f"{operation.get('qty_complete', 0)} | "
            f"{operation.get('status') or 'Pending'} | "
            f"{operation.get('last_updated') or BLANK} | "
            f"{operation.get('notes') or BLANK}"
        )

    print("\n" + "-" * 60)
    print("DEBURR")
    print("-" * 60)
    print(f"Employee:      {blank_if_missing(job, 'deburr', 'employee')}")
    print(f"Deburr Needed: {blank_if_missing(job, 'deburr', 'deburr_needed')}")
    print(f"Qty Deburred:  {blank_if_missing(job, 'deburr', 'qty_deburred')}")
    print(f"Status:        {status_if_missing(job, 'deburr')}")
    print(f"Last Updated:  {blank_if_missing(job, 'deburr', 'last_updated')}")
    print(f"Notes:         {blank_if_missing(job, 'deburr', 'notes')}")

    print("\n" + "-" * 60)
    print("INSPECTION")
    print("-" * 60)
    records = job["inspection"]["records"]
    if not records:
        print(f"Dimensions:    {BLANK}")
    for record in records:
        print(f"\nOperation Number: {record.get('operation_number', BLANK)}")
        print(f"Operation Type:   {record.get('operation_type') or BLANK}")
        print(f"Machine:          {record.get('machine') or BLANK}")
        print(f"Inspector:        {record.get('inspector') or BLANK}")
        print(f"Report Type:      {record.get('report_type') or BLANK}")
        print(f"Status:           {record.get('status') or 'Pending'}")
        print(f"Last Updated:     {record.get('last_updated') or BLANK}")
        print(f"Notes:            {record.get('notes') or BLANK}")
        dimensions = record.get("dimensions", [])
        if not isinstance(dimensions, list) or not dimensions:
            print(f"Dimensions:       {BLANK}")
            continue
        print("DIM | TARGET | TOLERANCE | FINDING | EQUIPMENT | RESULT")
        for dimension in dimensions:
            dimension = dimension if isinstance(dimension, dict) else {}
            equipment = dimension.get(
                "measurement_equipment_used", dimension.get("tool_used", BLANK)
            )
            print(
                f"{dimension.get('dimension_number', BLANK)} | "
                f"{dimension.get('target_dimension', BLANK)} | "
                f"{dimension.get('tolerance', BLANK)} | "
                f"{dimension.get('finding', BLANK)} | {equipment} | "
                f"{dimension.get('result', BLANK)}"
            )

    print("\n" + "-" * 60)
    print("PACKING")
    print("-" * 60)
    print(f"Employee:      {blank_if_missing(job, 'packing', 'employee')}")
    print(f"Qty Packed:    {blank_if_missing(job, 'packing', 'qty_packed')}")
    print(f"Box Count:     {blank_if_missing(job, 'packing', 'box_count')}")
    print(f"Status:        {status_if_missing(job, 'packing')}")
    print(f"Last Updated:  {blank_if_missing(job, 'packing', 'last_updated')}")
    print(f"Notes:         {blank_if_missing(job, 'packing', 'notes')}")

    print("\n" + "-" * 60)
    print("SHIPPING")
    print("-" * 60)
    print(f"Employee:      {blank_if_missing(job, 'shipping', 'employee')}")
    print(f"Ship Date:     {blank_if_missing(job, 'shipping', 'ship_date')}")
    print(f"Carrier:       {blank_if_missing(job, 'shipping', 'carrier')}")
    print(f"Tracking:      {blank_if_missing(job, 'shipping', 'tracking')}")
    print(f"Status:        {status_if_missing(job, 'shipping')}")
    print(f"Last Updated:  {blank_if_missing(job, 'shipping', 'last_updated')}")
    print(f"Notes:         {blank_if_missing(job, 'shipping', 'notes')}")


def print_job_status_summary(job):
    print("\nJOB STATUS SUMMARY")
    print("-" * 30)
    print(f"Job Number:   {job_field(job, 'job_number')}")
    print(f"Customer:     {job_field(job, 'customer')}")
    print(f"Part Number:  {job_field(job, 'part_number')}")
    print(f"Qty To Make:  {job_field(job, 'qty_to_make')}")

    print()
    print(f"Programming:    {status_if_missing(job, 'programming')}")
    print(f"Saw Cutting:    {status_if_missing(job, 'saw_cutting')}")
    print(f"CNC Machining:  {status_if_missing(job, 'cnc_machining')}")
    print(f"Deburr:         {status_if_missing(job, 'deburr')}")
    print(f"Inspection:     {status_if_missing(job, 'inspection')}")
    print(f"Packing:        {status_if_missing(job, 'packing')}")
    print(f"Shipping:       {status_if_missing(job, 'shipping')}")


def update_programming(job):
    print("\nUpdate Programming")
    print("-" * 30)

    normalized = normalize_operations(job)
    programming = normalized["programming"]
    programmer = get_existing_or_new(programming, "programmer", "Programmer Name")
    operation_count = get_positive_int(
        "Number of Operations Required", programming["operation_count"]
    )
    if operation_count < programming["operation_count"]:
        try:
            candidate = resize_operation_plan(normalized, operation_count)
        except ValueError as error:
            if "Confirm removal" not in str(error):
                print(f"\n{error}")
                return False
            if not get_yes_or_no("Remove the unused blank operation(s)? (y/n): "):
                print("Operation count was not changed.")
                return False
            candidate = resize_operation_plan(normalized, operation_count, True)
    else:
        candidate = resize_operation_plan(normalized, operation_count)
    programming = candidate["programming"]
    programming["programmer"] = programmer
    for operation in programming["operations"]:
        number = operation["operation_number"]
        print(f"\nOperation {number}")
        print("-" * 30)
        operation["operation_type"] = get_operation(
            {"operation": operation.get("operation_type", "")}
        )
        operation["program_name"] = get_existing_or_new(
            operation, "program_name", "Program Name"
        )
        operation["revision"] = get_existing_or_new(operation, "revision", "Revision")
        operation["status"] = get_status(operation)
        operation["last_updated"] = get_timestamp()
        operation["notes"] = get_existing_or_new(operation, "notes", "Notes")
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
    return save_job(
        job,
        action=(
            "plan_resize"
            if operation_count != normalized["programming"]["operation_count"]
            else "logical_save"
        ),
    )


def update_saw_cutting(job):
    print("\nUpdate Saw Cutting")
    print("-" * 30)

    saw_cutting = job["saw_cutting"]

    job["saw_cutting"] = {
        "employee": get_existing_or_new(saw_cutting, "employee", "Employee"),
        "qty_cut": get_existing_int_or_new(saw_cutting, "qty_cut", "Qty Cut"),
        "cut_length": get_existing_or_new(saw_cutting, "cut_length", "Cut Length"),
        "scrap_qty": get_existing_int_or_new(saw_cutting, "scrap_qty", "Scrap Qty"),
        "status": get_status(saw_cutting),
        "last_updated": get_timestamp(),
        "notes": get_existing_or_new(saw_cutting, "notes", "Notes"),
    }

    return save_job(job)


def update_cnc_machining(job):
    print("\nUpdate CNC Machining")
    print("-" * 30)

    normalized = normalize_operations(job)
    programming_operations = normalized["programming"]["operations"]
    operation_number = choose_configured_operation(programming_operations)
    programming_operation = operation_by_number(programming_operations, operation_number)
    operation_type = programming_operation.get("operation_type", "")
    if operation_type not in ALLOWED_OPERATIONS:
        print("\nThis operation needs a Mill or Turning type in Programming first.")
        return False
    print(f"Operation Type: {operation_type}")
    cnc_operations = normalized["cnc_machining"]["operations"]
    cnc_operation = operation_by_number(cnc_operations, operation_number)
    operator = get_existing_or_new(cnc_operation, "operator", "Operator Name")
    machine = get_machine_for_operation(cnc_operation, operation_type)
    qty_completed = get_existing_int_or_new(
        {**cnc_operation, "qty_complete": cnc_operation.get("qty_complete", 0)},
        "qty_complete",
        "Quantity Pieces Completed",
    )
    status = get_cnc_status(
        normalized,
        qty_completed,
        cnc_operation.get("status"),
    )
    cnc_operation.update(
        {
            "operator": operator,
            "machine": machine,
            "qty_complete": qty_completed,
            "status": status,
            "last_updated": get_timestamp(),
            "notes": get_existing_or_new(cnc_operation, "notes", "Notes"),
        }
    )
    cnc = normalized["cnc_machining"]
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
    job.update(normalized)
    return save_job(job)


def update_deburr(job):
    print("\nUpdate Deburr")
    print("-" * 30)

    deburr = job["deburr"]

    job["deburr"] = {
        "employee": get_existing_or_new(deburr, "employee", "Employee"),
        "deburr_needed": get_existing_or_new(deburr, "deburr_needed", "Deburr Needed"),
        "qty_deburred": get_existing_int_or_new(deburr, "qty_deburred", "Qty Deburred"),
        "status": get_status(deburr),
        "last_updated": get_timestamp(),
        "notes": get_existing_or_new(deburr, "notes", "Notes"),
    }

    return save_job(job)


def update_inspection(job):
    print("\nUpdate Inspection")
    print("-" * 30)

    normalized = normalize_operations(job)
    programming_operations = normalized["programming"]["operations"]
    operation_number = choose_configured_operation(programming_operations)
    programming_operation = operation_by_number(programming_operations, operation_number)
    cnc_operation = operation_by_number(
        normalized["cnc_machining"]["operations"], operation_number
    )
    machine = cnc_operation.get("machine", "") if cnc_operation else ""
    if not machine:
        print(
            "\nNo CNC machine is assigned to this operation. "
            "Assign the machine in CNC Machining first."
        )
        return False
    operation_type = programming_operation.get("operation_type", "")
    print(f"Operation: Operation {operation_number} - {operation_type or BLANK}")
    print(f"Machine: {machine}")
    inspection = normalized["inspection"]
    record = operation_by_number(inspection["records"], operation_number)
    if record is None:
        record = {"operation_number": operation_number, "dimensions": []}
        inspection["records"].append(record)
    dimensions = record.get("dimensions", [])

    if not isinstance(dimensions, list):
        dimensions = []

    record["operation_type"] = operation_type
    record["machine"] = machine
    record["inspector"] = get_existing_or_new(record, "inspector", "Inspector")
    record["report_type"] = record.get("report_type") or "First Article Inspection"
    record["status"] = get_status(record)

    add_dimensions = False

    if dimensions:
        replace_dimensions = get_yes_or_no("Replace existing dimensions? (y/n): ")

        if replace_dimensions:
            dimensions = []
            add_dimensions = True
    else:
        add_dimensions = get_yes_or_no("Add dimensions? (y/n): ")

    if add_dimensions:
        dimensions = []
        dimension_number = 1

        while True:
            print(f"\nDimension {dimension_number}")
            print("-" * 30)

            dimensions.append(
                {
                    "dimension_number": dimension_number,
                    "target_dimension": input("Target Dimension: ").strip(),
                    "tolerance": input("Tolerance: ").strip(),
                    "finding": input("Finding / Actual Dimension: ").strip(),
                    "measurement_equipment_used": input(
                        "Measurement Equipment Used: "
                    ).strip(),
                    "result": get_dimension_result(),
                }
            )

            if not get_yes_or_no("Add another dimension? (y/n): "):
                break

            dimension_number += 1

    record["dimensions"] = dimensions
    record["notes"] = get_existing_or_new(record, "notes", "Notes")
    record["last_updated"] = get_timestamp()
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
    job.update(normalized)
    return save_job(job)


def update_packing(job):
    print("\nUpdate Packing")
    print("-" * 30)

    packing = job["packing"]

    job["packing"] = {
        "employee": get_existing_or_new(packing, "employee", "Employee"),
        "qty_packed": get_existing_int_or_new(packing, "qty_packed", "Qty Packed"),
        "box_count": get_existing_int_or_new(packing, "box_count", "Box Count"),
        "status": get_status(packing),
        "last_updated": get_timestamp(),
        "notes": get_existing_or_new(packing, "notes", "Notes"),
    }

    return save_job(job)


def update_shipping(job):
    print("\nUpdate Shipping")
    print("-" * 30)

    shipping = job["shipping"]

    job["shipping"] = {
        "employee": get_existing_or_new(shipping, "employee", "Employee"),
        "ship_date": get_existing_or_new(shipping, "ship_date", "Ship Date"),
        "carrier": get_existing_or_new(shipping, "carrier", "Carrier"),
        "tracking": get_existing_or_new(shipping, "tracking", "Tracking"),
        "status": get_status(shipping),
        "last_updated": get_timestamp(),
        "notes": get_existing_or_new(shipping, "notes", "Notes"),
    }

    return save_job(job)


def show_updated_traveler_and_choose_next(job):
    print_traveler(job)

    while True:
        print("\nWhat next?")
        print("1. Update another section")
        print("0. Exit to Main Menu")

        choice = input("Choose an option: ").strip()

        if choice == "1":
            return True
        if choice == "0":
            return False

        print("Invalid choice. Please try again.")


def job_menu(job):
    while True:
        print("\nJob Menu")
        print("-" * 30)
        print("1. Update Programming")
        print("2. Update Saw Cutting")
        print("3. Update CNC Machining")
        print("4. Update Deburr")
        print("5. Update Inspection")
        print("6. Update Packing")
        print("7. Update Shipping")
        print("8. Print Traveler")
        print("9. Print Status Summary")
        print("10. Save Job")
        print("0. Exit to Main Menu")

        choice = input("Choose an option: ").strip()

        if choice == "1":
            update_programming(job)
            if not show_updated_traveler_and_choose_next(job):
                return
        elif choice == "2":
            update_saw_cutting(job)
            if not show_updated_traveler_and_choose_next(job):
                return
        elif choice == "3":
            update_cnc_machining(job)
            if not show_updated_traveler_and_choose_next(job):
                return
        elif choice == "4":
            update_deburr(job)
            if not show_updated_traveler_and_choose_next(job):
                return
        elif choice == "5":
            update_inspection(job)
            if not show_updated_traveler_and_choose_next(job):
                return
        elif choice == "6":
            update_packing(job)
            if not show_updated_traveler_and_choose_next(job):
                return
        elif choice == "7":
            update_shipping(job)
            if not show_updated_traveler_and_choose_next(job):
                return
        elif choice == "8":
            print_traveler(job)
        elif choice == "9":
            print_job_status_summary(job)
        elif choice == "10":
            save_job(job)
        elif choice == "0":
            return
        else:
            print("Invalid choice. Please try again.")


def _terminal_confirm_default_no(prompt):
    return input(f"{prompt} [y/N]: ").strip().casefold() == "y"


def _service_update_ordinary(job, persistence):
    """Edit one established ordinary allowlisted field in service mode."""
    import traveler_domain as domain

    normalized = normalize_operations(job)
    sections = tuple(
        dict.fromkeys(
            tuple(domain.SECTION_EDITABLE_FIELDS)
            + tuple(domain.OPERATION_EDITABLE_FIELDS)
        )
    )
    print("\nOrdinary sections")
    for index, section in enumerate(sections, 1):
        print(f"{index}. {section.replace('_', ' ').title()}")
    try:
        section = sections[get_positive_int("Section number") - 1]
    except (IndexError, ValueError):
        print("Invalid section.")
        return False
    fields = domain.SECTION_EDITABLE_FIELDS.get(section, {})
    operation_number = None
    if section in domain.OPERATION_EDITABLE_FIELDS:
        operations = normalized[section]["records" if section == "inspection" else "operations"]
        if not operations:
            print("No existing operation record is available to edit.")
            return False
        operation_number = get_positive_int("Operation number")
        row = operation_by_number(operations, operation_number)
        if row is None:
            print("That operation record does not exist.")
            return False
        fields = domain.OPERATION_EDITABLE_FIELDS[section]
    print("Editable fields: " + ", ".join(fields))
    field = input("Field: ").strip()
    if field not in fields:
        print("That field is not in the ordinary allowlist.")
        return False
    if operation_number is None:
        current = normalized[section].get(field, "")
        reference = section
        target = {"section": section, "field": field, "compatibility_reference": reference}
    else:
        key = "records" if section == "inspection" else "operations"
        current = operation_by_number(normalized[section][key], operation_number).get(field, "")
        reference = f"{section}:operation:{operation_number}"
        target = {"section": section, "field": field, "compatibility_reference": reference}
    value = input(f"New value [current: {current}]: ")
    if value == "":
        value = current
    if fields[field] == "quantity":
        try:
            value = int(value)
            if value < 0:
                raise ValueError
        except (TypeError, ValueError):
            print("The quantity must be a non-negative whole number.")
            return False
    candidate, _confirmed = domain.apply_ordinary_field_change(
        copy.deepcopy(job), target, value
    )
    job.clear()
    job.update(candidate)
    if save_job(job, persistence=persistence):
        return True
    return False


def _service_resize_plan(job, persistence):
    if not persistence.job_planner_authorized:
        print("\nOperation-plan resizing requires Job Planner authorization.")
        return False
    tracked = _loaded_snapshots.get(id(job))
    base = tracked[1] if tracked is not None and tracked[0] is job else None
    if base is None:
        print("\nThe traveler must be loaded again before resizing.")
        return False
    current = normalize_operations(job)["programming"]["operation_count"]
    desired = get_positive_int("Desired operation count", current)
    try:
        result = persistence.resize_plan(base, desired)
    except PlanResizeConfirmationRequired as confirmation:
        print("\nThe latest plan would remove:")
        for item in confirmation.removed_operations:
            detail = ", ".join(item.get("meaningful_sections", ()))
            print(
                f"  Operation {item['operation_number']}"
                + (f" (entered data: {detail})" if detail else "")
            )
        if not _terminal_confirm_default_no("Remove exactly these operations"):
            print("Resize canceled. No change was written.")
            return False
        try:
            result = persistence.resize_plan(
                base,
                desired,
                confirm_removed_operation_ids=confirmation.operation_ids,
            )
        except PlanResizeConflict as conflict:
            job.clear()
            job.update(copy.deepcopy(conflict.latest_snapshot.traveler))
            _loaded_snapshots[id(job)] = (job, conflict.latest_snapshot)
            print("\nThe plan changed before confirmation. Review the latest traveler.")
            return False
    except PlanResizeConflict as conflict:
        job.clear()
        job.update(copy.deepcopy(conflict.latest_snapshot.traveler))
        _loaded_snapshots[id(job)] = (job, conflict.latest_snapshot)
        print("\nThe operation plan changed. Review the latest traveler.")
        return False
    except PersistenceError as error:
        print(f"\nOperation plan was not resized: {error}")
        return False
    job.clear()
    job.update(copy.deepcopy(result.snapshot.traveler))
    _loaded_snapshots[id(job)] = (job, result.snapshot)
    print("\nOperation plan resized." if result.changed else "\nOperation plan unchanged.")
    return True


def _service_create_new_job(persistence):
    if not persistence.job_planner_authorized:
        print("\nCreating travelers requires Job Planner authorization.")
        return None
    job = {
        "job_number": input("Job Number: ").strip(),
        "customer": input("Customer: ").strip(),
        "part_number": input("Part Number: ").strip(),
        "description": input("Description: ").strip(),
        "qty_to_make": get_int("Qty To Make"),
        "material": input("Material: ").strip(),
        "cut_length": input("Cut Length: ").strip(),
        "programming": {},
        "saw_cutting": {},
        "cnc_machining": {},
        "deburr": {},
        "inspection": {},
        "packing": {},
        "shipping": {},
    }
    count = get_positive_int("Number of Operations Required", 1)
    if count != 1:
        job = resize_operation_plan(job, count)
    return job if save_job(job, persistence=persistence) else None


def _service_job_menu(job, persistence, manager):
    while True:
        print("\nJob Menu (service mode)")
        print("-" * 30)
        print("1. Edit Ordinary Field")
        if manager.is_job_planner:
            print("2. Resize Operation Plan")
        print("3. Print Traveler")
        print("4. Print Status Summary")
        print("0. Exit to Main Menu")
        choice = input("Choose an option: ").strip()
        if choice == "1":
            _service_update_ordinary(job, persistence)
        elif choice == "2" and manager.is_job_planner:
            _service_resize_plan(job, persistence)
        elif choice == "3":
            print_traveler(job)
        elif choice == "4":
            print_job_status_summary(job)
        elif choice == "0":
            return
        else:
            print("Invalid choice. Please choose an available option.")


def _service_login(manager):
    username = input("Username: ").strip()
    pin = getpass.getpass("Four-digit PIN: ")
    try:
        remember = _terminal_confirm_default_no("Remember this session securely")
        return manager.login(username, pin, remember=remember)
    except TravelerClientError as error:
        print(f"\nSign in failed: {error.public_message}")
        return None
    finally:
        pin = ""


def main(persistence=None, session_manager=None):
    selected_mode = os.environ.get(MODE_ENV, "local")
    if session_manager is None and selected_mode.casefold() == "service":
        try:
            session_manager = build_session_manager_from_environment()
        except (SessionConfigurationError, TravelerClientError) as error:
            print(f"ShopOS service configuration is unavailable: {error}")
            return
    if session_manager is not None:
        if persistence is not None:
            raise ValueError("A service session cannot be combined with injected persistence.")
        if session_manager.restore_remembered_session() is None:
            while not session_manager.signed_in:
                print("\nShopOS Employee Sign In")
                if _service_login(session_manager) is None:
                    if not _terminal_confirm_default_no("Try signing in again"):
                        return
        active = build_persistence(
            jobs_directory=None, mode="service", service_client=session_manager.client
        )
        configure_persistence(active)
        while True:
            print("\nMain Menu (service mode)")
            print("-" * 30)
            print(
                f"Signed in: {session_manager.employee.display_name or session_manager.employee.username}"
            )
            print("1. Create New Job Traveler")
            print("2. Open Existing Job Traveler")
            print("3. List Existing Job Travelers")
            print("4. Switch Employee")
            print("5. Sign Out")
            print("0. Exit")
            choice = input("Choose an option: ").strip()
            if choice == "1":
                job = _service_create_new_job(active)
                if job is not None:
                    _service_job_menu(job, active, session_manager)
            elif choice == "2":
                job = load_job(input("Job Number: ").strip(), persistence=active)
                if job is not None:
                    _service_job_menu(job, active, session_manager)
            elif choice == "3":
                list_existing_jobs(persistence=active)
            elif choice in {"4", "5"}:
                result = session_manager.switch_employee() if choice == "4" else session_manager.sign_out()
                if not result.server_invalidation_confirmed:
                    print("Server-side sign-out could not be confirmed; local session was cleared.")
                if choice == "5":
                    return
                while not session_manager.signed_in:
                    print("\nShopOS Employee Sign In")
                    if _service_login(session_manager) is None:
                        if not _terminal_confirm_default_no("Try signing in again"):
                            return
            elif choice == "0":
                session_manager.sign_out()
                print("Goodbye.")
                return
            else:
                print("Invalid choice. Please choose an available option.")
        return

    configure_persistence(_active_persistence(persistence))
    while True:
        print("\nMain Menu")
        print("-" * 30)
        print("1. Create New Job Traveler")
        print("2. Open Existing Job Traveler")
        print("3. List Existing Job Travelers")
        print("0. Exit")

        choice = input("Choose an option: ").strip()

        if choice == "1":
            job = create_new_job()
            if job is not None:
                job_menu(job)
        elif choice == "2":
            job_number = input("Job Number: ").strip()
            job = load_job(job_number)

            if job is not None:
                job_menu(job)
        elif choice == "3":
            list_existing_jobs()
        elif choice == "0":
            print("Goodbye.")
            return
        else:
            print("Invalid choice. Please try again.")


if __name__ == "__main__":
    main()
