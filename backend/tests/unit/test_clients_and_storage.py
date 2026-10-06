"""White-box tests of the outbound clients and storage: the Mistral client, its pacing and error classification,
the n8n client, local document storage and CV text extraction."""

import io
import time
from typing import Any

import httpx
import pytest
from docx import Document
from langchain_core.messages import AIMessage

from app.ai import llm
from app.ai.llm import LangChainStructuredLLM, RequestPacer, build_chat_model, classify_provider_error
from app.ai.schemas import CandidateAnalysisWire
from app.core.config import Settings
from app.core.errors import UpstreamRejectedError, UpstreamUnavailableError
from app.services.cv_extraction import UnsupportedDocumentError, detect_type, extract
from app.services.n8n import N8nClient
from app.services.storage import InvalidStorageKeyError, LocalStorage, new_cv_key, offer_document_key
from tests.conftest import VALID_ANALYSIS


# ---- Mistral client ----------------------------------------------------------------------------------------
def mistral_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"app_env": "test", "llm_provider": "mistral", "mistral_api_key": "test-key-not-real"}
    values.update(overrides)
    return Settings(**values)


def test_chat_model_is_mistral_with_the_configured_model_and_no_client_retries() -> None:
    chat = build_chat_model(mistral_settings(llm_model="ministral-14b-latest", llm_temperature=0.0))
    assert type(chat).__name__ == "ChatMistralAI"
    assert chat.model == "ministral-14b-latest" and chat.temperature == 0.0 and chat.max_retries == 1


def test_chat_model_refuses_other_providers_and_a_missing_key() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        build_chat_model(mistral_settings(llm_provider="none"))
    with pytest.raises(ValueError, match="MISTRAL_API_KEY"):
        build_chat_model(mistral_settings(mistral_api_key=None))


async def test_pacer_spaces_requests() -> None:
    pacer = RequestPacer(rate=20)  # one request per 50 ms
    started = time.monotonic()
    for _ in range(4):
        await pacer.wait()
    assert time.monotonic() - started >= 0.14  # 3 gaps of 50 ms (the first request goes at once)


def test_pacers_are_shared_per_rate() -> None:
    assert llm.pacer_for(3.0) is llm.pacer_for(3.0) and llm.pacer_for(3.0) is not llm.pacer_for(4.0)


class _Status(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (httpx.ConnectTimeout("slow"), UpstreamUnavailableError),
        (TimeoutError(), UpstreamUnavailableError),
        (_Status(429), UpstreamUnavailableError),  # free-tier rate limit: retry with backoff
        (_Status(503), UpstreamUnavailableError),
        (_Status(401), UpstreamRejectedError),  # wrong key: retrying cannot help
        (_Status(400), UpstreamRejectedError),
        (RuntimeError("unknown"), UpstreamUnavailableError),
    ],
)
def test_provider_errors_are_classified_for_the_orchestrator(exc: BaseException, kind: type) -> None:
    assert isinstance(classify_provider_error(exc), kind)


class _Runnable:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[Any] = []

    async def ainvoke(self, messages: Any, config: dict[str, Any]) -> Any:
        self.calls.append((messages, config))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def structured_llm(result: Any) -> tuple[LangChainStructuredLLM, _Runnable]:
    client = LangChainStructuredLLM(
        mistral_settings(llm_requests_per_second=50), CandidateAnalysisWire, run_name="candidate_analysis"
    )
    runnable = _Runnable(result)
    client._runnable = runnable  # the LangChain chain is replaced; everything around it is the real code
    return client, runnable


async def test_structured_answer_is_returned_with_the_stop_reason() -> None:
    parsed = CandidateAnalysisWire(**VALID_ANALYSIS)
    raw = AIMessage(content="", response_metadata={"finish_reason": "stop"})
    client, runnable = structured_llm({"raw": raw, "parsed": parsed, "parsing_error": None})
    outcome = await client.generate("system", "user", session_id="COR-1")
    assert outcome.parsed == parsed.model_dump() and outcome.stop_reason == "stop" and outcome.parsing_error is None
    messages, config = runnable.calls[0]
    assert [m.content for m in messages] == ["system", "user"] and config["run_name"] == "candidate_analysis"


