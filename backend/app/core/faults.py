"""Deterministic fault injection for demonstrating failure handling (dev/test only).

A caller sends   X-Fault-Inject: <target>:<mode>[@<n>][, ...]   and   X-Attempt: <attempt number>
  target  validate | score | ai | decide | cv
  mode    http_503 (retryable) | http_400 (non-retryable) | timeout (retryable) | malformed (AI only)
  @n      fail only while attempt <= n  (e.g. ai:http_503@2 fails attempts 1 and 2, then succeeds)

It is stateless (driven by the caller's attempt counter), so it behaves the same across workers and
replays. Disabled unless FAULT_INJECTION_ENABLED=true, and always disabled in production.
"""

import asyncio
from dataclasses import dataclass
from typing import Literal

from fastapi import Request

from app.core.config import get_settings
from app.core.errors import BadRequestError, UpstreamUnavailableError
from app.core.logging import get_logger

log = get_logger(__name__)

FaultMode = Literal["http_503", "http_400", "timeout", "malformed"]
_MODES: set[str] = {"http_503", "http_400", "timeout", "malformed"}


@dataclass(frozen=True)
class Fault:
    target: str
    mode: FaultMode
    until_attempt: int | None  # None = every attempt


def parse_faults(header: str | None) -> list[Fault]:
    faults: list[Fault] = []
    for raw in (header or "").split(","):
        spec = raw.strip().lower()
        if not spec or ":" not in spec:
            continue
        target, _, rest = spec.partition(":")
        mode, _, limit = rest.partition("@")
        if mode not in _MODES:
            continue
        faults.append(Fault(target=target.strip(), mode=mode, until_attempt=int(limit) if limit.isdigit() else None))  # type: ignore[arg-type]
    return faults


def active_fault(request: Request, target: str) -> Fault | None:
    if not get_settings().fault_injection_enabled:
        return None
    attempt_header = request.headers.get("X-Attempt", "1")
    attempt = int(attempt_header) if attempt_header.isdigit() else 1
    for fault in parse_faults(request.headers.get("X-Fault-Inject")):
        if fault.target == target and (fault.until_attempt is None or attempt <= fault.until_attempt):
            log.warning("fault_injected", target=target, mode=fault.mode, attempt=attempt)
            return fault
    return None


async def raise_if_injected(request: Request, target: str) -> Fault | None:
    """Raise transport-level faults; return content-level faults (e.g. 'malformed') for the caller to apply."""
    fault = active_fault(request, target)
    if fault is None:
        return None
    if fault.mode == "http_503":
        raise UpstreamUnavailableError(f"injected fault: {target} temporarily unavailable", code="INJECTED_FAULT")
    if fault.mode == "http_400":
        raise BadRequestError(f"injected fault: {target} rejected the request", code="INJECTED_FAULT")
    if fault.mode == "timeout":
        await asyncio.sleep(2)
        raise UpstreamUnavailableError(f"injected fault: {target} timed out", code="INJECTED_TIMEOUT")
    return fault
