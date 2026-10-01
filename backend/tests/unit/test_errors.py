from types import SimpleNamespace

import psycopg

from app.core.errors import database_error_to_app_error


class _FakeDbError(psycopg.errors.RaiseException):
    def __init__(self, sqlstate: str, message: str) -> None:
        # Set before super().__init__, which already reads `sqlstate`.
        self._state = sqlstate
        self._message = message
        super().__init__(message)

    @property
    def sqlstate(self) -> str | None:  # type: ignore[override]
        return self._state

    @property
    def diag(self):  # type: ignore[no-untyped-def,override]
        return SimpleNamespace(message_primary=self._message)


def test_business_errors_map_to_http_status_and_code() -> None:
    err = database_error_to_app_error(_FakeDbError("NT409", "INVALID_TRANSITION: SHORTLISTED -> OFFERED"))
    assert (err.status_code, err.code, err.retryable) == (409, "INVALID_TRANSITION", False)
    assert err.detail == "SHORTLISTED -> OFFERED"


def test_transient_database_errors_are_retryable() -> None:
    err = database_error_to_app_error(_FakeDbError("40P01", "deadlock detected"))
    assert err.status_code == 503 and err.retryable is True
