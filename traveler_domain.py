"""Pure, reusable Job Traveler domain and compatibility contract.

This module deliberately performs no filesystem access and imports no UI toolkit.
Normalization always returns a deep copy and never persists its result.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from typing import Any


DOMAIN_CONTRACT_NAME = "rr-shopos-job-traveler"
DOMAIN_CONTRACT_VERSION = 2

BLANK = "__________"
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

SHOPOS_METADATA_KEY = "_shopos"
SHOPOS_OPERATION_IDENTITIES_KEY = "operation_identities"
SHOPOS_DOCUMENT_REVISION_KEY = "document_revision"
SHOPOS_LAST_MUTATION_KEY = "last_applied_mutation_id"
SHOPOS_CLOSURE_STATE_KEY = "closure_state"

# These are ordinary value edits only.  Status/workflow transitions, operation-plan
# changes, identities, and Parts Count-owned values intentionally are not included.
SECTION_EDITABLE_FIELDS = {
    "programming": {"programmer": "text"},
    "saw_cutting": {
        "employee": "text",
        "qty_cut": "quantity",
        "cut_length": "text",
        "scrap_qty": "quantity",
        "notes": "notes",
    },
    "deburr": {
        "employee": "text",
        "deburr_needed": "text",
        "qty_deburred": "quantity",
        "notes": "notes",
    },
    "packing": {
        "employee": "text",
        "qty_packed": "quantity",
        "box_count": "quantity",
        "notes": "notes",
    },
    "shipping": {
        "employee": "text",
        "ship_date": "text",
        "carrier": "text",
        "tracking": "text",
        "notes": "notes",
    },
}
OPERATION_EDITABLE_FIELDS = {
    "programming": {
        "program_name": "text",
        "revision": "text",
        "notes": "notes",
    },
    "cnc_machining": {
        "operator": "text",
        "machine": "machine",
        "notes": "notes",
    },
    "inspection": {
        "inspector": "text",
        "report_type": "text",
        "notes": "notes",
    },
}
PROTECTED_MUTATION_FIELDS = frozenset(
    {
        "job_number",
        "job_num",
        SHOPOS_METADATA_KEY,
        "operation_number",
        "operation_count",
        "operations",
        "records",
        "status",
        "last_updated",
        "operation_type",
        "qty_complete",
        "qty_completed",
        "parts_completed",
        "part_total",
        "dimensions",
        "closure",
        "closed",
        "tasks",
        "assignments",
    }
)


class TravelerValidationError(ValueError):
    """Raised when a protected traveler structure is unsafe to interpret."""


def _canonical_uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise TravelerValidationError(f"{label} must be a canonical UUID.")
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, AttributeError) as error:
        raise TravelerValidationError(f"{label} must be a canonical UUID.") from error
    if value != canonical:
        raise TravelerValidationError(f"{label} must be a canonical UUID.")
    return canonical


def _metadata(job: dict[str, Any]) -> dict[str, Any]:
    value = job.get(SHOPOS_METADATA_KEY)
    return value if isinstance(value, dict) else {}


def document_revision(job: object) -> int:
    """Return the persisted ShopOS revision; legacy travelers are revision zero."""
    if not isinstance(job, dict):
        raise TravelerValidationError("The Job Traveler file must contain a JSON object.")
    metadata = job.get(SHOPOS_METADATA_KEY)
    if metadata is None:
        return 0
    if not isinstance(metadata, dict):
        raise TravelerValidationError("_shopos must contain a JSON object.")
    value = metadata.get(SHOPOS_DOCUMENT_REVISION_KEY, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TravelerValidationError(
            "_shopos.document_revision must be a non-negative whole number."
        )
    return value


def last_applied_mutation_id(job: object) -> str | None:
    if not isinstance(job, dict):
        raise TravelerValidationError("The Job Traveler file must contain a JSON object.")
    metadata = job.get(SHOPOS_METADATA_KEY)
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        raise TravelerValidationError("_shopos must contain a JSON object.")
    value = metadata.get(SHOPOS_LAST_MUTATION_KEY)
    if value is None:
        return None
    return _canonical_uuid(value, "_shopos.last_applied_mutation_id")


def is_authoritatively_closed(job: object) -> bool:
    """Recognize, but never create, the reserved future closure marker."""
    if not isinstance(job, dict):
        raise TravelerValidationError("The Job Traveler file must contain a JSON object.")
    metadata = job.get(SHOPOS_METADATA_KEY)
    if metadata is None:
        return False
    if not isinstance(metadata, dict):
        raise TravelerValidationError("_shopos must contain a JSON object.")
    value = metadata.get(SHOPOS_CLOSURE_STATE_KEY)
    if value is None:
        return False
    if value not in ("open", "closed"):
        raise TravelerValidationError(
            "_shopos.closure_state must be 'open' or 'closed'."
        )
    return value == "closed"


def _validated_identity_maps(
    job: dict[str, Any], *, require_complete: bool = False
) -> tuple[dict[str, str], dict[str, str]]:
    metadata = _metadata(job)
    identities = metadata.get(SHOPOS_OPERATION_IDENTITIES_KEY)
    if identities is None:
        if require_complete:
            raise TravelerValidationError("Stable operation identities are missing.")
        return {}, {}
    if not isinstance(identities, dict):
        raise TravelerValidationError("_shopos.operation_identities must be an object.")
    raw_machining = identities.get("machining_operations", {})
    raw_sections = identities.get("sections", {})
    if not isinstance(raw_machining, dict) or not isinstance(raw_sections, dict):
        raise TravelerValidationError(
            "Stable operation identity collections must be objects."
        )
    machining: dict[str, str] = {}
    sections: dict[str, str] = {}
    seen: set[str] = set()
    for raw_key, raw_value in raw_machining.items():
        if (
            not isinstance(raw_key, str)
            or not raw_key.isdigit()
            or str(int(raw_key)) != raw_key
            or int(raw_key) <= 0
        ):
            raise TravelerValidationError(
                "Stable machining identity keys must be positive operation numbers."
            )
        identity = _canonical_uuid(
            raw_value, f"Stable identity for machining operation {raw_key}"
        )
        if identity in seen:
            raise TravelerValidationError("Stable operation identities must be unique.")
        seen.add(identity)
        machining[raw_key] = identity
    for raw_key, raw_value in raw_sections.items():
        if not isinstance(raw_key, str) or raw_key not in SECTIONS:
            raise TravelerValidationError("Stable section identity has an invalid section.")
        identity = _canonical_uuid(raw_value, f"Stable identity for {raw_key}")
        if identity in seen:
            raise TravelerValidationError("Stable operation identities must be unique.")
        seen.add(identity)
        sections[raw_key] = identity
    return machining, sections


def stable_identity_projection(job: object) -> dict[str, dict[str, str]]:
    if not isinstance(job, dict):
        raise TravelerValidationError("The Job Traveler file must contain a JSON object.")
    machining, sections = _validated_identity_maps(job)
    return {
        "machining_operations": copy.deepcopy(machining),
        "sections": copy.deepcopy(sections),
    }


def bootstrap_stable_identities(job: dict[str, Any], id_factory) -> dict[str, Any]:
    """Return a copy with missing current identities filled by ``id_factory``.

    This helper does not persist and is never called during a read.  The server
    invokes it only after an ordinary edit has passed conflict and validation
    checks, immediately before the confirmed atomic replacement.
    """
    validate_traveler_structure(job)
    candidate = canonical_job(job)
    metadata = copy.deepcopy(_metadata(candidate))
    identities = copy.deepcopy(metadata.get(SHOPOS_OPERATION_IDENTITIES_KEY, {}))
    if not isinstance(identities, dict):
        raise TravelerValidationError("_shopos.operation_identities must be an object.")
    machining = copy.deepcopy(identities.get("machining_operations", {}))
    sections = copy.deepcopy(identities.get("sections", {}))
    if not isinstance(machining, dict) or not isinstance(sections, dict):
        raise TravelerValidationError(
            "Stable operation identity collections must be objects."
        )

    current_operation_keys = {
        str(operation["operation_number"])
        for operation in candidate["programming"]["operations"]
    }
    # A confirmed shrink retires only the identities that left the plan.  Keeping
    # retired keys would make a later expansion recycle an operation identity.
    machining = {
        key: value for key, value in machining.items() if key in current_operation_keys
    }
    for operation in candidate["programming"]["operations"]:
        key = str(operation["operation_number"])
        if key not in machining:
            machining[key] = _canonical_uuid(
                id_factory(), f"Stable identity for machining operation {key}"
            )
    for section in SECTIONS:
        if section not in sections:
            sections[section] = _canonical_uuid(
                id_factory(), f"Stable identity for {section}"
            )
    identities["machining_operations"] = machining
    identities["sections"] = sections
    metadata[SHOPOS_OPERATION_IDENTITIES_KEY] = identities
    candidate[SHOPOS_METADATA_KEY] = metadata
    _validated_identity_maps(candidate, require_complete=True)
    return candidate


def confirm_mutation_metadata(
    job: dict[str, Any], *, prior_revision: int, mutation_id: str
) -> dict[str, Any]:
    """Add the one confirmed server revision while preserving private metadata."""
    if (
        isinstance(prior_revision, bool)
        or not isinstance(prior_revision, int)
        or prior_revision < 0
    ):
        raise TravelerValidationError("The prior revision is invalid.")
    canonical_mutation_id = _canonical_uuid(mutation_id, "Mutation ID")
    candidate = copy.deepcopy(job)
    metadata = copy.deepcopy(_metadata(candidate))
    metadata[SHOPOS_DOCUMENT_REVISION_KEY] = prior_revision + 1
    metadata[SHOPOS_LAST_MUTATION_KEY] = canonical_mutation_id
    candidate[SHOPOS_METADATA_KEY] = metadata
    validate_traveler_structure(candidate)
    return candidate


def confirm_local_save_metadata(
    job: dict[str, Any], *, prior_revision: int
) -> dict[str, Any]:
    """Add one confirmed desktop revision without inventing a server request ID.

    A coordinated local compatibility save preserves any previously recorded
    ``last_applied_mutation_id`` exactly.  Legacy travelers therefore gain only
    the revision and identity metadata required by their first confirmed save.
    """
    if (
        isinstance(prior_revision, bool)
        or not isinstance(prior_revision, int)
        or prior_revision < 0
    ):
        raise TravelerValidationError("The prior revision is invalid.")
    candidate = copy.deepcopy(job)
    metadata = copy.deepcopy(_metadata(candidate))
    metadata[SHOPOS_DOCUMENT_REVISION_KEY] = prior_revision + 1
    candidate[SHOPOS_METADATA_KEY] = metadata
    validate_traveler_structure(candidate)
    return candidate


def deterministic_value_hash(value: Any) -> str:
    """Hash one finite JSON value for same-field optimistic concurrency."""
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise TravelerValidationError("The field value is not valid finite JSON.") from error
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def blank_programming_operation(operation_number: int) -> dict[str, Any]:
    return {
        "operation_number": operation_number,
        "operation_type": "",
        "program_name": "",
        "revision": "",
        "status": "Pending",
        "last_updated": "",
        "notes": "",
    }


def blank_cnc_operation(operation_number: int) -> dict[str, Any]:
    return {
        "operation_number": operation_number,
        "operator": "",
        "machine": "",
        "qty_complete": 0,
        "status": "Pending",
        "last_updated": "",
        "notes": "",
    }


def infer_operation_type(*values: object) -> str:
    """Infer a legacy operation type without inventing a machine."""
    for value in values:
        if value in ALLOWED_OPERATIONS:
            return str(value)
        if value in MILLING_MACHINES:
            return "Mill"
        if value in TURNING_MACHINES:
            return "Turning"
    return ""


def operation_by_number(
    operations: list[dict[str, Any]], operation_number: int
) -> dict[str, Any] | None:
    for operation in operations:
        if operation.get("operation_number") == operation_number:
            return operation
    return None


def normalize_operations(job: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized copy; merely loading a legacy job never rewrites it.

    The permissive conversions here intentionally retain the desktop applications'
    historical behavior. Call :func:`validate_traveler_structure` before using
    untrusted storage when malformed protected fields must be rejected.
    """
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
        requested_count = int(
            programming.get("operation_count", len(programming_operations))
        )
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
                "qty_complete": cnc.get(
                    "qty_complete", cnc.get("qty_completed", 0)
                ),
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
                    inspection.get("operation"),
                    programming_operations[0]["operation_type"],
                ),
                "machine": inspection.get("machine")
                or cnc_operations[0].get("machine", ""),
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


