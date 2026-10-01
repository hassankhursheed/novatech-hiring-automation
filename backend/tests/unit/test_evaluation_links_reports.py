import io
from datetime import UTC, date, datetime, timedelta

import pytest
from pypdf import PdfReader

from app.ai.llm import LLMOutcome
from app.ai.report_writer import ReportWriter
from app.core.errors import AppError, BadRequestError, UnauthorizedError
from app.core.links import LinkPurpose, LinkSigner
from app.domain.evaluation import EvaluationConfigError, EvaluationSettings, InterviewDecision, evaluate
from app.domain.report_summary import template_summary, unsupported_numbers
from app.services.offer_document import render_offer_letter
from tests.conftest import CANDIDATE_ID, INTERVIEW_ID, FakeLLM, offer_snapshot

SETTINGS = EvaluationSettings(application_weight=0.3, interview_weight=0.7, select_min_score=75, review_min_score=60)


# ---- interview evaluation ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("app_score", "interview", "recommendation", "decision", "final"),
    [
        (90, 84, "HIRE", InterviewDecision.SELECTED, 85.8),  # 27 + 58.8
        (90, 84, "NO_HIRE", InterviewDecision.INTERVIEW_REVIEW, 85.8),  # numbers say yes, interviewer says no
        (70, 64, "HIRE", InterviewDecision.INTERVIEW_REVIEW, 65.8),  # borderline
        (60, 40, "NO_HIRE", InterviewDecision.REJECTED, 46.0),
        (60, 40, "STRONG_HIRE", InterviewDecision.INTERVIEW_REVIEW, 46.0),  # numbers say no, interviewer says yes
        (100, 65, "HIRE", InterviewDecision.SELECTED, 75.5),  # 30 + 45.5, just above the threshold
    ],
)
def test_combined_score_and_decision(
    app_score: float, interview: float, recommendation: str, decision: str, final: float
) -> None:
    outcome = evaluate(app_score, interview, recommendation, SETTINGS)
    assert outcome.decision == decision and outcome.final_score == final
    assert str(final).rstrip("0").rstrip(".") in outcome.reason


def test_threshold_is_inclusive_and_weights_are_normalised() -> None:
    exactly = evaluate(75, 75, "HIRE", SETTINGS)
    assert exactly.decision == InterviewDecision.SELECTED
    doubled = EvaluationSettings(application_weight=3, interview_weight=7, select_min_score=75, review_min_score=60)
    assert evaluate(90, 84, "HIRE", doubled).final_score == 85.8


def test_missing_screening_score_uses_interview_only() -> None:
    outcome = evaluate(None, 80, "HIRE", SETTINGS)
    assert outcome.final_score == 80 and outcome.application_weight == 0 and "interview score 80 only" in outcome.reason


def test_invalid_settings_are_rejected() -> None:
    with pytest.raises(EvaluationConfigError):
        evaluate(80, 80, "HIRE", EvaluationSettings(0.3, 0.7, select_min_score=60, review_min_score=75))
    with pytest.raises(EvaluationConfigError):
        evaluate(80, 80, "HIRE", EvaluationSettings(0, 0, 75, 60))


# ---- signed links ---------------------------------------------------------------------------------------
def signer() -> LinkSigner:
    return LinkSigner("unit-test-secret-of-sufficient-length", timedelta(days=30))


def test_link_round_trip_binds_purpose_subject_and_entity() -> None:
    token, exp = signer().issue(LinkPurpose.INTERVIEW_SLOT, CANDIDATE_ID, INTERVIEW_ID)
    claims = signer().verify(token, {LinkPurpose.INTERVIEW_SLOT})
    assert (claims.subject_id, claims.entity_id, claims.purpose) == (
        CANDIDATE_ID,
        INTERVIEW_ID,
        LinkPurpose.INTERVIEW_SLOT,
    )
    assert claims.ctx()["actor_type"] == "CANDIDATE" and claims.ctx()["actor_id"] == CANDIDATE_ID
    assert exp <= datetime.now(UTC) + timedelta(days=30)


