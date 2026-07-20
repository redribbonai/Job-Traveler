"""Secure desktop employee-session behavior with fake credential stores only."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from unittest import mock


os.environ.setdefault("JOB_TRAVELER_TEST_PROCESS", "job-traveler-tests")
os.environ.setdefault("JOB_TRAVELER_TEST_WRITE_ROOTS", tempfile.gettempdir())

import requests

import desktop_session
from desktop_session import (
    CredentialBackendUnavailable,
    CredentialStoreError,
    DesktopSessionManager,
    KeyringCredentialStore,
)
from traveler_client import (
    ClientAuthorizationError,
    EmployeeIdentity,
    TravelerClient,
)


TOKEN = "temporary-remembered-session-token-with-sufficient-length"
PIN = "2468"


def account(*, planner=False):
    return {
        "username": "Operator",
        "display_name": "Safe Operator",
        "approved": True,
        "enabled": True,
        "capabilities": ["job_planner"] if planner else [],
    }


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = copy.deepcopy(body or {})
        self.content = json.dumps(body or {}).encode("utf-8")
        self.headers = {"Content-Type": "application/json"}

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
        return outcome


class FakeCredentialStore:
    def __init__(self, token=None, *, fail_load=False, fail_save=False, fail_delete=False):
        self.token = token
        self.fail_load = fail_load
        self.fail_save = fail_save
        self.fail_delete = fail_delete
        self.saved = []
        self.delete_calls = 0

    def load(self):
        if self.fail_load:
            raise CredentialStoreError()
        return self.token

    def save(self, token):
        if self.fail_save:
            raise CredentialStoreError()
        self.token = token
        self.saved.append(token)

    def delete(self):
        self.delete_calls += 1
        if self.fail_delete:
            raise CredentialStoreError()
        self.token = None


class DesktopSessionTests(unittest.TestCase):
    def manager(self, store, *responses):
        client = TravelerClient(
            "https://shop.example.test",
            transport=ScriptedTransport(*responses),
        )
        return DesktopSessionManager(
            "https://shop.example.test", credential_store=store, client=client
        )

    def test_valid_remembered_session_is_validated_before_sign_in(self):
        store = FakeCredentialStore(TOKEN)
        manager = self.manager(store, FakeResponse(200, {"account": account(planner=True)}))

        identity = manager.restore_remembered_session()

        self.assertEqual(identity.username, "Operator")
        self.assertTrue(manager.signed_in)
        self.assertTrue(manager.is_job_planner)
        call = manager.client.transport.calls[0]
        self.assertTrue(call[1].endswith("/api/v1/auth/me"))
        self.assertEqual(call[2]["headers"]["Authorization"], f"Bearer {TOKEN}")

    def test_rejected_or_malformed_remembered_session_is_removed(self):
        cases = (
            FakeResponse(401, {"error": {"code": "invalid_session"}}),
            FakeResponse(200, {"account": {"username": "malformed"}}),
        )
        for response in cases:
            with self.subTest(status=response.status_code):
                store = FakeCredentialStore(TOKEN)
                manager = self.manager(store, response)
                self.assertIsNone(manager.restore_remembered_session())
                self.assertFalse(manager.signed_in)
                self.assertIsNone(store.token)
                self.assertEqual(store.delete_calls, 1)
                self.assertNotIn(TOKEN, manager.last_notice)

    def test_unreachable_server_does_not_treat_remembered_token_as_signed_in(self):
        store = FakeCredentialStore(TOKEN)
        manager = self.manager(
            store, requests.exceptions.ConnectionError(f"secret={TOKEN}")
        )

        self.assertIsNone(manager.restore_remembered_session())
        self.assertFalse(manager.signed_in)
        self.assertEqual(store.token, TOKEN)
        self.assertNotIn(TOKEN, manager.last_notice)

    def test_login_remembers_only_token_and_never_retains_pin(self):
        store = FakeCredentialStore()
        login = FakeResponse(
            200,
            {
                "session": {
                    "token": TOKEN,
                    "token_type": "Bearer",
                    "expires_at": "2026-08-01T00:00:00+00:00",
                },
                "account": account(planner=True),
            },
        )
        manager = self.manager(store, login)

        identity = manager.login("Operator", PIN, remember=True)

        self.assertEqual(identity.username, "Operator")
        self.assertEqual(store.saved, [TOKEN])
        self.assertNotIn(PIN, repr(manager))
        self.assertNotIn(TOKEN, repr(manager))
        self.assertFalse(
            any(PIN in repr(value) for value in manager.__dict__.values())
        )

    def test_missing_or_failed_credential_backend_allows_memory_only_session(self):
        login = FakeResponse(
            200,
            {
                "session": {
                    "token": TOKEN,
                    "token_type": "Bearer",
                    "expires_at": "2026-08-01T00:00:00+00:00",
                },
                "account": account(),
            },
        )
        client = TravelerClient(
            "https://shop.example.test", transport=ScriptedTransport(login)
        )
        with mock.patch.object(
            desktop_session,
            "KeyringCredentialStore",
            side_effect=CredentialBackendUnavailable(),
        ):
            manager = DesktopSessionManager(
                "https://shop.example.test", client=client
            )
        self.assertFalse(manager.can_remember)
        self.assertIn("cannot be remembered", manager.last_notice)
        manager.login("Operator", PIN, remember=True)
        self.assertTrue(manager.signed_in)
        self.assertIn("cannot be remembered", manager.last_notice)

        failed_store = FakeCredentialStore(fail_save=True)
        second = self.manager(failed_store, login)
        second.login("Operator", PIN, remember=True)
        self.assertTrue(second.signed_in)
        self.assertNotIn(TOKEN, second.last_notice)

    def test_sign_out_and_switch_clear_local_secret_even_when_server_is_unreachable(self):
        for method in ("sign_out", "switch_employee"):
            with self.subTest(method=method):
                store = FakeCredentialStore(TOKEN)
                manager = self.manager(
                    store,
                    FakeResponse(200, {"account": account()}),
                    requests.exceptions.ConnectionError(f"do not reveal {TOKEN}"),
                )
                manager.restore_remembered_session()
                result = getattr(manager, method)()
                self.assertFalse(result.server_invalidation_confirmed)
                self.assertTrue(result.credential_removal_confirmed)
                self.assertFalse(manager.signed_in)
                self.assertIsNone(store.token)
                self.assertNotIn(TOKEN, manager.last_notice)

    def test_capability_denial_refreshes_identity_without_retrying_command(self):
        identity = EmployeeIdentity(
            username="Operator",
            display_name=None,
            approved=True,
            enabled=True,
            capabilities=("job_planner",),
        )
        store = FakeCredentialStore(TOKEN)
        transport = ScriptedTransport(
            FakeResponse(403, {"error": {"code": "capability_required"}}),
            FakeResponse(200, {"account": account(planner=False)}),
        )
        client = TravelerClient(
            "https://shop.example.test", TOKEN, transport=transport
        )
        client.set_bearer_session(TOKEN, identity)
        manager = DesktopSessionManager(
            "https://shop.example.test", credential_store=store, client=client
        )
        manager.employee = identity

        with self.assertRaises(ClientAuthorizationError):
            client.resize_plan(
                "SAFE-JOB",
                operation_count=2,
                document_revision=1,
                read_version="sha256:" + "a" * 64,
            )

        self.assertFalse(manager.is_job_planner)
        self.assertEqual(len(transport.calls), 2)


class KeyringAdapterTests(unittest.TestCase):
    def test_keyring_errors_are_generic_and_never_include_secret_text(self):
        class FailingBackend:
            priority = 1

            def get_password(self, _service, _account):
                raise RuntimeError(f"backend leaked {TOKEN}")

        store = KeyringCredentialStore(backend=FailingBackend())
        with self.assertRaises(CredentialStoreError) as raised:
            store.load()
        self.assertEqual(str(raised.exception), CredentialStoreError.public_message)
        self.assertNotIn(TOKEN, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
