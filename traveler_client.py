"""HTTPS-only ShopOS employee-session and Job Traveler service client.

Bearer tokens remain process memory only. Persistent remembering belongs to the
separately injected desktop credential-store boundary, and mutations are never
retried automatically.
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote, urlsplit

import requests


READ_VERSION = re.compile(r"^sha256:[0-9a-f]{64}$")
CAPABILITY_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class TravelerClientError(RuntimeError):
    code = "client_failure"
    public_message = "The Job Traveler service request could not be completed."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.public_message)


class HttpsEnforcementError(TravelerClientError, ValueError):
    code = "https_required"
    public_message = "Service mode requires a validated HTTPS endpoint."


class ClientAuthenticationError(TravelerClientError):
    code = "authentication_required"
    public_message = "An approved employee session is required."


class ClientAuthorizationError(TravelerClientError):
    code = "authorization_denied"
    public_message = "The employee session is not authorized for this request."


class ClientFeatureDisabledError(TravelerClientError):
    code = "feature_disabled"
    public_message = "Job Traveler service access is disabled."


class ClientNetworkDeniedError(TravelerClientError):
    code = "network_denied"
    public_message = "The trusted shop network did not permit this request."


class ClientValidationError(TravelerClientError, ValueError):
    code = "validation_failed"
    public_message = "The Job Traveler service rejected invalid data."


class ClientNotFoundError(TravelerClientError, LookupError):
    code = "job_not_found"
    public_message = "The Job Traveler was not found."


class ClientUnavailableError(TravelerClientError):
    code = "dependency_unavailable"
    public_message = "The Job Traveler service is temporarily unavailable."


class ClientTimeoutError(TravelerClientError, TimeoutError):
    code = "request_timeout"
    public_message = "The Job Traveler service request timed out."


@dataclass(frozen=True, repr=False)
class PendingMutation:
    """Safe retry material for one ambiguous logical mutation (never auth data)."""

    job_number: str
    request_id: str
    command: str
    payload: dict[str, Any]

    def __repr__(self) -> str:
        return (
            f"PendingMutation(job_number={self.job_number!r}, "
            f"request_id={self.request_id!r}, command={self.command!r})"
        )


class AmbiguousTransportError(TravelerClientError):
    code = "ambiguous_transport"
    public_message = (
        "The server outcome is unknown. Retry only with the original request ID."
    )

    def __init__(self, pending: PendingMutation) -> None:
        self.pending = pending
        super().__init__()


class ClientConflictError(TravelerClientError):
    code = "field_conflict"
    public_message = "A traveler field changed after it was read."

    def __init__(
        self,
        *,
        current: dict[str, Any],
        pending: PendingMutation,
    ) -> None:
        self.current = copy.deepcopy(current)
        self.pending = pending
        super().__init__()


class ClientPlanConflictError(ClientConflictError):
    code = "plan_conflict"
    public_message = "The operation plan changed after it was read."


class ClientAlreadyExistsError(TravelerClientError):
    code = "job_already_exists"
    public_message = "A Job Traveler already exists for that job number."


class ClientShrinkConfirmationRequired(TravelerClientError):
    code = "shrink_confirmation_required"
    public_message = "Confirm the exact operations that would be removed."

    def __init__(self, *, confirmation: dict[str, Any], pending: PendingMutation):
        self.confirmation = copy.deepcopy(confirmation)
        self.pending = pending
        super().__init__()


@dataclass(frozen=True)
class EmployeeIdentity:
    username: str
    display_name: str | None
    approved: bool
    enabled: bool
    capabilities: tuple[str, ...]

    @property
    def is_job_planner(self) -> bool:
        return "job_planner" in self.capabilities


@dataclass(frozen=True, repr=False)
class LoginResult:
    token: str
    expires_at: str
    employee: EmployeeIdentity

    def __repr__(self) -> str:
        return (
            f"LoginResult(expires_at={self.expires_at!r}, employee={self.employee!r}, "
            "token=<redacted>)"
        )


@dataclass(frozen=True)
class ClientTraveler:
    projection: dict[str, Any]
    etag: str

    @property
    def job_number(self) -> str:
        return self.projection["job_number"]

    @property
    def read_version(self) -> str:
        return self.projection["read_version"]


@dataclass(frozen=True)
class CommandResult:
    request_id: str
    command: str
    applied: bool
    replayed: bool
    no_op: bool
    document_revision: int
    read_version: str
    traveler: dict[str, Any]
    fields: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class StructuralCommandResult:
    request_id: str
    command: str
    applied: bool
    replayed: bool
    no_op: bool
    operation_count: int
    document_revision: int
    read_version: str
    traveler: dict[str, Any]


def _canonical_request_id(value: object) -> str:
    if not isinstance(value, str):
        raise ClientValidationError("A canonical request ID is required.")
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, AttributeError) as error:
        raise ClientValidationError("A canonical request ID is required.") from error
    if canonical != value:
        raise ClientValidationError("A canonical request ID is required.")
    return canonical


def _safe_base_url(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise HttpsEnforcementError()
    parsed = urlsplit(value)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise HttpsEnforcementError()
    path = parsed.path.rstrip("/")
    origin = f"https://{parsed.netloc}{path}"
    return origin


def _safe_token(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 4096
        or any(character.isspace() or ord(character) < 33 for character in value)
    ):
        raise ClientAuthenticationError()
    return value


def _employee_identity(value: Any) -> EmployeeIdentity:
    if not isinstance(value, dict) or set(value) != {
        "username",
        "display_name",
        "approved",
        "enabled",
        "capabilities",
    }:
        raise ClientValidationError("The employee session response is invalid.")
    username = value["username"]
    display_name = value["display_name"]
    capabilities = value["capabilities"]
    if (
        not isinstance(username, str)
        or not username
        or username != username.strip()
        or (display_name is not None and not isinstance(display_name, str))
        or not isinstance(value["approved"], bool)
        or not isinstance(value["enabled"], bool)
        or not isinstance(capabilities, list)
        or not all(
            isinstance(item, str) and CAPABILITY_NAME.fullmatch(item)
            for item in capabilities
        )
        or len(capabilities) != len(set(capabilities))
    ):
        raise ClientValidationError("The employee session response is invalid.")
    return EmployeeIdentity(
        username=username,
        display_name=display_name,
        approved=value["approved"],
        enabled=value["enabled"],
        capabilities=tuple(capabilities),
    )


class TravelerClient:
    """Small synchronous client with explicit safe retry semantics."""

    def __init__(
        self,
        base_url: str,
        bearer_session: str | Callable[[], str] | None = None,
        *,
        transport=None,
        connect_timeout: float = 3.0,
        read_timeout: float = 10.0,
        max_response_bytes: int = 2_000_000,
        request_id_factory: Callable[[], str] | None = None,
        authorization_denied_callback: Callable[[], None] | None = None,
    ) -> None:
        self.base_url = _safe_base_url(base_url)
        if callable(bearer_session):
            self._token_supplier = bearer_session
        elif isinstance(bearer_session, str):
            self._in_memory_token: str | None = _safe_token(bearer_session)
            self._token_supplier = lambda: self._in_memory_token or ""
        elif bearer_session is None:
            self._in_memory_token = None
            self._token_supplier = lambda: self._in_memory_token or ""
        else:
            raise ClientAuthenticationError()
        if (
            isinstance(connect_timeout, bool)
            or isinstance(read_timeout, bool)
            or not 0 < connect_timeout <= 30
            or not 0 < read_timeout <= 60
        ):
            raise ClientValidationError("Service timeouts are outside safe bounds.")
        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or not 1_024 <= max_response_bytes <= 10_000_000
        ):
            raise ClientValidationError("The response size limit is invalid.")
        self.connect_timeout = float(connect_timeout)
        self.read_timeout = float(read_timeout)
        self.max_response_bytes = max_response_bytes
        self.transport = transport or requests.Session()
        self.request_id_factory = request_id_factory or (lambda: str(uuid.uuid4()))
        self.authorization_denied_callback = authorization_denied_callback
        self._identity: EmployeeIdentity | None = None

    def __repr__(self) -> str:
        return f"TravelerClient(base_url={self.base_url!r}, bearer_session=<redacted>)"

    @property
    def employee(self) -> EmployeeIdentity | None:
        return self._identity

    def has_capability(self, capability: str) -> bool:
        return self._identity is not None and capability in self._identity.capabilities

    def set_bearer_session(
        self, token: str, employee: EmployeeIdentity | None = None
    ) -> None:
        self._in_memory_token = _safe_token(token)
        self._token_supplier = lambda: self._in_memory_token or ""
        self._identity = employee

    def clear_bearer_session(self) -> None:
        self._in_memory_token = None
        self._token_supplier = lambda: ""
        self._identity = None

    def _headers(self, *, authenticated: bool = True) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if not authenticated:
            return headers
        try:
            token = _safe_token(self._token_supplier())
        except ClientAuthenticationError:
            raise
        except Exception:
            raise ClientAuthenticationError() from None
        headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _error_code(body: Any) -> str | None:
        if not isinstance(body, dict):
            return None
        error = body.get("error")
        return error.get("code") if isinstance(error, dict) else None

    def _json_body(self, response) -> dict[str, Any]:
        content = getattr(response, "content", b"")
        if isinstance(content, str):
            content = content.encode("utf-8")
        if not isinstance(content, (bytes, bytearray)) or len(content) > self.max_response_bytes:
            raise ClientValidationError("The service response is invalid.")
        content_type = str(getattr(response, "headers", {}).get("Content-Type", ""))
        if "application/json" not in content_type.casefold():
            raise ClientValidationError("The service response is invalid.")
        try:
            body = response.json()
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise ClientValidationError("The service response is invalid.") from error
        if not isinstance(body, dict):
            raise ClientValidationError("The service response is invalid.")
        return body

    def _raise_response_error(
        self,
        response,
        body: dict[str, Any],
        *,
        pending: PendingMutation | None,
    ) -> None:
        status = int(getattr(response, "status_code", 0))
        code = self._error_code(body)
        if status == 401:
            raise ClientAuthenticationError()
        if status == 403 and code == "untrusted_network":
            raise ClientNetworkDeniedError()
        if status == 403:
            if pending is not None and self.authorization_denied_callback is not None:
                try:
                    self.authorization_denied_callback()
                except Exception:
                    pass
            raise ClientAuthorizationError()
        if status == 404 and code == "job_not_found":
            raise ClientNotFoundError()
        if status == 404 and code in {
            "not_found",
            "mutations_disabled",
            "structural_mutations_disabled",
        }:
            raise ClientFeatureDisabledError()
        if status == 409 and code == "field_conflict" and pending is not None:
            current = body.get("current")
            if not isinstance(current, dict):
                raise ClientValidationError("The conflict response is invalid.")
            raise ClientConflictError(current=current, pending=pending)
        if status == 409 and code == "plan_conflict" and pending is not None:
            current = body.get("current")
            if not isinstance(current, dict):
                raise ClientValidationError("The plan conflict response is invalid.")
            raise ClientPlanConflictError(current=current, pending=pending)
        if (
            status == 409
            and code == "shrink_confirmation_required"
            and pending is not None
        ):
            confirmation = body.get("current")
            if not isinstance(confirmation, dict):
                raise ClientValidationError(
                    "The shrink confirmation response is invalid."
                )
            raise ClientShrinkConfirmationRequired(
                confirmation=confirmation, pending=pending
            )
        if status == 409 and code == "job_already_exists":
            raise ClientAlreadyExistsError()
        if status in {400, 413, 415, 422}:
            raise ClientValidationError()
        if status in {408, 504}:
            raise ClientTimeoutError()
        if status == 423 or status >= 500:
            raise ClientUnavailableError()
        if status == 409:
            raise ClientConflictError(current={}, pending=pending) if pending else ClientValidationError()
        raise ClientValidationError("The service returned an unexpected response.")

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        pending: PendingMutation | None = None,
        authenticated: bool = True,
        expect_json: bool = True,
    ):
        url = self.base_url + path
        headers = self._headers(authenticated=authenticated)
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = self.transport.request(
                method,
                url,
                headers=headers,
                json=copy.deepcopy(body) if body is not None else None,
                timeout=(self.connect_timeout, self.read_timeout),
                allow_redirects=False,
            )
        except requests.exceptions.SSLError:
            raise HttpsEnforcementError() from None
        except requests.exceptions.ConnectTimeout:
            raise ClientTimeoutError() from None
        except requests.exceptions.ReadTimeout:
            if pending is not None:
                raise AmbiguousTransportError(pending) from None
            raise ClientTimeoutError() from None
        except requests.exceptions.ConnectionError:
            if pending is not None:
                raise AmbiguousTransportError(pending) from None
            raise ClientNetworkDeniedError() from None
        except requests.exceptions.Timeout:
            if pending is not None:
                raise AmbiguousTransportError(pending) from None
            raise ClientTimeoutError() from None
        except requests.exceptions.RequestException:
            if pending is not None:
                raise AmbiguousTransportError(pending) from None
            raise ClientUnavailableError() from None
        status = int(getattr(response, "status_code", 0))
        response_body = (
            self._json_body(response)
            if expect_json and status != 304
            else {}
        )
        if not 200 <= status < 300:
            self._raise_response_error(response, response_body, pending=pending)
        return response, response_body

    @staticmethod
    def _validate_projection(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ClientValidationError("The traveler response is invalid.")
        required = {"job_number", "read_version", "persisted", "normalized", "derived"}
        if set(value) != required:
            raise ClientValidationError("The traveler response is invalid.")
        if not isinstance(value["job_number"], str) or not value["job_number"]:
            raise ClientValidationError("The traveler response is invalid.")
        if READ_VERSION.fullmatch(value["read_version"]) is None:
            raise ClientValidationError("The traveler response is invalid.")
        if not isinstance(value["persisted"], dict) or not isinstance(value["normalized"], dict):
            raise ClientValidationError("The traveler response is invalid.")
        derived = value["derived"]
        if not isinstance(derived, dict):
            raise ClientValidationError("The traveler response is invalid.")
        revision = derived.get("document_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ClientValidationError("The traveler response is invalid.")
        metadata = value["persisted"].get("_shopos")
        persisted_revision = (
            metadata.get("document_revision", 0) if isinstance(metadata, dict) else 0
        )
        if persisted_revision != revision:
            raise ClientValidationError("The traveler response is invalid.")
        return copy.deepcopy(value)

    def login(self, username: object, pin: object) -> LoginResult:
        if (
            not isinstance(username, str)
            or not username
            or username != username.strip()
            or len(username) > 64
            or not isinstance(pin, str)
            or len(pin) != 4
            or not pin.isascii()
            or not pin.isdigit()
        ):
            raise ClientAuthenticationError()
        request_body = {"username": username, "pin": pin}
        try:
            _response, body = self._request(
                "POST",
                "/api/v1/auth/login",
                body=request_body,
                authenticated=False,
            )
        finally:
            request_body.clear()
        if set(body) != {"session", "account"}:
            raise ClientValidationError("The employee login response is invalid.")
        session = body["session"]
        if not isinstance(session, dict) or set(session) != {
            "token",
            "token_type",
            "expires_at",
        }:
            raise ClientValidationError("The employee login response is invalid.")
        token = _safe_token(session["token"])
        expires_at = session["expires_at"]
        if session["token_type"] != "Bearer" or not isinstance(expires_at, str):
            raise ClientValidationError("The employee login response is invalid.")
        employee = _employee_identity(body["account"])
        if not employee.approved or not employee.enabled:
            raise ClientAuthenticationError()
        return LoginResult(token=token, expires_at=expires_at, employee=employee)

    def validate_session(self) -> EmployeeIdentity:
        _response, body = self._request("GET", "/api/v1/auth/me")
        if set(body) != {"account"}:
            raise ClientValidationError("The employee session response is invalid.")
        employee = _employee_identity(body["account"])
        if not employee.approved or not employee.enabled:
            raise ClientAuthenticationError()
        self._identity = employee
        return employee

    def logout(self) -> None:
        try:
            response, _body = self._request(
                "POST", "/api/v1/auth/logout", expect_json=False
            )
            if int(getattr(response, "status_code", 0)) != 204:
                raise ClientValidationError("The employee logout response is invalid.")
        finally:
            self.clear_bearer_session()

    def list_travelers(self) -> list[dict[str, Any]]:
        _response, body = self._request("GET", "/api/v1/jobs")
        if set(body) != {"jobs", "count"} or not isinstance(body["jobs"], list):
            raise ClientValidationError("The traveler list response is invalid.")
        if body["count"] != len(body["jobs"]):
            raise ClientValidationError("The traveler list response is invalid.")
        result = []
        for item in body["jobs"]:
            if not isinstance(item, dict) or READ_VERSION.fullmatch(item.get("read_version", "")) is None:
                raise ClientValidationError("The traveler list response is invalid.")
            result.append(copy.deepcopy(item))
        return result

    def get_traveler(self, job_number: str) -> ClientTraveler:
        if not isinstance(job_number, str) or not job_number or job_number != job_number.strip():
            raise ClientValidationError("The job number is invalid.")
        response, body = self._request(
            "GET", f"/api/v1/jobs/{quote(job_number, safe='')}/traveler"
        )
        if set(body) != {"traveler"}:
            raise ClientValidationError("The traveler response is invalid.")
        projection = self._validate_projection(body["traveler"])
        etag = str(getattr(response, "headers", {}).get("ETag", ""))
        expected = f'"{projection["read_version"]}"'
        if etag.startswith("W/") or etag != expected:
            raise ClientValidationError("The traveler response lacks a matching strong ETag.")
        return ClientTraveler(projection=projection, etag=etag)

    def _new_request_id(self) -> str:
        return _canonical_request_id(self.request_id_factory())

    def _command(self, pending: PendingMutation) -> CommandResult:
        request_id = _canonical_request_id(pending.request_id)
        request_body = {
            "request_id": request_id,
            "command": pending.command,
            "payload": copy.deepcopy(pending.payload),
        }
        _response, body = self._request(
            "POST",
            f"/api/v1/jobs/{quote(pending.job_number, safe='')}/traveler/commands",
            body=request_body,
            pending=pending,
        )
        required = {
            "request_id",
            "resource_id",
            "command",
            "applied",
            "replayed",
            "no_op",
            "document_revision",
            "read_version",
            "traveler",
        }
        if not required.issubset(body) or body["request_id"] != request_id:
            raise ClientValidationError("The mutation response is invalid.")
        if (
            body["command"] != pending.command
            or body["resource_id"] != f"traveler:{pending.job_number}"
        ):
            raise ClientValidationError("The mutation response is invalid.")
        if not all(isinstance(body[name], bool) for name in ("applied", "replayed", "no_op")):
            raise ClientValidationError("The mutation response is invalid.")
        if (
            body["applied"] and (body["replayed"] or body["no_op"])
            or body["replayed"] and body["applied"]
            or not body["applied"] and not body["replayed"] and not body["no_op"]
        ):
            raise ClientValidationError("The mutation response is invalid.")
        projection = self._validate_projection(body["traveler"])
        revision = body["document_revision"]
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 0
            or revision != projection["derived"]["document_revision"]
            or body["read_version"] != projection["read_version"]
        ):
            raise ClientValidationError("The mutation response is invalid.")
        if "field" in body and "fields" in body:
            raise ClientValidationError("The mutation response is invalid.")
        if "field" in body:
            fields = (copy.deepcopy(body["field"]),)
        elif "fields" in body and isinstance(body["fields"], list):
            fields = tuple(copy.deepcopy(body["fields"]))
        else:
            raise ClientValidationError("The mutation response is invalid.")
        if not fields or not all(isinstance(field, dict) for field in fields):
            raise ClientValidationError("The mutation response is invalid.")
        expected_fields = (
            len(pending.payload.get("changes", []))
            if pending.command == "set_fields"
            else 1
        )
        if len(fields) != expected_fields or projection["job_number"] != pending.job_number:
            raise ClientValidationError("The mutation response is invalid.")
        return CommandResult(
            request_id=request_id,
            command=pending.command,
            applied=body["applied"],
            replayed=body["replayed"],
            no_op=body["no_op"],
            document_revision=revision,
            read_version=body["read_version"],
            traveler=projection,
            fields=fields,
        )

    def _structural_command(
        self, pending: PendingMutation
    ) -> StructuralCommandResult:
        request_id = _canonical_request_id(pending.request_id)
        request_body = {
            "request_id": request_id,
            "command": pending.command,
            "payload": copy.deepcopy(pending.payload),
        }
        path = (
            "/api/v1/jobs/commands"
            if pending.command == "create_traveler"
            else f"/api/v1/jobs/{quote(pending.job_number, safe='')}/traveler/commands"
        )
        _response, body = self._request(
            "POST", path, body=request_body, pending=pending
        )
        required = {
            "request_id",
            "resource_id",
            "command",
            "applied",
            "replayed",
            "no_op",
            "operation_count",
            "document_revision",
            "read_version",
            "traveler",
        }
        if set(body) != required or body["request_id"] != request_id:
            raise ClientValidationError("The structural mutation response is invalid.")
        if (
            body["command"] != pending.command
            or body["resource_id"] != f"traveler:{pending.job_number}"
            or not all(
                isinstance(body[name], bool)
                for name in ("applied", "replayed", "no_op")
            )
        ):
            raise ClientValidationError("The structural mutation response is invalid.")
        if (
            body["applied"] and (body["replayed"] or body["no_op"])
            or body["replayed"] and body["applied"]
            or not body["applied"]
            and not body["replayed"]
            and not body["no_op"]
        ):
            raise ClientValidationError("The structural mutation response is invalid.")
        projection = self._validate_projection(body["traveler"])
        operation_count = body["operation_count"]
        revision = body["document_revision"]
        if (
            isinstance(operation_count, bool)
            or not isinstance(operation_count, int)
            or operation_count < 1
            or projection["normalized"].get("programming", {}).get(
                "operation_count"
            )
            != operation_count
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision != projection["derived"]["document_revision"]
            or body["read_version"] != projection["read_version"]
            or projection["job_number"] != pending.job_number
        ):
            raise ClientValidationError("The structural mutation response is invalid.")
        return StructuralCommandResult(
            request_id=request_id,
            command=pending.command,
            applied=body["applied"],
            replayed=body["replayed"],
            no_op=body["no_op"],
            operation_count=operation_count,
            document_revision=revision,
            read_version=body["read_version"],
            traveler=projection,
        )

    def create_traveler(
        self,
        *,
        header: dict[str, Any],
        section_inputs: dict[str, Any],
        operation_count: int,
        request_id: str | None = None,
    ) -> StructuralCommandResult:
        if not isinstance(header, dict) or not isinstance(header.get("job_number"), str):
            raise ClientValidationError("The traveler creation header is invalid.")
        pending = PendingMutation(
            job_number=header["job_number"],
            request_id=request_id or self._new_request_id(),
            command="create_traveler",
            payload={
                "contract_version": 1,
                "header": copy.deepcopy(header),
                "section_inputs": copy.deepcopy(section_inputs),
                "operation_count": operation_count,
            },
        )
        return self._structural_command(pending)

    def resize_plan(
        self,
        job_number: str,
        *,
        operation_count: int,
        document_revision: int,
        read_version: str,
        confirm_removed_operation_ids: list[str] | None = None,
        request_id: str | None = None,
    ) -> StructuralCommandResult:
        payload: dict[str, Any] = {
            "contract_version": 1,
            "operation_count": operation_count,
            "document_revision": document_revision,
            "read_version": read_version,
        }
        if confirm_removed_operation_ids is not None:
            payload["confirm_removed_operation_ids"] = copy.deepcopy(
                confirm_removed_operation_ids
            )
        pending = PendingMutation(
            job_number=job_number,
            request_id=request_id or self._new_request_id(),
            command="resize_plan",
            payload=payload,
        )
        return self._structural_command(pending)

    def set_field(
        self,
        job_number: str,
        *,
        target: dict[str, Any],
        base_value_hash: str,
        new_value: str | int,
        request_id: str | None = None,
    ) -> CommandResult:
        pending = PendingMutation(
            job_number=job_number,
            request_id=request_id or self._new_request_id(),
            command="set_field",
            payload={
                "target": copy.deepcopy(target),
                "base_value_hash": base_value_hash,
                "new_value": copy.deepcopy(new_value),
            },
        )
        return self._command(pending)

    def replace_field_after_conflict(
        self,
        job_number: str,
        *,
        target: dict[str, Any],
        latest_value_hash: str,
        new_value: str | int,
        request_id: str | None = None,
    ) -> CommandResult:
        pending = PendingMutation(
            job_number=job_number,
            request_id=request_id or self._new_request_id(),
            command="replace_field_after_conflict",
            payload={
                "target": copy.deepcopy(target),
                "base_value_hash": latest_value_hash,
                "new_value": copy.deepcopy(new_value),
            },
        )
        return self._command(pending)

    def set_fields(
        self,
        job_number: str,
        *,
        changes: list[dict[str, Any]],
        request_id: str | None = None,
    ) -> CommandResult:
        pending = PendingMutation(
            job_number=job_number,
            request_id=request_id or self._new_request_id(),
            command="set_fields",
            payload={"changes": copy.deepcopy(changes)},
        )
        return self._command(pending)

    def retry_ambiguous(
        self, error: AmbiguousTransportError
    ) -> CommandResult | StructuralCommandResult:
        if not isinstance(error, AmbiguousTransportError):
            raise ClientValidationError("Only an ambiguous mutation can be retried.")
        if error.pending.command in {"create_traveler", "resize_plan"}:
            return self._structural_command(error.pending)
        return self._command(error.pending)


__all__ = [
    "AmbiguousTransportError",
    "ClientAuthenticationError",
    "ClientAlreadyExistsError",
    "ClientAuthorizationError",
    "ClientConflictError",
    "ClientFeatureDisabledError",
    "ClientNetworkDeniedError",
    "ClientNotFoundError",
    "ClientPlanConflictError",
    "ClientShrinkConfirmationRequired",
    "ClientTimeoutError",
    "ClientTraveler",
    "ClientUnavailableError",
    "ClientValidationError",
    "CommandResult",
    "EmployeeIdentity",
    "HttpsEnforcementError",
    "LoginResult",
    "PendingMutation",
    "StructuralCommandResult",
    "TravelerClient",
    "TravelerClientError",
]
