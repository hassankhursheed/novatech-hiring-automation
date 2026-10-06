"""Interview evaluation policy: combined score and decision after an interview.

final score = application_weight x screening score + interview_weight x interview score   (weights from settings)

  final >= select threshold  -> SELECTED
  final >= review threshold  -> INTERVIEW_REVIEW (hiring manager decides)
  otherwise                  -> REJECTED

The numbers decide. The interviewer's recommendation can only send a case to human review when it
contradicts the numbers; it never silently overrides them.

The AI assessment of the scorecard (evaluation.ai_enabled) is advisory in the same way: it can only ADD a review.
  AI enabled but unavailable/malformed       -> INTERVIEW_REVIEW
  comments contradict the ratings            -> INTERVIEW_REVIEW
  SELECTED by the numbers but AI says REJECT -> INTERVIEW_REVIEW
  REJECTED by the numbers but AI says SELECT -> INTERVIEW_REVIEW
  otherwise the decision above stands, with the AI's view in the reason.
"""

from dataclasses import dataclass
from enum import StrEnum

POLICY_VERSION = "interview-evaluation-1.1"

POSITIVE_RECOMMENDATIONS = {"HIRE", "STRONG_HIRE"}
NEGATIVE_RECOMMENDATIONS = {"NO_HIRE", "STRONG_NO_HIRE"}


class InterviewDecision(StrEnum):
    SELECTED = "SELECTED"
    INTERVIEW_REVIEW = "INTERVIEW_REVIEW"
    REJECTED = "REJECTED"


class EvaluationConfigError(ValueError):
    pass


@dataclass(frozen=True)
class EvaluationSettings:
    application_weight: float
    interview_weight: float
    select_min_score: float
    review_min_score: float
    ai_enabled: bool = False

    def validate(self) -> None:
        if self.application_weight < 0 or self.interview_weight < 0:
            raise EvaluationConfigError("evaluation weights must not be negative")
        if self.application_weight + self.interview_weight <= 0:
            raise EvaluationConfigError("at least one evaluation weight must be positive")
        if not 0 <= self.review_min_score <= self.select_min_score <= 100:
            raise EvaluationConfigError("thresholds must satisfy 0 <= review_min_score <= select_min_score <= 100")


@dataclass(frozen=True)
class InterviewAIInput:
    """The stored AI assessment of the scorecard being evaluated."""

    status: str  # COMPLETED | FALLBACK
    recommendation: str | None = None  # SELECT | REVIEW | REJECT
    evidence_alignment: str | None = None  # ALIGNED | PARTIAL | CONTRADICTORY
    fallback_reason: str | None = None


@dataclass(frozen=True)
class EvaluationOutcome:
    decision: InterviewDecision
    final_score: float
    application_weight: float
    interview_weight: float
    reason: str


def evaluate(
    application_score: float | None,
    interview_score: float,
    recommendation: str,
    settings: EvaluationSettings,
    ai: InterviewAIInput | None = None,
) -> EvaluationOutcome:
    settings.validate()
    if not 0 <= interview_score <= 100:
        raise ValueError("interview_score must be between 0 and 100")
    recommendation = recommendation.upper()

    if application_score is None:
        # E.g. a recruiter shortlisted manually after automatic scoring was impossible.
        app_w, int_w = 0.0, 1.0
        basis = f"interview score {interview_score:g} only (no screening score on record)"
    else:
        total = settings.application_weight + settings.interview_weight
        app_w, int_w = settings.application_weight / total, settings.interview_weight / total
        basis = f"{app_w:.0%} x screening {application_score:g} + {int_w:.0%} x interview {interview_score:g}"
    final = round(app_w * (application_score or 0) + int_w * interview_score, 2)
    summary = f"final score {final:g} ({basis}); interviewer recommends {recommendation}"

    if final >= settings.select_min_score:
        if recommendation in NEGATIVE_RECOMMENDATIONS:
            decision = InterviewDecision.INTERVIEW_REVIEW
            reason = f"{summary}: meets the selection threshold but the interviewer advises against hiring"
        else:
            decision = InterviewDecision.SELECTED
            reason = f"{summary}: meets the selection threshold {settings.select_min_score:g}"
    elif final >= settings.review_min_score:
        decision = InterviewDecision.INTERVIEW_REVIEW
        reason = (
            f"{summary}: between the review threshold {settings.review_min_score:g} "
            f"and the selection threshold {settings.select_min_score:g}"
        )
    elif recommendation in POSITIVE_RECOMMENDATIONS:
        decision = InterviewDecision.INTERVIEW_REVIEW
        reason = f"{summary}: below the review threshold but the interviewer recommends hiring"
    else:
        decision = InterviewDecision.REJECTED
        reason = f"{summary}: below the review threshold {settings.review_min_score:g}"

    if settings.ai_enabled:
        decision, reason = _apply_ai(decision, reason, ai)

    return EvaluationOutcome(
        decision=decision, final_score=final, application_weight=app_w, interview_weight=int_w, reason=reason
    )


def _apply_ai(decision: InterviewDecision, reason: str, ai: InterviewAIInput | None) -> tuple[InterviewDecision, str]:
    review = InterviewDecision.INTERVIEW_REVIEW
    if ai is None or ai.status.upper() != "COMPLETED" or not ai.recommendation:
        why = ai.fallback_reason if ai and ai.fallback_reason else "no AI assessment available"
        return review, f"{reason}; AI assessment unavailable ({why}) - human review required"
    advice = ai.recommendation.upper()
    if (ai.evidence_alignment or "").upper() == "CONTRADICTORY" and decision is not review:
        return review, f"{reason}; AI assessment: the interviewer's comments contradict the ratings - human review"
    if decision is InterviewDecision.SELECTED and advice == "REJECT":
        return review, f"{reason}; AI assessment advises REJECT - human review required"
    if decision is InterviewDecision.REJECTED and advice == "SELECT":
        return review, f"{reason}; AI assessment advises SELECT - human review required"
    return decision, f"{reason}; AI assessment advises {advice}"