def canonical_job(job: dict[str, Any]) -> dict[str, Any]:
    """Return the existing save schema while retaining compatible unknown fields."""
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


def operation_has_data(
    operation: object, ignored: tuple[str, ...] = ("operation_number",)
) -> bool:
    if not isinstance(operation, dict):
        return False
    for key, value in operation.items():
        if key in ignored:
            continue
        if value not in ("", None, 0, "Pending", []):
            return True
    return False


def resize_operation_plan(
    job: dict[str, Any], new_count: int, confirm_removal: bool = False
) -> dict[str, Any]:
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
                blank_cnc_operation(
                    len(normalized["cnc_machining"]["operations"]) + 1
                )
            )
    programming["operation_count"] = new_count
    return normalized


def get_required_quantity(job: dict[str, Any]) -> int | None:
    try:
        return int(job.get("qty_to_make"))
    except (TypeError, ValueError):
        return None


def get_cnc_status(
    job: dict[str, Any], qty_completed: int, current_status: str
) -> str:
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


def blank_if_missing(job: dict[str, Any], section: str, key: str) -> object:
    value = job.get(section, {}).get(key)
    return BLANK if value == "" or value is None else value


def status_if_missing(job: dict[str, Any], section: str) -> str:
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


def operation_if_missing(job: dict[str, Any], section: str) -> object:
    value = job.get(section, {}).get("operation")
    if value == "" or value is None or value not in ALLOWED_OPERATIONS:
        return BLANK
    return value


