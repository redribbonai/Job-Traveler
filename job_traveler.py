# job_traveler.py
import copy
import json
import os
from datetime import datetime


BLANK = "__________"
JOBS_FOLDER = "jobs"

SECTIONS = [
    "programming",
    "saw_cutting",
    "cnc_machining",
    "deburr",
    "inspection",
    "packing",
    "shipping",
]

ALLOWED_STATUSES = ["Pending", "In Progress", "Completed"]
ALLOWED_OPERATIONS = ["Mill", "Turning"]
MILLING_MACHINES = [
    "Haas VF2 SS",
    "DNM 5700L",
    "DNM 4500",
    "Mazak VC-EZ26",
]
TURNING_MACHINES = [
    "Haas ST15Y",
    "Lynx 2100LSY #1",
    "Lynx 2100LSY #2",
    "Puma 2600 SY2",
    "Puma TT 1300 SYYB",
]
ALL_MACHINES = MILLING_MACHINES + TURNING_MACHINES


def blank_programming_operation(operation_number):
    return {
        "operation_number": operation_number,
        "operation_type": "",
        "program_name": "",
        "revision": "",
        "status": "Pending",
        "last_updated": "",
        "notes": "",
    }


def blank_cnc_operation(operation_number):
    return {
        "operation_number": operation_number,
        "operator": "",
        "machine": "",
        "qty_complete": 0,
        "status": "Pending",
        "last_updated": "",
        "notes": "",
    }


def infer_operation_type(*values):
    """Infer a legacy operation type without inventing a machine."""
    for value in values:
        if value in ALLOWED_OPERATIONS:
            return value
        if value in MILLING_MACHINES:
            return "Mill"
        if value in TURNING_MACHINES:
            return "Turning"
    return ""


def normalize_operations(job):
    """Return a normalized copy; merely loading a legacy job never rewrites it."""
    normalized = copy.deepcopy(job)
    for section in SECTIONS:
        normalized.setdefault(section, {})

    programming = normalized.get("programming")
    if not isinstance(programming, dict):
        programming = {}
    raw_programming_operations = programming.get("operations")
    if isinstance(raw_programming_operations, list) and raw_programming_operations:
        programming_operations = []
        for number, raw in enumerate(raw_programming_operations, start=1):
            operation = copy.deepcopy(raw) if isinstance(raw, dict) else {}
            operation["operation_number"] = number
            operation.setdefault("operation_type", operation.pop("operation", ""))
            for key, default in blank_programming_operation(number).items():
                operation.setdefault(key, default)
            programming_operations.append(operation)
    else:
        operation = blank_programming_operation(1)
        operation.update(
            {
                "operation_type": infer_operation_type(
                    programming.get("operation"), programming.get("machine")
                ),
                "program_name": programming.get("program_name", ""),
                "revision": programming.get("revision", ""),
                "status": programming.get("status", "Pending"),
                "last_updated": programming.get("last_updated", ""),
                "notes": programming.get("notes", ""),
            }
        )
        programming_operations = [operation]
    try:
        requested_count = int(programming.get("operation_count", len(programming_operations)))
    except (TypeError, ValueError):
        requested_count = len(programming_operations)
    requested_count = max(1, requested_count, len(programming_operations))
    while len(programming_operations) < requested_count:
        programming_operations.append(
            blank_programming_operation(len(programming_operations) + 1)
        )
    programming["programmer"] = programming.get("programmer", "")
    programming["operation_count"] = len(programming_operations)
    programming["operations"] = programming_operations
    normalized["programming"] = programming

    cnc = normalized.get("cnc_machining")
    if not isinstance(cnc, dict):
        cnc = {}
    raw_cnc_operations = cnc.get("operations")
    if isinstance(raw_cnc_operations, list):
        cnc_operations = []
        for number, raw in enumerate(raw_cnc_operations, start=1):
            operation = copy.deepcopy(raw) if isinstance(raw, dict) else {}
            operation["operation_number"] = number
            if "qty_complete" not in operation:
                operation["qty_complete"] = operation.pop("qty_completed", 0)
            for key, default in blank_cnc_operation(number).items():
                operation.setdefault(key, default)
            operation.pop("first_article", None)
            cnc_operations.append(operation)
    else:
        operation = blank_cnc_operation(1)
        legacy_machine = cnc.get("machine") or programming.get("machine", "")
        operation.update(
            {
                "operator": cnc.get("operator", ""),
                "machine": legacy_machine,
                "qty_complete": cnc.get("qty_complete", cnc.get("qty_completed", 0)),
                "status": cnc.get("status", "Pending"),
                "last_updated": cnc.get("last_updated", ""),
                "notes": cnc.get("notes", ""),
            }
        )
        cnc_operations = [operation]
    while len(cnc_operations) < len(programming_operations):
        cnc_operations.append(blank_cnc_operation(len(cnc_operations) + 1))
    cnc["operations"] = cnc_operations
    normalized["cnc_machining"] = cnc

    inspection = normalized.get("inspection")
    if not isinstance(inspection, dict):
        inspection = {}
    raw_records = inspection.get("records")
    if isinstance(raw_records, list):
        records = []
        for row in raw_records:
            if not isinstance(row, dict):
                continue
            record = copy.deepcopy(row)
            try:
                operation_number = int(record.get("operation_number"))
            except (TypeError, ValueError):
                continue
            programming_operation = operation_by_number(
                programming_operations, operation_number
            )
            cnc_operation = operation_by_number(cnc_operations, operation_number)
            if programming_operation is None:
                continue
            record["operation_number"] = operation_number
            record.setdefault(
                "operation_type", programming_operation.get("operation_type", "")
            )
            record.setdefault(
                "machine", cnc_operation.get("machine", "") if cnc_operation else ""
            )
            record.setdefault("dimensions", [])
            records.append(record)
    elif any(
        key in inspection
        for key in ("inspector", "dimensions", "operation", "machine", "status")
    ):
        records = [
            {
                "operation_number": 1,
                "operation_type": infer_operation_type(
                    inspection.get("operation"), programming_operations[0]["operation_type"]
                ),
                "machine": inspection.get("machine") or cnc_operations[0].get("machine", ""),
                "inspector": inspection.get("inspector", ""),
                "report_type": inspection.get("report_type", ""),
                "status": inspection.get("status", "Pending"),
                "last_updated": inspection.get("last_updated", ""),
                "notes": inspection.get("notes", ""),
                "dimensions": copy.deepcopy(inspection.get("dimensions", [])),
            }
        ]
    else:
        records = []
    inspection["records"] = records
    normalized["inspection"] = inspection
    return normalized


