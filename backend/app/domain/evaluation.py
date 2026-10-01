"""Interview evaluation policy: combined score and decision after an interview.

final score = application_weight x screening score + interview_weight x interview score   (weights from settings)

  final >= select threshold  -> SELECTED
  final >= review threshold  -> INTERVIEW_REVIEW (hiring manager decides)
  otherwise                  -> REJECTED

The numbers decide. The interviewer's recommendation can only send a case to human review when it
contradicts the numbers; it never silently overrides them.
"""

from dataclasses import dataclass
from enum import StrEnum

POLICY_VERSION = "interview-evaluation-1.0"

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

    def validate(self) -> None:
        if self.application_weight < 0 or self.interview_weight < 0:
            raise EvaluationConfigError("evaluation weights must not be negative")
        if self.application_weight + self.interview_weight <= 0:
            raise EvaluationConfigError("at least one evaluation weight must be positive")
        if not 0 <= self.review_min_score <= self.select_min_score <= 100:
            raise EvaluationConfigError("thresholds must satisfy 0 <= review_min_score <= select_min_score <= 100")


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

    return EvaluationOutcome(
        decision=decision, final_score=final, application_weight=app_w, interview_weight=int_w, reason=reason
    )