def job_field(job: dict[str, Any], key: str) -> object:
    value = job.get(key)
    return BLANK if value == "" or value is None else value


def _first_available_value(job: dict[str, Any], names: tuple[str, ...]) -> object:
    for name in names:
        if name not in job:
            continue
        value = job[name]
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        return value
    return None


def _required_identifier(value: object, label: str) -> str:
    if isinstance(value, bool) or isinstance(value, (dict, list)):
        raise TravelerValidationError(f"{label} must be text or a whole number")
    if not isinstance(value, (str, int)):
        raise TravelerValidationError(f"{label} must be text or a whole number")
    normalized = str(value).strip()
    if not normalized:
        raise TravelerValidationError(f"{label} is required")
    return normalized


def _positive_whole_number(value: object) -> int:
    if isinstance(value, bool):
        raise TravelerValidationError("Quantity to make must be a positive whole number")
    if isinstance(value, int):
        quantity = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise TravelerValidationError(
                "Quantity to make must be a positive whole number"
            )
        quantity = int(value)
    elif isinstance(value, str):
        try:
            quantity = int(value.strip())
        except (TypeError, ValueError) as error:
            raise TravelerValidationError(
                "Quantity to make must be a positive whole number"
            ) from error
    else:
        raise TravelerValidationError("Quantity to make must be a positive whole number")
    if quantity <= 0:
        raise TravelerValidationError("Quantity to make must be a positive whole number")
    return quantity


