"""ShopOS API implementation of the desktop persistence boundary."""

from __future__ import annotations

import copy
from typing import Any

import traveler_domain as domain
from traveler_client import (
    ClientAlreadyExistsError,
    ClientConflictError,
    ClientNotFoundError,
    ClientPlanConflictError,
    ClientShrinkConfirmationRequired,
    ClientTraveler,
    TravelerClient,
    TravelerClientError,
)
from traveler_persistence import (
    ConflictField,
    PersistenceConflict,
    PersistenceAlreadyExistsError,
    PersistenceNotFoundError,
    PersistenceError,
    PersistenceStorageError,
    PersistenceValidationError,
    PlanResizeConfirmationRequired,
    PlanResizeConflict,
    SaveResult,
    TravelerPersistence,
    TravelerSnapshot,
    TravelerSummary,
    UnsupportedPersistenceAction,
    rebase_conflict_intent,
)


def _value_at(document: dict[str, Any], path: tuple[str | int, ...]) -> Any:
    current: Any = document
    for component in path:
        current = current[component]
    return copy.deepcopy(current)


def _snapshot(response: ClientTraveler | dict[str, Any]) -> TravelerSnapshot:
    projection = response.projection if isinstance(response, ClientTraveler) else response
    try:
        persisted = projection["persisted"]
        traveler = domain.canonical_job(persisted)
        domain.validate_traveler_structure(traveler)
        job_number = domain.parts_count_projection(persisted)["job_number"]
        read_version = projection["read_version"]
        revision = projection["derived"]["document_revision"]
        if domain.document_revision(persisted) != revision:
            raise PersistenceValidationError(
                "The service traveler revision is inconsistent."
            )
    except (KeyError, TypeError, ValueError, RecursionError) as error:
        raise PersistenceValidationError("The service traveler response is invalid.") from error
    etag = response.etag if isinstance(response, ClientTraveler) else f'"{read_version}"'
    return TravelerSnapshot(
        job_number=job_number,
        traveler=traveler,
        read_version=read_version,
        document_revision=revision,
        etag=etag,
    )


