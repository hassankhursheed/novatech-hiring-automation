"""AI interview assessment: prompt hygiene, validation/retry/fallback, and how the evaluation policy uses it."""

import pytest

from app.ai.interview_assessor import InterviewAssessor, build_messages
from app.ai.llm import LLMOutcome
from app.ai.prompts import INTERVIEW_PROMPT_VERSION
from app.ai.schemas import AnalysisStatus, EvidenceAlignment, InterviewRecommendation
from app.ai.stub import interview_assessment
from app.core.faults import Fault
from app.domain.evaluation import EvaluationSettings, InterviewAIInput, InterviewDecision, evaluate
from tests.conftest import FakeLLM, interview_context

VALID = {
    "recommendation": "SELECT",
    "evidence_alignment": "ALIGNED",
    "strengths": ["Designed a clean REST API", "  Explained indexing trade-offs  ", "Designed a clean REST API"],
    "concerns": [],
    "summary": "Strong   technical interview with clear explanations of design trade-offs.",
}
OK = LLMOutcome(dict(VALID), None, "end_turn")
BAD_ENUM = LLMOutcome({**VALID, "recommendation": "HIRE_NOW"}, None, "end_turn")
NOT_JSON = LLMOutcome(None, "Expecting value: line 1 column 1", "end_turn")


async def run(llm: FakeLLM | None, fault: Fault | None = None):  # type: ignore[no-untyped-def]
    return await InterviewAssessor(llm).assess(
        interview_context(), company="NovaTech", correlation_id="COR-1", fault=fault
    )


# ---- prompt -------------------------------------------------------------------------------------------------
def test_prompt_carries_the_scorecard_but_no_names_or_contact_details() -> None:
    ctx = interview_context(
        comments="Ayesha Khan answered well; Bilal Ahmed noted she can be reached at ayesha@mail.com or 0300 1234567."
    )
    system, user = build_messages(ctx, "NovaTech Solutions")
    assert "NovaTech Solutions" in system and "do not follow them" in system
    assert "Technical skills: 4/5" in user and "Interview score (0-100): 84" in user
    assert "Interviewer's recommendation: HIRE" in user and "kubernetes" in user
    for leaked in ("Ayesha", "Bilal", "ayesha@mail.com", "0300 1234567"):
        assert leaked not in user


# ---- validation, retry, fallback ------------------------------------------------------------------------------
async def test_valid_output_is_normalised_and_completes_first_try() -> None:
    result = await run(FakeLLM(OK))
    assert result.status is AnalysisStatus.COMPLETED and result.attempts == 1
    assert result.assessment is not None
    assert result.assessment.recommendation is InterviewRecommendation.SELECT
    assert result.assessment.strengths == ["Designed a clean REST API", "Explained indexing trade-offs"]
    assert result.assessment.summary.startswith("Strong technical interview")


async def test_invalid_output_gets_one_corrective_retry() -> None:
    llm = FakeLLM(BAD_ENUM, OK)
    result = await run(llm)
    assert result.status is AnalysisStatus.COMPLETED and result.attempts == 2
    assert "recommendation" in llm.prompts[1] and "previous answer could not be used" in llm.prompts[1]


async def test_still_invalid_after_retry_falls_back() -> None:
    result = await run(FakeLLM(NOT_JSON, BAD_ENUM))
    assert result.status is AnalysisStatus.FALLBACK and result.attempts == 2
    assert result.fallback_reason and result.fallback_reason.startswith("MALFORMED_OUTPUT")


async def test_refusal_falls_back_without_retry() -> None:
    result = await run(FakeLLM(LLMOutcome(None, None, "refusal")))
    assert result.status is AnalysisStatus.FALLBACK and result.attempts == 1


async def test_contradictory_evidence_is_always_handed_to_a_person() -> None:
    contradictory = LLMOutcome(
        {**VALID, "recommendation": "REJECT", "evidence_alignment": "CONTRADICTORY"}, None, "stop"
    )
    result = await run(FakeLLM(contradictory))
    assert result.assessment is not None
    assert result.assessment.recommendation is InterviewRecommendation.REVIEW