def _optional_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def parts_count_projection(job: object) -> dict[str, Any]:
    """Return the established Parts Count header projection from a traveler."""
    if not isinstance(job, dict):
        raise TravelerValidationError("The job file must contain a JSON object")
    return {
        "job_number": _required_identifier(
            _first_available_value(job, ("job_number", "job_num")), "Job number"
        ),
        "part_number": _required_identifier(
            _first_available_value(job, ("part_number", "part_num")), "Part number"
        ),
        "qty_to_make": _positive_whole_number(
            _first_available_value(job, ("qty_to_make", "part_total", "quantity"))
        ),
        "customer": _optional_text(job.get("customer")),
        "description": _optional_text(job.get("description")),
    }


def _validate_operation_rows(section: dict[str, Any], key: str, label: str) -> None:
    if key not in section:
        return
    rows = section[key]
    if not isinstance(rows, list):
        raise TravelerValidationError(f"{label}.{key} must contain a JSON array.")
    seen: set[int] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise TravelerValidationError(
                f"{label}.{key}[{index}] must contain a JSON object."
            )
        if "operation_number" not in row:
            continue
        number = row["operation_number"]
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            raise TravelerValidationError(
                f"{label}.{key}[{index}].operation_number must be a positive whole number."
            )
        if number in seen:
            raise TravelerValidationError(
                f"{label}.{key} contains duplicate operation number {number}."
            )
        seen.add(number)


