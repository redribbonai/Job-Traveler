"""Phase 2C service-client protocol, retry, and no-fallback tests."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
import uuid


os.environ.setdefault("JOB_TRAVELER_TEST_PROCESS", "job-traveler-tests")
os.environ.setdefault("JOB_TRAVELER_TEST_WRITE_ROOTS", tempfile.gettempdir())

import requests

import traveler_domain as domain
from traveler_client import (
    AmbiguousTransportError,
    ClientAuthenticationError,
    ClientConflictError,
    ClientFeatureDisabledError,
    ClientNetworkDeniedError,
    ClientShrinkConfirmationRequired,
    ClientTimeoutError,
    ClientUnavailableError,
    ClientValidationError,
    HttpsEnforcementError,
    EmployeeIdentity,
    TravelerClient,
)
from traveler_persistence import (
    PlanResizeConfirmationRequired,
    PersistenceConfigurationError,
    TravelerSnapshot,
    UnsupportedPersistenceAction,
    build_persistence,
)
from traveler_service_persistence import ServiceTravelerPersistence


TOKEN = "temporary-test-bearer-secret"
REQUEST_ONE = "00000000-0000-4000-8000-000000000001"
REQUEST_TWO = "00000000-0000-4000-8000-000000000002"


def fixture():
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent
        / "test_fixtures"
        / "canonical"
        / "SANITIZED-MULTI.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def projection(document=None, version=None, revision=0):
    document = copy.deepcopy(document or fixture())
    if revision:
        metadata = copy.deepcopy(document.get("_shopos", {}))
        metadata["document_revision"] = revision
        document["_shopos"] = metadata
    version = version or "sha256:" + "a" * 64
    return {
        "job_number": document["job_number"],
        "read_version": version,
        "persisted": copy.deepcopy(document),
        "normalized": domain.normalize_operations(document),
        "derived": {
            "section_statuses": domain.section_statuses(document),
            "operations": domain.operation_descriptors(document),
            "closure": {"available": False, "value": None, "reason": "not_persisted"},
            "operation_reference_contract": domain.operation_reference_contract(document),
            "operation_identities": domain.stable_identity_projection(document),
            "document_revision": revision,
        },
    }


class FakeResponse:
    def __init__(self, status, body, *, etag=None):
        self.status_code = status
        self._body = copy.deepcopy(body)
        self.content = json.dumps(body).encode("utf-8")
        self.headers = {"Content-Type": "application/json"}
        if etag is not None:
            self.headers["ETag"] = etag

    def json(self):
        return copy.deepcopy(self._body)


class ScriptedTransport:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, copy.deepcopy(kwargs)))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return outcome(method, url, kwargs)
        return outcome


def command_response(request_id, command="set_field", *, document=None, revision=1):
    view = projection(
        document=document,
        version="sha256:" + f"{revision:x}" * 64,
        revision=revision,
    )
    body = {
        "request_id": request_id,
        "resource_id": "traveler:SANITIZED-MULTI",
        "command": command,
        "applied": True,
        "replayed": False,
        "no_op": False,
        "field": {
            "section": "programming",
            "field": "program_name",
            "operation_number": 1,
            "value": "UPDATED",
            "value_hash": domain.deterministic_value_hash("UPDATED"),
            "target_kind": "operation",
            "stable_operation_id": None,
        },
        "document_revision": revision,
        "read_version": view["read_version"],
        "traveler": view,
    }
    return FakeResponse(200, body)


def new_traveler(job_number="NEW-SAFE-JOB", operation_count=2, request_id=REQUEST_ONE):
    header = {
        "job_number": job_number,
        "customer": "Sanitized Customer",
        "part_number": "SAFE-PART",
        "description": "Temporary test traveler",
        "qty_to_make": 10,
        "material": "6061-T6",
        "cut_length": "3.00",
    }
    section_inputs = {section: {} for section in domain.SECTIONS}
    document = domain.build_new_traveler(header, section_inputs, operation_count)
    ids = (str(uuid.UUID(int=index)) for index in range(1, 100))
    document = domain.bootstrap_stable_identities(document, lambda: next(ids))
    document = domain.confirm_mutation_metadata(
        document, prior_revision=0, mutation_id=request_id
    )
    return header, section_inputs, document


def structural_response(
    request_id,
    command,
    *,
    document,
    operation_count,
    status=200,
    replayed=False,
):
    view = projection(
        document=document,
        version="sha256:" + "b" * 64,
        revision=domain.document_revision(document),
    )
    return FakeResponse(
        status,
        {
            "request_id": request_id,
            "resource_id": f"traveler:{document['job_number']}",
            "command": command,
            "applied": not replayed,
            "replayed": replayed,
            "no_op": False,
            "operation_count": operation_count,
            "document_revision": domain.document_revision(document),
            "read_version": view["read_version"],
            "traveler": view,
        },
    )


def planner_identity():
    return EmployeeIdentity(
        username="Planner",
        display_name=None,
        approved=True,
        enabled=True,
        capabilities=("job_planner",),
    )


class TravelerClientTests(unittest.TestCase):
    def test_https_and_bounded_configuration_fail_closed(self):
        with self.assertRaises(HttpsEnforcementError):
            TravelerClient("http://shop.example.test", TOKEN)
        with self.assertRaises(HttpsEnforcementError):
            TravelerClient("https://user:secret@shop.example.test", TOKEN)
        with self.assertRaises(ClientValidationError):
            TravelerClient("https://shop.example.test", TOKEN, read_timeout=120)

    def test_authenticated_read_requires_matching_strong_etag(self):
        view = projection()
        transport = ScriptedTransport(
            FakeResponse(200, {"traveler": view}, etag=f'"{view["read_version"]}"')
        )
        client = TravelerClient(
            "https://shop.example.test", TOKEN, transport=transport
        )
        traveler = client.get_traveler("SANITIZED-MULTI")
        self.assertEqual(traveler.read_version, view["read_version"])
        headers = transport.calls[0][2]["headers"]
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")
        self.assertIs(transport.calls[0][2]["allow_redirects"], False)

        bad = TravelerClient(
            "https://shop.example.test",
            TOKEN,
            transport=ScriptedTransport(
                FakeResponse(200, {"traveler": view}, etag='W/"mismatch"')
            ),
        )
        with self.assertRaises(ClientValidationError):
            bad.get_traveler("SANITIZED-MULTI")

    def test_typed_auth_network_feature_timeout_and_dependency_errors(self):
        cases = (
            (FakeResponse(401, {"error": {"code": "invalid_session"}}), ClientAuthenticationError),
            (FakeResponse(403, {"error": {"code": "untrusted_network"}}), ClientNetworkDeniedError),
            (FakeResponse(404, {"error": {"code": "not_found"}}), ClientFeatureDisabledError),
            (FakeResponse(503, {"error": {"code": "database_unavailable"}}), ClientUnavailableError),
            (requests.exceptions.ConnectTimeout("timeout"), ClientTimeoutError),
        )
        for outcome, expected in cases:
            with self.subTest(expected=expected.__name__):
                client = TravelerClient(
                    "https://shop.example.test",
                    TOKEN,
                    transport=ScriptedTransport(outcome),
                )
                with self.assertRaises(expected):
                    client.get_traveler("SANITIZED-MULTI")

    def test_ambiguous_retry_reuses_exact_request_id_and_redacts_secret(self):
        leaking_transport_error = requests.exceptions.ReadTimeout(
            f"do not expose Authorization: Bearer {TOKEN}"
        )
        transport = ScriptedTransport(
            leaking_transport_error,
            command_response(REQUEST_ONE),
        )
        client = TravelerClient(
            "https://shop.example.test",
            TOKEN,
            transport=transport,
            request_id_factory=lambda: REQUEST_ONE,
        )
        with self.assertRaises(AmbiguousTransportError) as raised:
            client.set_field(
                "SANITIZED-MULTI",
                target={
                    "section": "programming",
                    "field": "program_name",
                    "compatibility_reference": "programming:operation:1",
                },
                base_value_hash="sha256:" + "0" * 64,
                new_value="UPDATED",
            )
        error = raised.exception
        self.assertNotIn(TOKEN, str(error))
        self.assertNotIn(TOKEN, repr(error.pending))
        self.assertIsNone(error.__cause__)
        result = client.retry_ambiguous(error)
        self.assertEqual(result.request_id, REQUEST_ONE)
        bodies = [call[2]["json"] for call in transport.calls]
        self.assertEqual(bodies[0], bodies[1])

    def test_deliberate_replacement_is_a_new_user_request(self):
        transport = ScriptedTransport(
            command_response(REQUEST_ONE),
            command_response(REQUEST_TWO, command="replace_field_after_conflict"),
        )
        ids = iter((REQUEST_ONE, REQUEST_TWO))
        client = TravelerClient(
            "https://shop.example.test",
            TOKEN,
            transport=transport,
            request_id_factory=lambda: next(ids),
        )
        target = {
            "section": "programming",
            "field": "program_name",
            "compatibility_reference": "programming:operation:1",
        }
        first = client.set_field(
            "SANITIZED-MULTI",
            target=target,
            base_value_hash="sha256:" + "0" * 64,
            new_value="UPDATED",
        )
        second = client.replace_field_after_conflict(
            "SANITIZED-MULTI",
            target=target,
            latest_value_hash="sha256:" + "1" * 64,
            new_value="UPDATED",
        )
        self.assertNotEqual(first.request_id, second.request_id)

    def test_login_validation_logout_and_safe_identity_projection(self):
        transport = ScriptedTransport(
            FakeResponse(
                200,
                {
                    "session": {
                        "token": TOKEN,
                        "token_type": "Bearer",
                        "expires_at": "2026-08-01T00:00:00+00:00",
                    },
                    "account": {
                        "username": "Planner",
                        "display_name": None,
                        "approved": True,
                        "enabled": True,
                        "capabilities": ["job_planner"],
                    },
                },
            ),
            FakeResponse(
                200,
                {
                    "account": {
                        "username": "Planner",
                        "display_name": None,
                        "approved": True,
                        "enabled": True,
                        "capabilities": ["job_planner"],
                    }
                },
            ),
            FakeResponse(204, {}),
        )
        client = TravelerClient("https://shop.example.test", transport=transport)
        login = client.login("Planner", "2468")
        client.set_bearer_session(login.token, login.employee)
        self.assertTrue(client.validate_session().is_job_planner)
        client.logout()
        with self.assertRaises(ClientAuthenticationError):
            client.get_traveler("SAFE-JOB")
        self.assertNotIn(TOKEN, repr(login))

    def test_typed_create_resize_confirmation_and_ambiguous_replay(self):
        header, sections, created_document = new_traveler()
        expanded = domain.resize_operation_plan_structural(created_document, 3)
        expanded = domain.bootstrap_stable_identities(
            expanded, lambda: "00000000-0000-4000-8000-000000000099"
        )
        expanded = domain.confirm_mutation_metadata(
            expanded, prior_revision=1, mutation_id=REQUEST_TWO
        )
        confirmation = {
            "document_revision": 2,
            "read_version": "sha256:" + "b" * 64,
            "current_operation_count": 3,
            "requested_operation_count": 2,
            "removed_operations": [
                {
                    "operation_number": 3,
                    "operation_id": "00000000-0000-4000-8000-000000000099",
                    "contains_meaningful_data": False,
                    "meaningful_sections": [],
                }
            ],
        }
        lost = requests.exceptions.ReadTimeout(f"must redact {TOKEN}")
        transport = ScriptedTransport(
            structural_response(
                REQUEST_ONE,
                "create_traveler",
                document=created_document,
                operation_count=2,
                status=201,
            ),
            lost,
            structural_response(
                REQUEST_TWO,
                "resize_plan",
                document=expanded,
                operation_count=3,
                replayed=True,
            ),
            FakeResponse(
                409,
                {
                    "error": {"code": "shrink_confirmation_required"},
                    "current": confirmation,
                },
            ),
        )
        ids = iter((REQUEST_ONE, REQUEST_TWO, str(uuid.uuid4())))
        client = TravelerClient(
            "https://shop.example.test",
            TOKEN,
            transport=transport,
            request_id_factory=lambda: next(ids),
        )
        client.set_bearer_session(TOKEN, planner_identity())
        created = client.create_traveler(
            header=header, section_inputs=sections, operation_count=2
        )
        self.assertEqual(created.document_revision, 1)
        with self.assertRaises(AmbiguousTransportError) as raised:
            client.resize_plan(
                "NEW-SAFE-JOB",
                operation_count=3,
                document_revision=1,
                read_version=created.read_version,
            )
        replayed = client.retry_ambiguous(raised.exception)
        self.assertTrue(replayed.replayed)
        self.assertEqual(
            transport.calls[1][2]["json"], transport.calls[2][2]["json"]
        )
        with self.assertRaises(ClientShrinkConfirmationRequired) as shrink:
            client.resize_plan(
                "NEW-SAFE-JOB",
                operation_count=2,
                document_revision=2,
                read_version="sha256:" + "b" * 64,
            )
        self.assertEqual(
            shrink.exception.confirmation["removed_operations"][0]["operation_number"],
            3,
        )


class ServicePersistenceTests(unittest.TestCase):
    def client_for(self, *responses, ids=(REQUEST_ONE, REQUEST_TWO)):
        iterator = iter(ids)
        return TravelerClient(
            "https://shop.example.test",
            TOKEN,
            transport=ScriptedTransport(*responses),
            request_id_factory=lambda: next(iterator),
        )

    def snapshot(self):
        document = domain.canonical_job(fixture())
        return TravelerSnapshot(
            job_number=document["job_number"],
            traveler=document,
            read_version="sha256:" + "a" * 64,
            document_revision=0,
            etag='"sha256:' + "a" * 64 + '"',
        )

    def test_true_one_field_save_uses_narrow_command_and_never_local_fallback(self):
        response_document = fixture()
        response_document["programming"]["operations"][0]["program_name"] = "UPDATED"
        client = self.client_for(command_response(REQUEST_ONE, document=response_document))
        persistence = ServiceTravelerPersistence(client)
        base = self.snapshot()
        intended = copy.deepcopy(base.traveler)
        intended["programming"]["operations"][0]["program_name"] = "UPDATED"
        result = persistence.save(base, intended)
        self.assertTrue(result.changed)
        body = client.transport.calls[0][2]["json"]
        self.assertEqual(body["command"], "set_field")
        self.assertNotIn("document", body["payload"])
        with self.assertRaises(UnsupportedPersistenceAction):
            persistence.create(intended)
        with self.assertRaises(PersistenceConfigurationError):
            build_persistence(
                mode="service", jobs_directory=tempfile.gettempdir(), service_client=client
            )

    def test_structural_actions_require_planner_identity_before_http(self):
        client = self.client_for()
        persistence = ServiceTravelerPersistence(client)
        base = self.snapshot()
        intended = domain.resize_operation_plan(base.traveler, 3)
        with self.assertRaises(UnsupportedPersistenceAction):
            persistence.save(base, intended, action="plan_resize")
        with self.assertRaises(UnsupportedPersistenceAction):
            persistence.resize_plan(base, 3)
        self.assertFalse(client.transport.calls)

    def test_planner_persistence_create_and_resize_confirmation(self):
        header, sections, created_document = new_traveler()
        created_response = structural_response(
            REQUEST_ONE,
            "create_traveler",
            document=created_document,
            operation_count=2,
            status=201,
        )
        confirmation = {
            "document_revision": 1,
            "read_version": "sha256:" + "b" * 64,
            "current_operation_count": 2,
            "requested_operation_count": 1,
            "removed_operations": [
                {
                    "operation_number": 2,
                    "operation_id": created_document["_shopos"]
                    ["operation_identities"]["machining_operations"]["2"],
                    "contains_meaningful_data": False,
                    "meaningful_sections": [],
                }
            ],
        }
        client = self.client_for(
            created_response,
            FakeResponse(
                409,
                {
                    "error": {"code": "shrink_confirmation_required"},
                    "current": confirmation,
                },
            ),
        )
        client.set_bearer_session(TOKEN, planner_identity())
        persistence = ServiceTravelerPersistence(client)
        candidate = domain.build_new_traveler(header, sections, 2)
        created = persistence.create(candidate)
        self.assertTrue(created.changed)
        with self.assertRaises(PlanResizeConfirmationRequired) as raised:
            persistence.resize_plan(created.snapshot, 1)
        self.assertEqual(
            raised.exception.operation_ids,
            [confirmation["removed_operations"][0]["operation_id"]],
        )


if __name__ == "__main__":
    unittest.main()
