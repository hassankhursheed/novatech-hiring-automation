"""Screening decision policy (pure).

Principle: the configured rules decide the route; AI is advisory and can only ADD a human review,
never remove one. The AI can never shortlist or reject on its own.

  AI enabled but unavailable/malformed -> SCREENING_REVIEW (brief: unrecoverable AI failures go to review)
  rules SHORTLIST + AI REJECT            -> SCREENING_REVIEW (disagreement)
  rules SHORTLIST + AI SHORTLIST/REVIEW  -> SHORTLISTED
  rules REVIEW                           -> SCREENING_REVIEW
  rules REJECT + AI SHORTLIST            -> SCREENING_REVIEW (disagreement)
  rules REJECT otherwise                 -> REJECTED (or SCREENING_REVIEW when auto-reject is disabled)
"""

from dataclasses import dataclass

from app.domain.contracts import AISummaryInput, ScoreRoute, ScoreSummaryInput, ScreeningDecisionType

POLICY_VERSION = "screening-policy/1.0.0"


@dataclass(frozen=True)
class PolicySettings:
    ai_enabled: bool
    auto_reject_enabled: bool


@dataclass(frozen=True)
class PolicyResult:
    decision: ScreeningDecisionType
    reason: str


def decide(score: ScoreSummaryInput, ai: AISummaryInput | None, settings: PolicySettings) -> PolicyResult:
    rule_text = f"rule score {score.score:g} ({score.route.value})"
    ai_status = ai.status.upper() if ai else "MISSING"
    ai_rec = (ai.recommendation or "").upper() if ai and ai_status == "COMPLETED" else None

    if settings.ai_enabled and ai_rec is None:
        why = ai.fallback_reason if ai and ai.fallback_reason else "no AI analysis available"
        return PolicyResult(
            ScreeningDecisionType.SCREENING_REVIEW,
            f"{rule_text}; AI analysis unavailable ({why}) - human review required",
        )

    if score.route is ScoreRoute.REVIEW:
        suffix = f"; AI advises {ai_rec}" if ai_rec else ""
        return PolicyResult(ScreeningDecisionType.SCREENING_REVIEW, f"{rule_text} is in the review band{suffix}")

    if score.route is ScoreRoute.SHORTLIST:
        if ai_rec == "REJECT":
            return PolicyResult(
                ScreeningDecisionType.SCREENING_REVIEW, f"{rule_text} but AI advises REJECT - human review required"
            )
        suffix = f"; AI advises {ai_rec}" if ai_rec else ""
        return PolicyResult(ScreeningDecisionType.SHORTLISTED, f"{rule_text} meets the shortlist threshold{suffix}")

    # rules say REJECT
    if ai_rec == "SHORTLIST":
        return PolicyResult(
            ScreeningDecisionType.SCREENING_REVIEW, f"{rule_text} but AI advises SHORTLIST - human review required"
        )
    if not settings.auto_reject_enabled:
        return PolicyResult(
            ScreeningDecisionType.SCREENING_REVIEW,
            f"{rule_text} is below the review threshold; automatic rejection is disabled",
        )
    suffix = f"; AI advises {ai_rec}" if ai_rec else ""
    return PolicyResult(ScreeningDecisionType.REJECTED, f"{rule_text} is below the review threshold{suffix}")