async def test_unparseable_answer_is_reported_not_raised() -> None:
    raw = AIMessage(content="not json", response_metadata={"finish_reason": "stop"})
    client, _ = structured_llm({"raw": raw, "parsed": None, "parsing_error": ValueError("bad json")})
    outcome = await client.generate("s", "u", session_id=None)
    assert outcome.parsed is None and outcome.parsing_error == "bad json"


async def test_transport_failures_become_retryable_errors() -> None:
    client, _ = structured_llm(_Status(429))
    with pytest.raises(UpstreamUnavailableError):
        await client.generate("s", "u", session_id=None)
    client, _ = structured_llm(_Status(401))
    with pytest.raises(UpstreamRejectedError):
        await client.generate("s", "u", session_id=None)


# ---- n8n client ----------------------------------------------------------------------------------------------
def n8n_client(handler: Any, key: str | None = "k" * 30) -> N8nClient:
    settings = Settings(app_env="test", n8n_base_url="http://n8n:5678", n8n_api_key=key)
    return N8nClient(settings, transport=httpx.MockTransport(handler))


async def test_kick_and_notify_send_the_service_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    client = n8n_client(handler)
    assert await client.kick("feedback submitted") is True
    assert await client.notify({"template_key": "staff.login_link", "recipient": "a@b.example"}) is True
    assert [r.url.path for r in seen] == ["/webhook/ops/kick", "/webhook/ops/notify"]
    assert all(r.headers["X-API-Key"] == "k" * 30 for r in seen)
    await client.close()


async def test_kick_failures_never_break_the_caller() -> None:
    client = n8n_client(lambda request: httpx.Response(500))
    assert await client.kick("x") is False and await client.notify({"template_key": "t"}) is False
    unconfigured = n8n_client(lambda request: httpx.Response(200), key="")
    assert unconfigured.enabled is False or await unconfigured.kick("x") is True


async def test_replay_maps_n8n_answers_to_api_errors() -> None:
    ok = n8n_client(lambda request: httpx.Response(200, json={"status": "RESOLVED"}))
    assert (await ok.replay("e1", "s1"))["status"] == "RESOLVED"
    rejected = n8n_client(lambda request: httpx.Response(409, json={"code": "ALREADY_RESOLVED", "detail": "done"}))
    with pytest.raises(UpstreamRejectedError) as info:
        await rejected.replay("e1", "s1")
    assert info.value.code == "ALREADY_RESOLVED" and info.value.status_code == 409
    down = n8n_client(lambda request: httpx.Response(502))
    with pytest.raises(UpstreamUnavailableError):
        await down.replay("e1", "s1")


# ---- storage -------------------------------------------------------------------------------------------------
def test_local_storage_round_trip_and_key_validation(tmp_path: Any) -> None:
    store = LocalStorage(str(tmp_path))
    key = new_cv_key("pdf")
    store.save(key, b"%PDF-1.4")
    store.save_text(key, "Python developer")  # extracted text sits next to the file
    assert store.exists(key) and store.read(key) == b"%PDF-1.4" and store.read_text(key) == "Python developer"
    assert store.read("cv/2026/01/" + "0" * 32 + ".pdf") is None
    assert offer_document_key("OFR-2026-0001", 2, 2026).endswith("OFR-2026-0001-r2.pdf")
    for bad in ("../etc/passwd", "/abs/path.pdf", "cv/../../x.pdf"):
        with pytest.raises(InvalidStorageKeyError):
            store.save(bad, b"x")


# ---- CV extraction -----------------------------------------------------------------------------------------------
def docx_bytes(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_word_cv_text_is_extracted() -> None:
    data = docx_bytes("Backend developer: Python, FastAPI, PostgreSQL")
    assert detect_type(data) == "docx"
    extracted = extract(data)
    assert "FastAPI" in extracted.text


@pytest.mark.parametrize("data", [b"MZ\x90\x00 executable", b"plain text cv", b"%PDF-1.4 broken"])
def test_unsupported_or_broken_files_are_refused(data: bytes) -> None:
    with pytest.raises(UnsupportedDocumentError):
        extract(data)