def canonical_job(job):
    """Return the save-time schema while retaining unrelated compatible fields."""
    canonical = normalize_operations(job)
    programming = canonical["programming"]
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
    cnc = canonical["cnc_machining"]
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
    inspection = canonical["inspection"]
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
    return canonical


def operation_by_number(operations, operation_number):
    for operation in operations:
        if operation.get("operation_number") == operation_number:
            return operation
    return None


def operation_has_data(operation, ignored=("operation_number",)):
    if not isinstance(operation, dict):
        return False
    for key, value in operation.items():
        if key in ignored:
            continue
        if value not in ("", None, 0, "Pending", []):
            return True
    return False


def resize_operation_plan(job, new_count, confirm_removal=False):
    """Resize a normalized plan, refusing to discard downstream records."""
    if isinstance(new_count, bool) or not isinstance(new_count, int) or new_count <= 0:
        raise ValueError("Number of Operations Required must be a positive whole number.")
    normalized = normalize_operations(job)
    programming = normalized["programming"]
    old_count = programming["operation_count"]
    if new_count < old_count:
        removed = set(range(new_count + 1, old_count + 1))
        used_cnc = [
            row.get("operation_number")
            for row in normalized["cnc_machining"]["operations"]
            if row.get("operation_number") in removed and operation_has_data(row)
        ]
        used_inspection = [
            row.get("operation_number")
            for row in normalized["inspection"]["records"]
            if row.get("operation_number") in removed and operation_has_data(row)
        ]
        if used_cnc or used_inspection:
            numbers = sorted(set(used_cnc + used_inspection))
            raise ValueError(
                "Cannot reduce operations because production or inspection data exists "
                f"for Operation(s) {', '.join(map(str, numbers))}."
            )
        if not confirm_removal:
            raise ValueError("Confirm removal of the unused blank operation(s).")
        programming["operations"] = programming["operations"][:new_count]
        normalized["cnc_machining"]["operations"] = [
            row
            for row in normalized["cnc_machining"]["operations"]
            if row.get("operation_number", 0) <= new_count
        ]
        normalized["inspection"]["records"] = [
            row
            for row in normalized["inspection"]["records"]
            if row.get("operation_number", 0) <= new_count
        ]
    else:
        while len(programming["operations"]) < new_count:
            programming["operations"].append(
                blank_programming_operation(len(programming["operations"]) + 1)
            )
        while len(normalized["cnc_machining"]["operations"]) < new_count:
            normalized["cnc_machining"]["operations"].append(
                blank_cnc_operation(len(normalized["cnc_machining"]["operations"]) + 1)
            )
    programming["operation_count"] = new_count
    return normalized


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


