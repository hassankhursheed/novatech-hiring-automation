import pytest

from app.domain.contracts import AISummaryInput, ScoreRoute, ScoreSummaryInput, ScreeningDecisionType
from app.domain.screening_policy import PolicySettings, decide

AI_ON = PolicySettings(ai_enabled=True, auto_reject_enabled=True)
S, R, J = ScreeningDecisionType.SHORTLISTED, ScreeningDecisionType.SCREENING_REVIEW, ScreeningDecisionType.REJECTED


def ai(rec: str) -> AISummaryInput:
    return AISummaryInput(status="COMPLETED", recommendation=rec)


@pytest.mark.parametrize(
    ("route", "ai_rec", "expected"),
    [
        (ScoreRoute.SHORTLIST, "SHORTLIST", S),
        (ScoreRoute.SHORTLIST, "REVIEW", S),
        (ScoreRoute.SHORTLIST, "REJECT", R),  # disagreement -> human
        (ScoreRoute.REVIEW, "SHORTLIST", R),
        (ScoreRoute.REVIEW, "REJECT", R),
        (ScoreRoute.REJECT, "REJECT", J),
        (ScoreRoute.REJECT, "REVIEW", J),
        (ScoreRoute.REJECT, "SHORTLIST", R),  # AI can add review, never shortlist
    ],
)
def test_rules_decide_ai_can_only_add_review(route: ScoreRoute, ai_rec: str, expected: ScreeningDecisionType) -> None:
    assert decide(ScoreSummaryInput(route=route, score=70), ai(ai_rec), AI_ON).decision is expected


def test_ai_fallback_routes_to_review_even_for_strong_rule_score() -> None:
    fallback = AISummaryInput(status="FALLBACK", fallback_reason="MALFORMED_OUTPUT: bad json")
    result = decide(ScoreSummaryInput(route=ScoreRoute.SHORTLIST, score=95), fallback, AI_ON)
    assert result.decision is R and "MALFORMED_OUTPUT" in result.reason


def test_ai_disabled_uses_rules_only() -> None:
    off = PolicySettings(ai_enabled=False, auto_reject_enabled=True)
    assert decide(ScoreSummaryInput(route=ScoreRoute.SHORTLIST, score=90), None, off).decision is S
    assert decide(ScoreSummaryInput(route=ScoreRoute.REJECT, score=10), None, off).decision is J


def test_auto_reject_disabled_sends_rejections_to_review() -> None:
    no_auto = PolicySettings(ai_enabled=True, auto_reject_enabled=False)
    assert decide(ScoreSummaryInput(route=ScoreRoute.REJECT, score=10), ai("REJECT"), no_auto).decision is R
