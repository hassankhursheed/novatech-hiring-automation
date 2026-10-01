"""Request-scoped context (correlation id) shared by logging, errors and outbound calls."""

import re
import uuid
from contextvars import ContextVar

_CORRELATION_ID: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_VALID_ID = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


def new_request_id() -> str:
    return f"REQ-{uuid.uuid4().hex[:16]}"


def sanitize_correlation_id(value: str | None) -> str | None:
    """Accept caller-provided ids only if they are short and safe to log/echo."""
    if value and _VALID_ID.match(value):
        return value
    return None


def set_correlation_id(value: str) -> None:
    _CORRELATION_ID.set(value)


def get_correlation_id() -> str | None:
    return _CORRELATION_ID.get()