def test_link_for_another_purpose_or_secret_is_rejected() -> None:
    token, _ = signer().issue(LinkPurpose.OFFER_RESPONSE, CANDIDATE_ID, INTERVIEW_ID)
    with pytest.raises(UnauthorizedError):
        signer().verify(token, {LinkPurpose.INTERVIEW_SLOT})
    with pytest.raises(UnauthorizedError):
        LinkSigner("a-different-secret-of-sufficient-len", timedelta(days=1)).verify(
            token, {LinkPurpose.OFFER_RESPONSE}
        )
    with pytest.raises(UnauthorizedError):
        signer().verify(token[:-4] + "abcd", {LinkPurpose.OFFER_RESPONSE})


def test_expired_link_is_gone_and_expiry_is_capped() -> None:
    past = datetime.now(UTC) - timedelta(days=2)
    token, _ = signer().issue(
        LinkPurpose.OFFER_RESPONSE, CANDIDATE_ID, INTERVIEW_ID, now=past, expires_at=past + timedelta(hours=1)
    )
    with pytest.raises(AppError) as exc:
        signer().verify(token, {LinkPurpose.OFFER_RESPONSE})
    assert exc.value.status_code == 410 and exc.value.code == "LINK_EXPIRED"

    _, capped = signer().issue(
        LinkPurpose.OFFER_RESPONSE, CANDIDATE_ID, INTERVIEW_ID, datetime.now(UTC) + timedelta(days=365)
    )
    assert capped <= datetime.now(UTC) + timedelta(days=30, seconds=1)
    with pytest.raises(BadRequestError):
        signer().issue(LinkPurpose.OFFER_RESPONSE, CANDIDATE_ID, INTERVIEW_ID, datetime.now(UTC) - timedelta(minutes=1))


def test_staff_links_act_as_staff() -> None:
    token, _ = signer().issue(LinkPurpose.OFFER_APPROVAL, CANDIDATE_ID, INTERVIEW_ID)
    assert signer().verify(token, {LinkPurpose.OFFER_APPROVAL}).ctx()["actor_type"] == "STAFF"


# ---- daily report ---------------------------------------------------------------------------------------
METRICS = {
    "applications_received": 12,
    "shortlisted": 5,
    "rejected": 4,
    "manual_review": 3,
    "offers_sent": 1,
    "workflow_failures": 0,
    "avg_minutes_to_screening_decision": 2.5,
    "manual_intervention_required": 2,
}
DAY = date(2026, 10, 1)


def test_template_summary_uses_only_metric_numbers() -> None:
    text = template_summary(METRICS, DAY)
    assert "12 application(s)" in text and "2 item(s) need manual attention" in text
    assert unsupported_numbers(text, METRICS, DAY) == []


def test_numeric_guardrail_flags_invented_or_computed_numbers() -> None:
    assert unsupported_numbers("12 applications, 5 shortlisted (42%).", METRICS, DAY) == ["42"]
    assert unsupported_numbers("Average 2.5 minutes; 1,200 visits.", METRICS, DAY) == ["1200"]


async def test_report_writer_accepts_grounded_ai_text_and_rejects_invented_numbers() -> None:
    good = LLMOutcome(
        {"summary": "NovaTech received 12 applications; 5 were shortlisted and 4 rejected."}, None, "end_turn"
    )
    bad = LLMOutcome(
        {"summary": "NovaTech received 12 applications, a 20% rise on yesterday's numbers."}, None, "end_turn"
    )
    accepted = await ReportWriter(FakeLLM(good)).write(METRICS, DAY, company="NovaTech", correlation_id=None)
    rejected = await ReportWriter(FakeLLM(bad)).write(METRICS, DAY, company="NovaTech", correlation_id=None)
    assert accepted.source == "AI" and accepted.fallback_reason is None
    assert rejected.source == "TEMPLATE" and "20" in (rejected.fallback_reason or "")
    disabled = await ReportWriter(None).write(METRICS, DAY, company="NovaTech", correlation_id=None)
    assert disabled.source == "TEMPLATE" and disabled.summary == template_summary(METRICS, DAY)


# ---- offer letter ---------------------------------------------------------------------------------------
def test_offer_letter_is_deterministic_and_readable() -> None:
    offer = offer_snapshot("APPROVED")
    first = render_offer_letter(offer, company="NovaTech Solutions", careers_email="careers@x", issued_on=DAY)
    second = render_offer_letter(offer, company="NovaTech Solutions", careers_email="careers@x", issued_on=DAY)
    assert first.content == second.content and first.sha256 == second.sha256
    text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(first.content)).pages)
    assert "Hira Saleem" in text and "PKR 220,000" in text and "OFF-2026-0001" in text
