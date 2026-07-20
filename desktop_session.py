"""Desktop employee sessions backed only by an injected secure credential store."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Protocol

from traveler_client import (
    ClientAuthenticationError,
    ClientAuthorizationError,
    EmployeeIdentity,
    TravelerClient,
    TravelerClientError,
)


CREDENTIAL_SERVICE = "Red Ribbon ShopOS Desktop"
CREDENTIAL_ACCOUNT = "remembered-employee-session-v1"
SERVICE_URL_ENV = "SHOPOS_JOB_TRAVELER_SERVICE_URL"


class CredentialStoreError(RuntimeError):
    public_message = "Secure operating-system credential storage is unavailable."

    def __init__(self) -> None:
        super().__init__(self.public_message)


class CredentialBackendUnavailable(CredentialStoreError):
    pass


class SessionConfigurationError(ValueError):
    pass


class CredentialStore(Protocol):
    def load(self) -> str | None: ...

    def save(self, token: str) -> None: ...

    def delete(self) -> None: ...


class KeyringCredentialStore:
    """Cross-platform keyring adapter using one fixed per-profile secret slot."""

    def __init__(self, backend=None) -> None:
        if backend is None:
            try:
                import keyring
                from keyring.errors import KeyringError, NoKeyringError
            except (ImportError, OSError):
                raise CredentialBackendUnavailable() from None
            self._keyring = keyring
            self._errors = (KeyringError, NoKeyringError, OSError)
            backend = keyring.get_keyring()
        else:
            self._keyring = None
            self._errors = (OSError,)
        self._backend = backend
        try:
            priority = float(self._backend.priority)
        except (AttributeError, TypeError, ValueError, OSError):
            raise CredentialBackendUnavailable() from None
        if priority <= 0:
            raise CredentialBackendUnavailable()

    def load(self) -> str | None:
        try:
            return self._backend.get_password(CREDENTIAL_SERVICE, CREDENTIAL_ACCOUNT)
        except self._errors:
            raise CredentialStoreError() from None
        except Exception:
            raise CredentialStoreError() from None

    def save(self, token: str) -> None:
        if not isinstance(token, str) or not token:
            raise CredentialStoreError()
        try:
            self._backend.set_password(CREDENTIAL_SERVICE, CREDENTIAL_ACCOUNT, token)
        except self._errors:
            raise CredentialStoreError() from None
        except Exception:
            raise CredentialStoreError() from None

    def delete(self) -> None:
        try:
            existing = self._backend.get_password(
                CREDENTIAL_SERVICE, CREDENTIAL_ACCOUNT
            )
            if existing is not None:
                self._backend.delete_password(
                    CREDENTIAL_SERVICE, CREDENTIAL_ACCOUNT
                )
        except self._errors:
            raise CredentialStoreError() from None
        except Exception:
            raise CredentialStoreError() from None


@dataclass(frozen=True)
class LogoutResult:
    server_invalidation_confirmed: bool
    credential_removal_confirmed: bool


class DesktopSessionManager:
    """Own one employee token in memory and optionally one OS-keyring entry."""

    def __init__(
        self,
        base_url: str,
        *,
        credential_store: CredentialStore | None = None,
        client: TravelerClient | None = None,
    ) -> None:
        self.last_notice = ""
        if credential_store is None:
            try:
                credential_store = KeyringCredentialStore()
            except CredentialBackendUnavailable:
                credential_store = None
                self.last_notice = (
                    "Secure credential storage is unavailable; sign-in can be "
                    "used for this process but cannot be remembered."
                )
        self.credential_store = credential_store
        self.client = client or TravelerClient(base_url)
        self.client.authorization_denied_callback = self._refresh_after_denial
        self.employee: EmployeeIdentity | None = None

    def __repr__(self) -> str:
        return (
            "DesktopSessionManager(employee="
            f"{self.employee.username!r}, token=<redacted>)"
            if self.employee is not None
            else "DesktopSessionManager(employee=None, token=<redacted>)"
        )

    @property
    def can_remember(self) -> bool:
        return self.credential_store is not None

    @property
    def signed_in(self) -> bool:
        return self.employee is not None

    @property
    def is_job_planner(self) -> bool:
        return self.employee is not None and self.employee.is_job_planner

    def _delete_remembered(self) -> bool:
        if self.credential_store is None:
            return True
        try:
            self.credential_store.delete()
            return True
        except CredentialStoreError:
            self.last_notice = (
                "The local session was cleared, but secure credential removal "
                "could not be confirmed."
            )
            return False
        except Exception:
            self.last_notice = (
                "The local session was cleared, but secure credential removal "
                "could not be confirmed."
            )
            return False

    def _clear_local(self, *, remove_remembered: bool) -> bool:
        self.employee = None
        self.client.clear_bearer_session()
        return self._delete_remembered() if remove_remembered else True

    def restore_remembered_session(self) -> EmployeeIdentity | None:
        self.employee = None
        self.client.clear_bearer_session()
        if self.credential_store is None:
            return None
        try:
            token = self.credential_store.load()
        except CredentialStoreError:
            self.last_notice = (
                "Secure credential storage could not be read; sign-in cannot be "
                "remembered for this attempt."
            )
            return None
        except Exception:
            self.last_notice = (
                "Secure credential storage could not be read; sign-in cannot be "
                "remembered for this attempt."
            )
            return None
        if token is None:
            return None
        try:
            self.client.set_bearer_session(token)
            identity = self.client.validate_session()
        except (ClientAuthenticationError, ClientAuthorizationError):
            self._clear_local(remove_remembered=True)
            self.last_notice = "The remembered session is no longer valid. Sign in again."
            return None
        except ValueError:
            self._clear_local(remove_remembered=True)
            self.last_notice = "The remembered session is no longer valid. Sign in again."
            return None
        except TravelerClientError:
            self.client.clear_bearer_session()
            self.last_notice = (
                "The remembered session could not be validated because ShopOS is "
                "unavailable. Sign in when the service is reachable."
            )
            return None
        finally:
            token = None
        self.employee = identity
        return identity

    def login(
        self, username: str, pin: str, *, remember: bool = False
    ) -> EmployeeIdentity:
        try:
            result = self.client.login(username, pin)
        finally:
            pin = ""
        self.client.set_bearer_session(result.token, result.employee)
        self.employee = result.employee
        if remember and self.credential_store is not None:
            try:
                self.credential_store.save(result.token)
            except CredentialStoreError:
                self.last_notice = (
                    "Signed in for this process, but the session could not be "
                    "remembered securely."
                )
            except Exception:
                self.last_notice = (
                    "Signed in for this process, but the session could not be "
                    "remembered securely."
                )
        elif remember:
            self.last_notice = (
                "Signed in for this process. Secure credential storage is unavailable, "
                "so the session cannot be remembered."
            )
        else:
            self._delete_remembered()
        return result.employee

    def validate_current_session(self) -> EmployeeIdentity:
        try:
            identity = self.client.validate_session()
        except (ClientAuthenticationError, ClientAuthorizationError):
            self._clear_local(remove_remembered=True)
            raise
        self.employee = identity
        return identity

    def _refresh_after_denial(self) -> None:
        try:
            self.validate_current_session()
        except TravelerClientError:
            pass

    def sign_out(self) -> LogoutResult:
        server_confirmed = True
        if self.employee is not None:
            try:
                self.client.logout()
            except TravelerClientError:
                server_confirmed = False
            except Exception:
                server_confirmed = False
        credential_confirmed = self._clear_local(remove_remembered=True)
        if not server_confirmed:
            self.last_notice = (
                "Signed out locally. Server-side session invalidation could not be "
                "confirmed."
            )
        return LogoutResult(server_confirmed, credential_confirmed)

    def switch_employee(self) -> LogoutResult:
        return self.sign_out()


def build_session_manager_from_environment(
    *, environ=None, credential_store: CredentialStore | None = None
) -> DesktopSessionManager:
    environment = os.environ if environ is None else environ
    value = environment.get(SERVICE_URL_ENV)
    if not isinstance(value, str) or not value or value != value.strip():
        raise SessionConfigurationError(
            f"Service mode requires {SERVICE_URL_ENV} with an HTTPS ShopOS origin."
        )
    return DesktopSessionManager(value, credential_store=credential_store)


__all__ = [
    "CREDENTIAL_ACCOUNT",
    "CREDENTIAL_SERVICE",
    "CredentialBackendUnavailable",
    "CredentialStore",
    "CredentialStoreError",
    "DesktopSessionManager",
    "KeyringCredentialStore",
    "LogoutResult",
    "SERVICE_URL_ENV",
    "SessionConfigurationError",
    "build_session_manager_from_environment",
]