def _validate_shopos_metadata(job: dict[str, Any]) -> None:
    metadata = job.get(SHOPOS_METADATA_KEY)
    if metadata is None:
        return
    if not isinstance(metadata, dict):
        raise TravelerValidationError("_shopos must contain a JSON object.")
    revision = document_revision(job)
    mutation_id = last_applied_mutation_id(job)
    if revision == 0 and mutation_id is not None:
        raise TravelerValidationError(
            "A last applied mutation ID requires a positive document revision."
        )
    _validated_identity_maps(job)
    is_authoritatively_closed(job)


def validate_traveler_structure(value: object) -> None:
    """Validate protected current/legacy structure without normalizing the source."""
    if not isinstance(value, dict):
        raise TravelerValidationError("The Job Traveler file must contain a JSON object.")
    parts_count_projection(value)
    for section_name in SECTIONS:
        if section_name in value and not isinstance(value[section_name], dict):
            raise TravelerValidationError(
                f"Traveler section '{section_name}' must contain a JSON object."
            )
    programming = value.get("programming", {})
    cnc = value.get("cnc_machining", {})
    inspection = value.get("inspection", {})
    if isinstance(programming, dict):
        _validate_operation_rows(programming, "operations", "programming")
        if "operation_count" in programming:
            raw_count = programming["operation_count"]
            try:
                count = int(raw_count)
            except (TypeError, ValueError) as error:
                raise TravelerValidationError(
                    "programming.operation_count must be a positive whole number."
                ) from error
            if isinstance(raw_count, bool) or count <= 0:
                raise TravelerValidationError(
                    "programming.operation_count must be a positive whole number."
                )
    if isinstance(cnc, dict):
        _validate_operation_rows(cnc, "operations", "cnc_machining")
    if isinstance(inspection, dict):
        _validate_operation_rows(inspection, "records", "inspection")
    _validate_shopos_metadata(value)


def _editable_field_rule(section: str, field: str) -> tuple[str, str]:
    if field in PROTECTED_MUTATION_FIELDS or field.startswith("_"):
        raise TravelerValidationError("The requested field is protected.")
    if field in SECTION_EDITABLE_FIELDS.get(section, {}):
        return "section", SECTION_EDITABLE_FIELDS[section][field]
    if field in OPERATION_EDITABLE_FIELDS.get(section, {}):
        return "operation", OPERATION_EDITABLE_FIELDS[section][field]
    raise TravelerValidationError("The requested field is not editable.")


