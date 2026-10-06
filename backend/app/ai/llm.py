"""Structured LLM client for Mistral AI, built on LangChain (langchain-mistralai).

Configuration: LLM_PROVIDER=mistral, LLM_MODEL (default mistral-medium-latest), MISTRAL_API_KEY.
Output is requested as JSON that follows our schema (Mistral's native json_schema response format) and is then
validated again by our own pydantic models before anything is stored.

Calls are paced (LLM_REQUESTS_PER_SECOND, default 1) so a free-tier key is not throttled when many applications
arrive at once. Transport failures are classified so callers can retry correctly:
  timeouts, connection errors, 408/409/429/5xx -> UpstreamUnavailableError (retryable, HTTP 503)
  other 4xx (bad key, unknown model, bad request) -> UpstreamRejectedError (not retryable, HTTP 502)
Content problems (malformed/invalid output, refusals) are NOT exceptions; they come back in LLMOutcome.
"""

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.core.config import Settings
from app.core.errors import UpstreamRejectedError, UpstreamUnavailableError
from app.core.logging import get_logger

log = get_logger(__name__)

_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


@dataclass(frozen=True)
class LLMOutcome:
    parsed: dict[str, Any] | None
    parsing_error: str | None
    stop_reason: str | None
    trace_id: str | None = None


class StructuredLLM(Protocol):
    provider: str
    model: str

    async def generate(self, system: str, user: str, *, session_id: str | None) -> LLMOutcome: ...


def build_chat_model(settings: Settings) -> BaseChatModel:
    """The Mistral chat model. Retries stay with the orchestrator (n8n backoff), so the client tries once."""
    if settings.llm_provider != "mistral":
        raise ValueError(f"unsupported LLM provider {settings.llm_provider}")
    if not settings.llm_api_key:
        raise ValueError("MISTRAL_API_KEY is not set")
    from langchain_mistralai import ChatMistralAI

    return ChatMistralAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=int(settings.llm_timeout_seconds),
        max_retries=1,
    )


class RequestPacer:
    """Keeps at most `rate` requests per second across this process (free-tier keys are rate limited)."""

    def __init__(self, rate: float) -> None:
        self._interval = 1.0 / rate
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def wait(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            delay = self._next_at - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_at = max(loop.time(), self._next_at) + self._interval


_PACERS: dict[float, RequestPacer] = {}


def pacer_for(rate: float) -> RequestPacer:
    return _PACERS.setdefault(rate, RequestPacer(rate))


def classify_provider_error(exc: BaseException) -> Exception:
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    name = type(exc).__name__
    if (
        isinstance(exc, asyncio.TimeoutError | httpx.TimeoutException | httpx.TransportError)
        or "Timeout" in name
        or "Connection" in name
    ):
        return UpstreamUnavailableError(f"AI provider unreachable: {name}", code="AI_PROVIDER_UNAVAILABLE")
    if isinstance(status, int):
        if status in _RETRYABLE_STATUS or status >= 500:
            return UpstreamUnavailableError(
                f"AI provider returned HTTP {status}", code="AI_PROVIDER_UNAVAILABLE", extra={"provider_status": status}
            )
        return UpstreamRejectedError(
            f"AI provider rejected the request (HTTP {status}): {name}",
            code="AI_PROVIDER_REJECTED",
            extra={"provider_status": status},
        )
    return UpstreamUnavailableError(f"AI provider call failed: {name}", code="AI_PROVIDER_UNAVAILABLE")


def _langfuse_handler(settings: Settings) -> Any | None:
    if not settings.langfuse_enabled:
        return None
    try:
        from langfuse.langchain import CallbackHandler  # reads LANGFUSE_* from the environment
    except ImportError:  # pragma: no cover - dependency is declared, guard for slim installs
        log.warning("langfuse_not_installed")
        return None
    return CallbackHandler()


def _trace_attributes(handler: Any | None, session_id: str | None, run_name: str, settings: Settings) -> Any:
    """Langfuse v4: puts the correlation id (session), feature tag and environment on every span of the call."""
    if handler is None:
        return contextlib.nullcontext()
    from langfuse import propagate_attributes

    return propagate_attributes(
        session_id=session_id or None,
        tags=[run_name, settings.llm_model],
        trace_name=run_name,
        environment=settings.app_env,
    )


class LangChainStructuredLLM:
    def __init__(self, settings: Settings, schema: type[BaseModel], *, run_name: str) -> None:
        self.provider = settings.llm_provider
        self.model = settings.llm_model
        self._settings = settings
        self._run_name = run_name
        chat = build_chat_model(settings)
        self._runnable = chat.with_structured_output(schema, method=settings.llm_structured_method, include_raw=True)
        self._pacer = pacer_for(settings.llm_requests_per_second)

    async def generate(self, system: str, user: str, *, session_id: str | None) -> LLMOutcome:
        handler = _langfuse_handler(self._settings)
        config: dict[str, Any] = {
            "run_name": self._run_name,
            "callbacks": [handler] if handler else [],
            # Correlation id groups every AI call of one business transaction in the tracing UI (no PII).
            "metadata": {"langfuse_session_id": session_id, "langfuse_tags": [self._run_name]},
        }
        await self._pacer.wait()
        try:
            with _trace_attributes(handler, session_id, self._run_name, self._settings):
                result = await self._runnable.ainvoke([SystemMessage(system), HumanMessage(user)], config=config)
        except (UpstreamUnavailableError, UpstreamRejectedError):
            raise
        except Exception as exc:
            raise classify_provider_error(exc) from exc

        raw = result.get("raw")
        parsed = result.get("parsed")
        parsing_error = result.get("parsing_error")
        metadata = getattr(raw, "response_metadata", {}) or {}
        stop_reason = metadata.get("stop_reason") or metadata.get("finish_reason")
        return LLMOutcome(
            parsed=parsed.model_dump() if isinstance(parsed, BaseModel) else parsed,
            parsing_error=str(parsing_error) if parsing_error else None,
            stop_reason=str(stop_reason) if stop_reason else None,
            trace_id=getattr(handler, "last_trace_id", None) if handler else None,
        )
