"""HTTP middleware: correlation id propagation, request logging, security headers."""

import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.context import new_request_id, sanitize_correlation_id, set_correlation_id
from app.core.logging import get_logger

log = get_logger("http")

CORRELATION_HEADER = "X-Correlation-ID"


class CorrelationAndLoggingMiddleware(BaseHTTPMiddleware):
    """Binds the caller's X-Correlation-ID (or a generated request id) to every log line and response."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        correlation_id = sanitize_correlation_id(request.headers.get(CORRELATION_HEADER)) or new_request_id()
        set_correlation_id(correlation_id)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 1)

        response.headers[CORRELATION_HEADER] = correlation_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"

        if request.url.path not in ("/health/live", "/health/ready"):
            log.info(
                "request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=duration_ms,
                attempt=request.headers.get("X-Attempt"),
            )
        return response
