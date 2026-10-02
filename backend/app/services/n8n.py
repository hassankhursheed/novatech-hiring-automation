"""Calls from the backend to n8n webhooks.

* kick():   wakes the dispatcher (WF-00) right after a person acts, so the next step starts within seconds
            instead of at the next one-minute tick. Best effort: if n8n is down, the schedule still picks
            the work up, so a failed kick is logged and never surfaces to the person.
* replay(): asks WF-07 to replay one error-queue entry on behalf of a staff member.
* notify(): sends one message through WF-09 / SWF-02 (the only place that knows the mail provider), at most once
            per dedupe key. Used for staff sign-in links.
"""

from typing import Any

import httpx

from app.core.config import Settings
from app.core.context import get_correlation_id
from app.core.errors import UpstreamRejectedError, UpstreamUnavailableError
from app.core.logging import get_logger

log = get_logger(__name__)


class N8nClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base = settings.n8n_base_url.rstrip("/")
        self._key = settings.n8n_webhook_key
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=2.0), transport=transport)

    @property
    def enabled(self) -> bool:
        return bool(self._base and self._key)

    def _headers(self) -> dict[str, str]:
        headers = {"X-API-Key": self._key or ""}
        if cid := get_correlation_id():
            headers["X-Correlation-ID"] = cid
        return headers

    async def close(self) -> None:
        await self._client.aclose()

    async def kick(self, reason: str) -> bool:
        if not self.enabled:
            return False
        try:
            response = await self._client.post(
                f"{self._base}/webhook/ops/kick", json={"reason": reason}, headers=self._headers(), timeout=2.0
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            log.warning("n8n_kick_failed", reason=reason, error=type(exc).__name__)
            return False

    async def notify(self, message: dict[str, Any]) -> bool:
        if not self.enabled:
            log.warning("n8n_notify_skipped", reason="n8n is not configured", template=message.get("template_key"))
            return False
        try:
            response = await self._client.post(
                f"{self._base}/webhook/ops/notify", json=message, headers=self._headers(), timeout=30.0
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            log.error("n8n_notify_failed", template=message.get("template_key"), error=type(exc).__name__)
            return False

    async def replay(self, error_id: str, staff_id: str) -> dict[str, Any]:
        if not self.enabled:
            raise UpstreamUnavailableError("n8n is not configured", code="N8N_NOT_CONFIGURED", retryable=False)
        try:
            response = await self._client.post(
                f"{self._base}/webhook/ops/replay",
                json={"error_id": error_id, "requested_by": staff_id},
                headers=self._headers(),
                timeout=60.0,
            )
        except httpx.HTTPError as exc:
            raise UpstreamUnavailableError("n8n is unreachable", code="N8N_UNAVAILABLE") from exc
        if response.status_code >= 500:
            raise UpstreamUnavailableError(f"n8n returned HTTP {response.status_code}", code="N8N_UNAVAILABLE")
        try:
            body: dict[str, Any] = response.json()
        except ValueError:
            body = {"raw": response.text[:500]}
        if response.status_code >= 400:
            raise UpstreamRejectedError(
                str(body.get("detail") or body.get("message") or f"replay rejected (HTTP {response.status_code})"),
                code=str(body.get("code") or "REPLAY_REJECTED"),
                status_code=409 if response.status_code == 409 else 502,
            )
        return body
