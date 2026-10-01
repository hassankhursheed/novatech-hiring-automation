"""Provider-agnostic structured LLM client built on LangChain.

Swapping providers is configuration (LLM_PROVIDER / LLM_MODEL / <PROVIDER>_API_KEY), not code.
Transport failures are classified so callers can retry correctly:
  timeouts, connection errors, 408/409/429/5xx -> UpstreamUnavailableError (retryable, HTTP 503)
  other 4xx (bad key, unknown model, bad request) -> UpstreamRejectedError (not retryable, HTTP 502)
Content problems (malformed/invalid output, refusals) are NOT exceptions; they come back in LLMOutcome.
"""

import asyncio
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
    """Instantiate the configured provider. Imports are lazy so unused providers need not be installed."""
    common: dict[str, Any] = {"model": settings.llm_model, "timeout": settings.llm_timeout_seconds, "max_retries": 1}
    provider = settings.llm_provider
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        # No temperature: current Claude models reject sampling parameters; thinking runs adaptively.
        return ChatAnthropic(max_tokens=settings.llm_max_tokens, **common)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(**common)
    if provider == "mistral":
        from langchain_mistralai import ChatMistralAI

        return ChatMistralAI(**common)
    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(**common)
    raise ValueError(f"unsupported LLM provider {provider}")


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


class LangChainStructuredLLM:
    def __init__(self, settings: Settings, schema: type[BaseModel], *, run_name: str) -> None:
        self.provider = settings.llm_provider
        self.model = settings.llm_model
        self._settings = settings
        self._run_name = run_name
        chat = build_chat_model(settings)
        self._runnable = chat.with_structured_output(schema, method=settings.llm_structured_method, include_raw=True)

    async def generate(self, system: str, user: str, *, session_id: str | None) -> LLMOutcome:
        handler = _langfuse_handler(self._settings)
        config: dict[str, Any] = {
            "run_name": self._run_name,
            "callbacks": [handler] if handler else [],
            # Correlation id groups every AI call of one business transaction in the tracing UI (no PII).
            "metadata": {"langfuse_session_id": session_id, "langfuse_tags": [self._run_name]},
        }
        try:
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