def _validate_edit_value(
    rule: str,
    value: Any,
    *,
    normalized_job: dict[str, Any],
    operation_number: int | None,
) -> Any:
    if rule in ("text", "notes"):
        if not isinstance(value, str):
            raise TravelerValidationError("The new field value must be text.")
        maximum = 4_000 if rule == "notes" else 500
        if len(value) > maximum or any(
            ord(character) < 32 and character not in "\n\r\t" for character in value
        ):
            raise TravelerValidationError("The new field value is invalid.")
        return value.strip()
    if rule == "quantity":
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TravelerValidationError(
                "The new field value must be a non-negative whole number."
            )
        return value
    if rule == "machine":
        if value not in ALL_MACHINES:
            raise TravelerValidationError("The selected machine is not permitted.")
        if operation_number is None:
            raise TravelerValidationError("A machine requires a machining operation.")
        programming = operation_by_number(
            normalized_job["programming"]["operations"], operation_number
        ) or {}
        operation_type = programming.get("operation_type")
        allowed = (
            MILLING_MACHINES
            if operation_type == "Mill"
            else TURNING_MACHINES if operation_type == "Turning" else []
        )
        if value not in allowed:
            raise TravelerValidationError(
                "The selected machine does not match the programmed operation type."
            )
        return value
    raise TravelerValidationError("The editable-field rule is invalid.")