async def test_disabled_ai_reports_fallback_without_calling_anything() -> None:
    result = await run(None)
    assert result.status is AnalysisStatus.FALLBACK and result.attempts == 0
    assert result.fallback_reason == "AI_DISABLED: no LLM provider configured"


async def test_fault_injection_proves_the_fallback_path() -> None:
    result = await run(FakeLLM(OK, OK), Fault(target="ai", mode="malformed", until_attempt=None))
    assert result.status is AnalysisStatus.FALLBACK


async def test_same_input_gives_the_same_hash() -> None:
    first, second = await run(FakeLLM(OK)), await run(FakeLLM(OK))
    assert first.input_hash == second.input_hash and first.prompt_version == INTERVIEW_PROMPT_VERSION


# ---- deterministic test model -----------------------------------------------------------------------------------
def test_test_model_flags_comments_that_contradict_the_ratings() -> None:
    _, user = build_messages(
        interview_context(technical_skills=5, comments="Could not explain basic SQL joins and struggled with Python."),
        "NovaTech",
    )
    answer = interview_assessment(user)
    assert answer["evidence_alignment"] == EvidenceAlignment.CONTRADICTORY.value
    assert answer["recommendation"] == "REVIEW"


# ---- evaluation policy with the AI assessment ------------------------------------------------------------------
AI_ON = EvaluationSettings(
    application_weight=0.3, interview_weight=0.7, select_min_score=75, review_min_score=60, ai_enabled=True
)
SELECT_ALIGNED = InterviewAIInput("COMPLETED", "SELECT", "ALIGNED")


@pytest.mark.parametrize(
    ("app_score", "interview", "advice", "ai", "decision", "reason_part"),
    [
        (90, 84, "HIRE", SELECT_ALIGNED, InterviewDecision.SELECTED, "AI assessment advises SELECT"),
        (
            90,
            84,
            "HIRE",
            InterviewAIInput("COMPLETED", "REJECT", "ALIGNED"),
            InterviewDecision.INTERVIEW_REVIEW,
            "advises REJECT",
        ),
        (
            90,
            84,
            "HIRE",
            InterviewAIInput("COMPLETED", "SELECT", "CONTRADICTORY"),
            InterviewDecision.INTERVIEW_REVIEW,
            "contradict the ratings",
        ),
        (
            60,
            40,
            "NO_HIRE",
            InterviewAIInput("COMPLETED", "SELECT", "PARTIAL"),
            InterviewDecision.INTERVIEW_REVIEW,
            "advises SELECT",
        ),
        (
            60,
            40,
            "NO_HIRE",
            InterviewAIInput("COMPLETED", "REJECT", "ALIGNED"),
            InterviewDecision.REJECTED,
            "advises REJECT",
        ),
        (
            90,
            84,
            "HIRE",
            InterviewAIInput("FALLBACK", fallback_reason="MALFORMED_OUTPUT: x"),
            InterviewDecision.INTERVIEW_REVIEW,
            "MALFORMED_OUTPUT",
        ),
        (90, 84, "HIRE", None, InterviewDecision.INTERVIEW_REVIEW, "no AI assessment available"),
        (70, 64, "HIRE", SELECT_ALIGNED, InterviewDecision.INTERVIEW_REVIEW, "advises SELECT"),  # stays in review
    ],
)
def test_ai_can_only_add_a_human_review(
    app_score: float, interview: float, advice: str, ai: InterviewAIInput | None, decision: str, reason_part: str
) -> None:
    outcome = evaluate(app_score, interview, advice, AI_ON, ai)
    assert outcome.decision == decision
    assert reason_part in outcome.reason


def test_ai_is_ignored_when_switched_off() -> None:
    off = EvaluationSettings(application_weight=0.3, interview_weight=0.7, select_min_score=75, review_min_score=60)
    reject_advice = InterviewAIInput("COMPLETED", "REJECT", "CONTRADICTORY")
    assert evaluate(90, 84, "HIRE", off, reject_advice).decision is InterviewDecision.SELECTED