def get_required_quantity(job):
    try:
        return int(job.get("qty_to_make"))
    except (TypeError, ValueError):
        return None


def get_cnc_status(job, qty_completed, current_status):
    required_quantity = get_required_quantity(job)

    if qty_completed <= 0:
        if current_status == "In Progress":
            return "In Progress"
        return "Pending"

    if required_quantity is None:
        return "In Progress"

    if qty_completed >= required_quantity:
        return "Completed"

    return "In Progress"


def save_job(job):
    os.makedirs(JOBS_FOLDER, exist_ok=True)

    filename = os.path.join(JOBS_FOLDER, f"{job['job_number']}.json")

    saved_job = canonical_job(job)
    with open(filename, "w") as file:
        json.dump(saved_job, file, indent=4)

    job.clear()
    job.update(saved_job)

    print(f"\nSaved job traveler to {filename}")


def load_job(job_number):
    filename = os.path.join(JOBS_FOLDER, f"{job_number}.json")

    if not os.path.exists(filename):
        print(f"\nNo job traveler found for job number {job_number}.")
        return None

    with open(filename, "r") as file:
        job = json.load(file)

    for section in SECTIONS:
        job.setdefault(section, {})

    return job


def list_existing_jobs():
    if not os.path.exists(JOBS_FOLDER):
        print("\nNo saved job travelers found.")
        return

    job_files = [
        filename
        for filename in os.listdir(JOBS_FOLDER)
        if filename.endswith(".json")
    ]

    if not job_files:
        print("\nNo saved job travelers found.")
        return

    print("\nExisting Job Travelers")
    print("-" * 30)
    print("Job Number | Customer | Part Number | Qty To Make")

    for filename in sorted(job_files):
        path = os.path.join(JOBS_FOLDER, filename)

        try:
            with open(path, "r") as file:
                job = json.load(file)
        except (OSError, json.JSONDecodeError):
            continue

        print(
            f"{job.get('job_number', BLANK)} | "
            f"{job.get('customer', BLANK)} | "
            f"{job.get('part_number', BLANK)} | "
            f"Qty: {job.get('qty_to_make', BLANK)}"
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

    save_job(job)
    return job


def blank_if_missing(job, section, key):
    value = job.get(section, {}).get(key)

    if value == "" or value is None:
        return BLANK

    return value


def status_if_missing(job, section):
    section_data = job.get(section, {})
    if isinstance(section_data, dict):
        rows = None
        if section in ("programming", "cnc_machining"):
            rows = section_data.get("operations")
        elif section == "inspection":
            rows = section_data.get("records")
        if isinstance(rows, list) and rows:
            statuses = [
                row.get("status", "Pending") if isinstance(row, dict) else "Pending"
                for row in rows
            ]
            if all(value == "Completed" for value in statuses):
                return "Completed"
            if any(value in ("In Progress", "Completed") for value in statuses):
                return "In Progress"
            return "Pending"
    value = section_data.get("status") if isinstance(section_data, dict) else None

    if value == "" or value is None or value not in ALLOWED_STATUSES:
        return "Pending"

    return value


def operation_if_missing(job, section):
    value = job.get(section, {}).get("operation")

    if value == "" or value is None or value not in ALLOWED_OPERATIONS:
        return BLANK

    return value


def job_field(job, key):
    value = job.get(key)

    if value == "" or value is None:
        return BLANK

    return value


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
    save_job(job)
    return True


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

    save_job(job)


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
    save_job(job)
    return True


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

    save_job(job)


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
    save_job(job)
    return True


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

    save_job(job)


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

    save_job(job)


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


def main():
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