def _resolve_target(
    job: dict[str, Any], target: object
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(target, dict):
        raise TravelerValidationError("The mutation target must be an object.")
    if set(target) not in (
        {"section", "field", "operation_id"},
        {"section", "field", "compatibility_reference"},
    ):
        raise TravelerValidationError(
            "The mutation target requires section, field, and one operation reference."
        )
    section = target.get("section")
    field = target.get("field")
    if not isinstance(section, str) or section not in SECTIONS:
        raise TravelerValidationError("The mutation target section is invalid.")
    if not isinstance(field, str) or not field or len(field) > 100:
        raise TravelerValidationError("The mutation target field is invalid.")
    kind, rule = _editable_field_rule(section, field)
    normalized = canonical_job(job)
    machining, sections = _validated_identity_maps(normalized)
    operation_number: int | None = None

    if "operation_id" in target:
        identity = _canonical_uuid(target["operation_id"], "Operation ID")
        if kind == "section":
            if sections.get(section) != identity:
                raise TravelerValidationError("The stable operation identity is invalid.")
        else:
            matches = [int(number) for number, value in machining.items() if value == identity]
            if len(matches) != 1:
                raise TravelerValidationError("The stable operation identity is invalid.")
            operation_number = matches[0]
    else:
        reference = target.get("compatibility_reference")
        if not isinstance(reference, str):
            raise TravelerValidationError("The compatibility reference is invalid.")
        expected_prefix = f"{section}:operation:"
        if kind == "section":
            if reference != section:
                raise TravelerValidationError("The compatibility reference is invalid.")
        else:
            if not reference.startswith(expected_prefix):
                raise TravelerValidationError("The compatibility reference is invalid.")
            raw_number = reference[len(expected_prefix):]
            if not raw_number.isdigit() or int(raw_number) <= 0:
                raise TravelerValidationError("The compatibility reference is invalid.")
            operation_number = int(raw_number)

    if kind == "section":
        container = normalized[section]
    else:
        key = "records" if section == "inspection" else "operations"
        container = operation_by_number(normalized[section][key], operation_number or 0)
        if container is None:
            raise TravelerValidationError("The referenced operation does not exist.")

    current_value = copy.deepcopy(container.get(field, ""))
    state = {
        "section": section,
        "field": field,
        "operation_number": operation_number,
        "value": current_value,
        "value_hash": deterministic_value_hash(current_value),
        "target_kind": kind,
        "validation_rule": rule,
    }
    return normalized, {"container": container, "state": state}


def ordinary_field_state(job: object, target: object) -> dict[str, Any]:
    """Return the authoritative current value/hash for one allowed target."""
    validate_traveler_structure(job)
    assert isinstance(job, dict)
    _normalized, resolved = _resolve_target(job, target)
    state = copy.deepcopy(resolved["state"])
    state.pop("validation_rule", None)
    return state


def apply_ordinary_field_change(
    job: object, target: object, new_value: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply one allowlisted field change to a canonical copy only."""
    validate_traveler_structure(job)
    assert isinstance(job, dict)
    normalized, resolved = _resolve_target(job, target)
    state = resolved["state"]
    confirmed_value = _validate_edit_value(
        state["validation_rule"],
        new_value,
        normalized_job=normalized,
        operation_number=state["operation_number"],
    )
    resolved["container"][state["field"]] = copy.deepcopy(confirmed_value)
    validate_traveler_structure(normalized)
    confirmed_state = copy.deepcopy(state)
    confirmed_state["value"] = copy.deepcopy(confirmed_value)
    confirmed_state["value_hash"] = deterministic_value_hash(confirmed_value)
    confirmed_state.pop("validation_rule", None)
    return normalized, confirmed_state


def section_statuses(job: dict[str, Any]) -> dict[str, str]:
    """Return current derived status for each of the seven sections."""
    normalized = normalize_operations(job)
    return {section: status_if_missing(normalized, section) for section in SECTIONS}


def _derived_status(value: object) -> str:
    return str(value) if value in ALLOWED_STATUSES else "Pending"


def operation_descriptors(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Return Task-relevant descriptors with stable IDs when already persisted.

    Legacy travelers retain explicitly temporary compatibility coordinates.  A
    read never generates or persists identities.
    """
    normalized = normalize_operations(job)
    machining_identities, section_identities = _validated_identity_maps(normalized)
    programming = normalized["programming"]["operations"]
    cnc = normalized["cnc_machining"]["operations"]
    inspection = normalized["inspection"]["records"]
    descriptors: list[dict[str, Any]] = []

    def append_fixed(section: str) -> None:
        stable_identity = section_identities.get(section)
        descriptors.append(
            {
                "compatibility_reference": section,
                "stable_operation_id": stable_identity,
                "operation_reference": stable_identity or section,
                "reference_stability": (
                    "stable_uuid" if stable_identity else "temporary_positional"
                ),
                "section": section,
                "operation_number": None,
                "operation_type": None,
                "machine": None,
                "status": status_if_missing(normalized, section),
            }
        )

    for row in programming:
        number = row["operation_number"]
        stable_identity = machining_identities.get(str(number))
        compatibility_reference = f"programming:operation:{number}"
        descriptors.append(
            {
                "compatibility_reference": compatibility_reference,
                "stable_operation_id": stable_identity,
                "operation_reference": stable_identity or compatibility_reference,
                "reference_stability": (
                    "stable_uuid" if stable_identity else "temporary_positional"
                ),
                "section": "programming",
                "operation_number": number,
                "operation_type": row.get("operation_type", ""),
                "machine": None,
                "status": _derived_status(row.get("status")),
            }
        )
    append_fixed("saw_cutting")
    for row in cnc:
        number = row["operation_number"]
        programming_row = operation_by_number(programming, number) or {}
        stable_identity = machining_identities.get(str(number))
        compatibility_reference = f"cnc_machining:operation:{number}"
        descriptors.append(
            {
                "compatibility_reference": compatibility_reference,
                "stable_operation_id": stable_identity,
                "operation_reference": stable_identity or compatibility_reference,
                "reference_stability": (
                    "stable_uuid" if stable_identity else "temporary_positional"
                ),
                "section": "cnc_machining",
                "operation_number": number,
                "operation_type": programming_row.get("operation_type", ""),
                "machine": row.get("machine", ""),
                "status": _derived_status(row.get("status")),
            }
        )
    append_fixed("deburr")
    inspection_by_number = {
        row.get("operation_number"): row for row in inspection if isinstance(row, dict)
    }
    for programming_row in programming:
        number = programming_row["operation_number"]
        row = inspection_by_number.get(number, {})
        cnc_row = operation_by_number(cnc, number) or {}
        stable_identity = machining_identities.get(str(number))
        compatibility_reference = f"inspection:operation:{number}"
        descriptors.append(
            {
                "compatibility_reference": compatibility_reference,
                "stable_operation_id": stable_identity,
                "operation_reference": stable_identity or compatibility_reference,
                "reference_stability": (
                    "stable_uuid" if stable_identity else "temporary_positional"
                ),
                "section": "inspection",
                "operation_number": number,
                "operation_type": row.get(
                    "operation_type", programming_row.get("operation_type", "")
                ),
                "machine": row.get("machine", cnc_row.get("machine", "")),
                "status": _derived_status(row.get("status")),
            }
        )
    append_fixed("packing")
    append_fixed("shipping")
    return descriptors


def operation_reference_contract(job: object) -> str:
    """Describe whether the current read model has complete stable identities."""
    validate_traveler_structure(job)
    assert isinstance(job, dict)
    descriptors = operation_descriptors(job)
    if descriptors and all(
        descriptor["reference_stability"] == "stable_uuid"
        for descriptor in descriptors
    ):
        return "stable_uuid_v1"
    if any(
        descriptor["reference_stability"] == "stable_uuid"
        for descriptor in descriptors
    ):
        return "mixed_compatibility_v1"
    return "temporary_positional_v1"


def read_model(job: object) -> dict[str, Any]:
    """Validate and build the reusable normalized and derived read model."""
    validate_traveler_structure(job)
    assert isinstance(job, dict)
    normalized = normalize_operations(job)
    return {
        "normalized": normalized,
        "section_statuses": section_statuses(normalized),
        "operations": operation_descriptors(normalized),
        "operation_identities": stable_identity_projection(normalized),
        "document_revision": document_revision(normalized),
        "operation_reference_contract": operation_reference_contract(normalized),
        "authoritatively_closed": is_authoritatively_closed(normalized),
    }


__all__ = [
    "ALLOWED_OPERATIONS",
    "ALLOWED_STATUSES",
    "ALL_MACHINES",
    "BLANK",
    "DOMAIN_CONTRACT_NAME",
    "DOMAIN_CONTRACT_VERSION",
    "MILLING_MACHINES",
    "SECTIONS",
    "SECTION_EDITABLE_FIELDS",
    "OPERATION_EDITABLE_FIELDS",
    "PROTECTED_MUTATION_FIELDS",
    "SHOPOS_METADATA_KEY",
    "TURNING_MACHINES",
    "TravelerValidationError",
    "apply_ordinary_field_change",
    "blank_cnc_operation",
    "blank_if_missing",
    "blank_programming_operation",
    "bootstrap_stable_identities",
    "canonical_job",
    "confirm_local_save_metadata",
    "confirm_mutation_metadata",
    "deterministic_value_hash",
    "document_revision",
    "get_cnc_status",
    "get_required_quantity",
    "infer_operation_type",
    "is_authoritatively_closed",
    "job_field",
    "last_applied_mutation_id",
    "normalize_operations",
    "operation_by_number",
    "operation_descriptors",
    "operation_reference_contract",
    "operation_has_data",
    "operation_if_missing",
    "ordinary_field_state",
    "parts_count_projection",
    "read_model",
    "resize_operation_plan",
    "section_statuses",
    "stable_identity_projection",
    "status_if_missing",
    "validate_traveler_structure",
]
