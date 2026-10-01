"""Error model.

Every error response is RFC 9457 problem+json with two extra fields that automation relies on:
  code       stable machine-readable error code (e.g. INVALID_TRANSITION)
  retryable  true only for transient failures (timeouts, 5xx upstream, DB unavailable)
n8n's retry sub-workflow reads `retryable` instead of guessing from the status code.
"""

from typing import Any

import psycopg
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import get_correlation_id
from app.core.logging import get_logger

log = get_logger(__name__)

PROBLEM_JSON = "application/problem+json"

# SQLSTATEs that indicate a transient condition worth retrying.
_RETRYABLE_SQLSTATES = {
    "40001",  # serialization_failure
    "40P01",  # deadlock_detected
    "55P03",  # lock_not_available
    "57014",  # query_canceled (statement_timeout)
    "57P01",  # admin_shutdown
    "23505",  # unique_violation outside our handled paths = concurrent race; a retry resolves it
    "08000",
    "08003",
    "08006",
    "08001",
    "08004",  # connection exceptions
}


class AppError(Exception):
    """Base class for errors with a stable code, HTTP status and retry semantics."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "INTERNAL_ERROR"
    title: str = "Internal error"
    retryable: bool = False

    def __init__(
        self,
        detail: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        retryable: bool | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        if retryable is not None:
            self.retryable = retryable
        self.extra = extra or {}


class BadRequestError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "BAD_REQUEST"
    title = "Bad request"


class UnauthorizedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHORIZED"
    title = "Unauthorized"


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"
    title = "Not found"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"
    title = "Conflict"


class UnprocessableError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "UNPROCESSABLE"
    title = "Business rule violation"


class PayloadTooLargeError(AppError):
    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "PAYLOAD_TOO_LARGE"
    title = "Payload too large"


class UpstreamUnavailableError(AppError):
    """A dependency (LLM provider, database) is temporarily unavailable. Safe to retry."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "UPSTREAM_UNAVAILABLE"
    title = "Upstream temporarily unavailable"
    retryable = True


class UpstreamRejectedError(AppError):
    """A dependency rejected the request permanently (bad credentials, invalid model). Do not retry."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "UPSTREAM_REJECTED"
    title = "Upstream rejected the request"
    retryable = False


def database_error_to_app_error(exc: psycopg.Error) -> AppError:
    """Map PostgreSQL errors to AppErrors. SQLSTATE class NT = business rule raised by api.* functions."""
    sqlstate = getattr(exc, "sqlstate", None) or ""
    message = (exc.diag.message_primary if getattr(exc, "diag", None) else None) or str(exc)

    if sqlstate.startswith("NT") and len(sqlstate) == 5 and sqlstate[2:].isdigit():
        code, _, detail = message.partition(": ")
        http_status = int(sqlstate[2:])
        if http_status not in {400, 403, 404, 409, 410, 422}:
            http_status = 422
        return AppError(detail or message, code=code or "BUSINESS_RULE", status_code=http_status, retryable=False)

    if isinstance(exc, psycopg.OperationalError) or sqlstate in _RETRYABLE_SQLSTATES:
        return UpstreamUnavailableError(
            "database temporarily unavailable", code="DATABASE_UNAVAILABLE", extra={"sqlstate": sqlstate}
        )

    return AppError("unexpected database error", code="DATABASE_ERROR", extra={"sqlstate": sqlstate})


def _problem(
    request: Request,
    *,
    status_code: int,
    code: str,
    title: str,
    detail: str,
    retryable: bool,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://novatech.example/problems/{code.lower().replace('_', '-')}",
        "title": title,
        "status": status_code,
        "detail": detail,
        "code": code,
        "retryable": retryable,
        "correlation_id": get_correlation_id(),
        "instance": request.url.path,
    }
    if extra:
        body.update(extra)
    headers = {"Retry-After": "5"} if retryable else None
    return JSONResponse(body, status_code=status_code, media_type=PROBLEM_JSON, headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        log_method = log.warning if exc.status_code < 500 else log.error
        log_method("request_failed", code=exc.code, status=exc.status_code, retryable=exc.retryable, detail=exc.detail)
        return _problem(
            request,
            status_code=exc.status_code,
            code=exc.code,
            title=exc.title,
            detail=exc.detail,
            retryable=exc.retryable,
            extra=exc.extra or None,
        )

    @app.exception_handler(psycopg.Error)
    async def _db_error(request: Request, exc: psycopg.Error) -> JSONResponse:
        mapped = database_error_to_app_error(exc)
        return await _app_error(request, mapped)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return _problem(
            request,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="REQUEST_VALIDATION_FAILED",
            title="Request validation failed",
            detail="the request body or parameters are invalid",
            retryable=False,
            extra={"errors": errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem(
            request,
            status_code=exc.status_code,
            code=f"HTTP_{exc.status_code}",
            title=str(exc.detail),
            detail=str(exc.detail),
            retryable=exc.status_code >= 500,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_exception")
        return _problem(
            request,
            status_code=500,
            code="INTERNAL_ERROR",
            title="Internal error",
            detail="an unexpected error occurred",
            retryable=True,
        )