def _target_specs(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only Phase 2B allowlisted targets and their desktop JSON paths."""
    canonical = domain.canonical_job(document)
    identities = domain.stable_identity_projection(canonical)
    specs: list[dict[str, Any]] = []
    for section, fields in domain.SECTION_EDITABLE_FIELDS.items():
        section_identity = identities["sections"].get(section)
        reference = (
            {"operation_id": section_identity}
            if section_identity
            else {"compatibility_reference": section}
        )
        for field in fields:
            target = {"section": section, "field": field, **reference}
            state = domain.ordinary_field_state(canonical, target)
            specs.append(
                {
                    "target": target,
                    "path": (section, field),
                    "state": state,
                }
            )
    for section, fields in domain.OPERATION_EDITABLE_FIELDS.items():
        key = "records" if section == "inspection" else "operations"
        rows = canonical[section][key]
        for index, row in enumerate(rows):
            number = row["operation_number"]
            operation_identity = identities["machining_operations"].get(str(number))
            reference = (
                {"operation_id": operation_identity}
                if operation_identity
                else {"compatibility_reference": f"{section}:operation:{number}"}
            )
            for field in fields:
                target = {"section": section, "field": field, **reference}
                state = domain.ordinary_field_state(canonical, target)
                specs.append(
                    {
                        "target": target,
                        "path": (section, key, index, field),
                        "state": state,
                    }
                )
    return specs


def _allowed_changes(
    base: TravelerSnapshot, intended: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        candidate = domain.canonical_job(intended)
        domain.validate_traveler_structure(candidate)
        if domain.parts_count_projection(candidate)["job_number"] != base.job_number:
            raise PersistenceValidationError("A save cannot change the job number.")
    except PersistenceValidationError:
        raise
    except (TypeError, ValueError, RecursionError) as error:
        raise PersistenceValidationError("The intended Job Traveler is invalid.") from error

    reconstructed = copy.deepcopy(base.traveler)
    changes: list[dict[str, Any]] = []
    for spec in _target_specs(base.traveler):
        try:
            intended_value = _value_at(candidate, spec["path"])
        except (KeyError, IndexError, TypeError):
            intended_value = ""
        state = spec["state"]
        if intended_value == state["value"]:
            continue
        try:
            reconstructed, _confirmed = domain.apply_ordinary_field_change(
                reconstructed, spec["target"], intended_value
            )
        except (TypeError, ValueError, RecursionError) as error:
            raise PersistenceValidationError("The intended field value is invalid.") from error
        changes.append(
            {
                "target": copy.deepcopy(spec["target"]),
                "base_value_hash": state["value_hash"],
                "new_value": copy.deepcopy(intended_value),
                "path": spec["path"],
                "base_value": copy.deepcopy(state["value"]),
            }
        )

    # Any remaining difference is structural, protected, unknown-field mutation,
    # or a status/timestamp workflow intentionally not authorized in Phase 2C.
    if domain.canonical_job(reconstructed) != candidate:
        raise UnsupportedPersistenceAction(
            "Service mode cannot save this structural or protected traveler action."
        )
    return candidate, changes


class ServiceTravelerPersistence(TravelerPersistence):
    """Explicit opt-in API mode; it contains no filesystem fallback."""

    mode = "service"

    def __init__(self, client: TravelerClient) -> None:
        if not isinstance(client, TravelerClient):
            raise PersistenceValidationError("An authenticated TravelerClient is required.")
        self.client = client

    @property
    def job_planner_authorized(self) -> bool:
        return self.client.has_capability("job_planner")

    @staticmethod
    def _service_failure(error: TravelerClientError):
        if isinstance(error, ClientNotFoundError):
            return PersistenceNotFoundError(error.public_message)
        return PersistenceStorageError(error.public_message)

    def load(self, job_number: object) -> TravelerSnapshot:
        if not isinstance(job_number, str):
            raise PersistenceValidationError("The job number is invalid.")
        try:
            return _snapshot(self.client.get_traveler(job_number))
        except TravelerClientError as error:
            raise self._service_failure(error) from error

    def list_summaries(self) -> list[TravelerSummary]:
        try:
            items = self.client.list_travelers()
        except TravelerClientError as error:
            raise self._service_failure(error) from error
        summaries = []
        for item in items:
            try:
                identity = item["persisted_identity"]
                statuses = item["derived"]["section_statuses"]
                values = list(statuses.values())
                status = (
                    "Completed"
                    if values and all(value == "Completed" for value in values)
                    else "In Progress"
                    if any(value in {"In Progress", "Completed"} for value in values)
                    else "Pending"
                )
                summaries.append(
                    TravelerSummary(
                        job_number=item["job_number"],
                        customer=identity.get("customer", ""),
                        part_number=identity.get("part_number", ""),
                        quantity=identity.get("quantity", ""),
                        status=status,
                    )
                )
            except (KeyError, TypeError, AttributeError) as error:
                raise PersistenceValidationError(
                    "The service traveler list response is invalid."
                ) from error
        return summaries

    def create(self, traveler: dict[str, Any], *, overwrite: bool = False) -> SaveResult:
        del overwrite  # The service never overwrites, regardless of desktop legacy intent.
        if not self.job_planner_authorized:
            raise UnsupportedPersistenceAction(
                "Job Traveler creation requires the Job Planner capability."
            )
        try:
            candidate = domain.canonical_job(traveler)
            if "_shopos" in candidate:
                raise PersistenceValidationError(
                    "Creation cannot supply ShopOS metadata."
                )
            header_names = {
                "job_number",
                "customer",
                "part_number",
                "description",
                "qty_to_make",
                "material",
                "cut_length",
            }
            if set(candidate) != header_names | set(domain.SECTIONS):
                raise PersistenceValidationError(
                    "The intended new Job Traveler contains unsupported fields."
                )
            header = {name: copy.deepcopy(candidate[name]) for name in header_names}
            operation_count = candidate["programming"]["operation_count"]
            section_inputs = {
                section: {
                    field: copy.deepcopy(candidate[section][field])
                    for field in domain.SECTION_EDITABLE_FIELDS.get(section, {})
                    if field in candidate[section]
                }
                for section in domain.SECTIONS
            }
            reconstructed = domain.build_new_traveler(
                header, section_inputs, operation_count
            )
            if reconstructed != candidate:
                raise PersistenceValidationError(
                    "The intended new Job Traveler is not a supported creation shape."
                )
        except PersistenceValidationError:
            raise
        except (KeyError, TypeError, ValueError, RecursionError) as error:
            raise PersistenceValidationError(
                "The intended new Job Traveler is invalid."
            ) from error
        try:
            result = self.client.create_traveler(
                header=header,
                section_inputs=section_inputs,
                operation_count=operation_count,
            )
        except ClientAlreadyExistsError as error:
            raise PersistenceAlreadyExistsError(error.public_message) from error
        except TravelerClientError as error:
            raise self._service_failure(error) from error
        return self._result(result)

    def resize_plan(
        self,
        base: TravelerSnapshot,
        operation_count: int,
        *,
        confirm_removed_operation_ids: list[str] | None = None,
    ) -> SaveResult:
        if not self.job_planner_authorized:
            raise UnsupportedPersistenceAction(
                "Operation-plan resizing requires the Job Planner capability."
            )
        try:
            result = self.client.resize_plan(
                base.job_number,
                operation_count=operation_count,
                document_revision=base.document_revision,
                read_version=base.read_version,
                confirm_removed_operation_ids=confirm_removed_operation_ids,
            )
        except ClientShrinkConfirmationRequired as error:
            confirmation = error.confirmation
            try:
                removed = confirmation["removed_operations"]
                if (
                    confirmation["document_revision"] != base.document_revision
                    or confirmation["read_version"] != base.read_version
                    or confirmation["requested_operation_count"] != operation_count
                    or not isinstance(removed, list)
                    or not removed
                ):
                    raise KeyError
            except (KeyError, TypeError):
                raise PersistenceValidationError(
                    "The shrink confirmation response is invalid."
                ) from error
            raise PlanResizeConfirmationRequired(
                requested_count=operation_count,
                document_revision=base.document_revision,
                read_version=base.read_version,
                removed_operations=removed,
            ) from error
        except ClientPlanConflictError as error:
            try:
                latest = self.load(base.job_number)
            except PersistenceError:
                raise PersistenceStorageError(
                    "The latest operation plan could not be loaded."
                ) from error
            raise PlanResizeConflict(latest) from error
        except TravelerClientError as error:
            raise self._service_failure(error) from error
        return self._result(result)

    def _conflict(
        self,
        error: ClientConflictError,
        base: TravelerSnapshot,
        intended: dict[str, Any],
        changes: list[dict[str, Any]],
    ) -> PersistenceConflict:
        del error
        try:
            latest = _snapshot(self.client.get_traveler(base.job_number))
        except TravelerClientError as read_error:
            raise self._service_failure(read_error) from read_error
        latest_specs = {
            (
                spec["target"]["section"],
                spec["target"]["field"],
                spec["state"]["operation_number"],
            ): spec
            for spec in _target_specs(latest.traveler)
        }
        conflicts = []
        for change in changes:
            base_state = domain.ordinary_field_state(base.traveler, change["target"])
            key = (
                base_state["section"],
                base_state["field"],
                base_state["operation_number"],
            )
            current_spec = latest_specs.get(key)
            if current_spec is None:
                raise UnsupportedPersistenceAction(
                    "The edited operation no longer exists in service mode."
                )
            current_state = current_spec["state"]
            if current_state["value_hash"] == change["base_value_hash"]:
                continue
            conflicts.append(
                ConflictField(
                    path=change["path"],
                    base_value=copy.deepcopy(change["base_value"]),
                    intended_value=copy.deepcopy(change["new_value"]),
                    authoritative_value=copy.deepcopy(current_state["value"]),
                    authoritative_hash=current_state["value_hash"],
                    target=copy.deepcopy(current_spec["target"]),
                )
            )
        if not conflicts:
            raise PersistenceStorageError("The service conflict response could not be confirmed.")
        return PersistenceConflict(conflicts, latest, base_snapshot=base)

    @staticmethod
    def _result(result) -> SaveResult:
        snapshot = _snapshot(result.traveler)
        if snapshot.read_version != result.read_version:
            raise PersistenceValidationError("The saved traveler response is inconsistent.")
        return SaveResult(snapshot=snapshot, changed=result.applied)

    def save(
        self,
        base: TravelerSnapshot,
        intended: dict[str, Any],
        *,
        action: str = "logical_save",
    ) -> SaveResult:
        if action == "plan_resize":
            raise UnsupportedPersistenceAction(
                "Use the dedicated operation-plan resize action in service mode."
            )
        if action != "logical_save":
            raise UnsupportedPersistenceAction("The requested service action is unsupported.")
        _candidate, changes = _allowed_changes(base, intended)
        if not changes:
            try:
                latest = _snapshot(self.client.get_traveler(base.job_number))
            except TravelerClientError as error:
                raise self._service_failure(error) from error
            return SaveResult(snapshot=latest, changed=False)
        try:
            if len(changes) == 1:
                change = changes[0]
                result = self.client.set_field(
                    base.job_number,
                    target=change["target"],
                    base_value_hash=change["base_value_hash"],
                    new_value=change["new_value"],
                )
            else:
                result = self.client.set_fields(
                    base.job_number,
                    changes=[
                        {
                            "target": change["target"],
                            "base_value_hash": change["base_value_hash"],
                            "new_value": change["new_value"],
                        }
                        for change in changes
                    ],
                )
        except ClientConflictError as error:
            raise self._conflict(error, base, intended, changes) from error
        except TravelerClientError as error:
            raise self._service_failure(error) from error
        return self._result(result)

    def resolve_conflict(
        self,
        conflict: PersistenceConflict,
        intended: dict[str, Any],
        *,
        action: str = "logical_save",
    ) -> SaveResult:
        if action != "logical_save":
            raise UnsupportedPersistenceAction("The requested service action is unsupported.")
        rebased = rebase_conflict_intent(conflict, intended)
        _candidate, changes = _allowed_changes(conflict.latest_snapshot, rebased)
        if not changes:
            return SaveResult(conflict.latest_snapshot, changed=False)
        replaced = [
            field
            for field in conflict.conflicts
            if _value_at(rebased, field.path) == field.intended_value
            and field.intended_value != field.authoritative_value
        ]
        try:
            if len(changes) == 1 and len(replaced) == 1:
                change = changes[0]
                field = replaced[0]
                result = self.client.replace_field_after_conflict(
                    conflict.latest_snapshot.job_number,
                    target=field.target or change["target"],
                    latest_value_hash=field.authoritative_hash,
                    new_value=change["new_value"],
                )
                return self._result(result)
        except ClientConflictError as error:
            raise self._conflict(
                error, conflict.latest_snapshot, rebased, changes
            ) from error
        except TravelerClientError as error:
            raise self._service_failure(error) from error
        return self.save(conflict.latest_snapshot, rebased, action=action)


__all__ = ["ServiceTravelerPersistence"]
