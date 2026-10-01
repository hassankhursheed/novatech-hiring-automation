import pytest

from app.ai.analyzer import CandidateAnalyzer, build_messages
from app.ai.llm import LLMOutcome, classify_provider_error
from app.ai.redaction import redact
from app.ai.schemas import AnalysisStatus
from app.core.errors import UpstreamRejectedError, UpstreamUnavailableError
from app.core.faults import Fault
from tests.conftest import VALID_ANALYSIS, FakeLLM, candidate_context

OK = LLMOutcome(parsed=dict(VALID_ANALYSIS), parsing_error=None, stop_reason="end_turn")
OUT_OF_RANGE = LLMOutcome(
    parsed={**VALID_ANALYSIS, "technical_strength": 42}, parsing_error=None, stop_reason="end_turn"
)
NOT_JSON = LLMOutcome(parsed=None, parsing_error="Expecting value: line 1 column 1", stop_reason="end_turn")


async def run(llm: FakeLLM | None, fault: Fault | None = None):  # type: ignore[no-untyped-def]
    return await CandidateAnalyzer(llm).analyze(
        candidate_context(), company="NovaTech", correlation_id="COR-1", fault=fault
    )


async def test_valid_output_completes_first_try() -> None:
    result = await run(FakeLLM(OK))
    assert result.status is AnalysisStatus.COMPLETED
    assert result.attempts == 1
    assert result.analysis and result.analysis.missing_skills == ["docker"]


async def test_one_corrective_retry_then_success() -> None:
    llm = FakeLLM(OUT_OF_RANGE, OK)
    result = await run(llm)
    assert result.status is AnalysisStatus.COMPLETED and result.attempts == 2
    assert "technical_strength" in llm.prompts[1]  # the retry tells the model what was wrong


async def test_malformed_twice_falls_back_without_infinite_retry() -> None:
    llm = FakeLLM(NOT_JSON, OUT_OF_RANGE, OK)
    result = await run(llm)
    assert result.status is AnalysisStatus.FALLBACK
    assert result.attempts == 2
    assert result.fallback_reason and result.fallback_reason.startswith("MALFORMED_OUTPUT")
    assert len(llm.outcomes) == 1  # third outcome never requested


async def test_refusal_falls_back_immediately() -> None:
    result = await run(FakeLLM(LLMOutcome(parsed=None, parsing_error=None, stop_reason="refusal")))
    assert result.status is AnalysisStatus.FALLBACK and result.attempts == 1
    assert result.fallback_reason and result.fallback_reason.startswith("MODEL_REFUSAL")


async def test_injected_malformed_fault_exercises_fallback() -> None:
    result = await run(FakeLLM(OK, OK), fault=Fault(target="ai", mode="malformed", until_attempt=None))
    assert result.status is AnalysisStatus.FALLBACK


async def test_no_provider_configured_is_a_fallback() -> None:
    result = await run(None)
    assert result.status is AnalysisStatus.FALLBACK and result.fallback_reason.startswith("AI_DISABLED")


async def test_transport_errors_propagate_for_orchestrator_retry() -> None:
    with pytest.raises(UpstreamUnavailableError):
        await run(FakeLLM(UpstreamUnavailableError("down")))


def test_input_hash_changes_with_input() -> None:
    import asyncio

    a = asyncio.run(CandidateAnalyzer(None).analyze(candidate_context(), company="N", correlation_id=None))
    b = asyncio.run(
        CandidateAnalyzer(None).analyze(candidate_context(cv_text="different"), company="N", correlation_id=None)
    )
    assert a.input_hash != b.input_hash


class _StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class APITimeoutError(Exception):
    pass


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_StatusError(429), UpstreamUnavailableError),
        (_StatusError(529), UpstreamUnavailableError),
        (_StatusError(500), UpstreamUnavailableError),
        (_StatusError(401), UpstreamRejectedError),
        (_StatusError(400), UpstreamRejectedError),
        (APITimeoutError(), UpstreamUnavailableError),
    ],
)
def test_provider_error_classification(exc: Exception, expected: type) -> None:
    assert isinstance(classify_provider_error(exc), expected)


def test_prompt_is_pii_minimised_and_fenced() -> None:
    system, user = build_messages(candidate_context(), "NovaTech")
    assert "Ali" not in user and "ali@example.com" not in user and "1234567" not in user
    assert "2019 - 2023" in user  # employment dates are kept
    assert "<application>" in user and "untrusted" in system


def test_redaction_patterns() -> None:
    text = (
        "Sara Khan | sara.k@mail.com | 0300-1234567 | CNIC 35202-1234567-1 | https://linkedin.com/in/sara | Gender: F"
    )
    out = redact(text, extra_terms=["Sara Khan"])
    for leaked in ("Sara", "sara.k@mail.com", "0300-1234567", "35202-1234567-1", "linkedin", "Gender: F"):
        assert leaked not in out
