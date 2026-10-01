import dataclasses

import pytest

from app.domain.contracts import ScoreRoute
from app.domain.scoring import NoActiveRulesError, input_hash, route_for, score_application
from tests.conftest import PY_CONFIG, scoring_input


def test_exceptional_candidate_scores_100() -> None:
    data = scoring_input(
        skills=["python", "fastapi", "postgresql", "rest apis", "git", "docker", "aws"],
        experience_years=5,
        cv_text="Five years in fintech.",
    )
    outcome = score_application(PY_CONFIG, data)
    assert outcome.score == 100.0
    assert outcome.route is ScoreRoute.SHORTLIST
    assert outcome.points_awarded == outcome.points_possible == 100


def test_weak_candidate_is_rejected_with_explanation() -> None:
    outcome = score_application(PY_CONFIG, scoring_input(skills=["python"], experience_years=0.5))
    assert outcome.score == 20.0
    assert outcome.route is ScoreRoute.REJECT
    by_key = {r.rule_key: r for r in outcome.breakdown}
    assert by_key["python"].points_awarded == 20 and by_key["python"].evidence == "declared skills"
    assert by_key["experience"].points_awarded == 0


def test_skills_found_only_in_cv_text_count_with_aliases() -> None:
    data = scoring_input(skills=["python"], cv_text="Maintained REST API services on Postgres, code on GitHub.")
    by_key = {r.rule_key: r for r in score_application(PY_CONFIG, data).breakdown}
    assert by_key["sql"].points_awarded == 10 and by_key["sql"].evidence == "CV text"
    assert by_key["rest_apis"].points_awarded == 10
    assert by_key["git"].points_awarded == 5


def test_changed_configuration_changes_the_outcome_without_code_changes() -> None:
    data = scoring_input(skills=["python", "fastapi", "postgresql", "rest apis", "git", "docker"], experience_years=3)
    before = score_application(PY_CONFIG, data)
    assert (before.score, before.route) == (80.0, ScoreRoute.SHORTLIST)

    stricter = dataclasses.replace(PY_CONFIG, version=4, shortlist_min_score=90)
    assert score_application(stricter, data).route is ScoreRoute.REVIEW

    heavier_docker = dataclasses.replace(
        PY_CONFIG,
        version=5,
        rules=[dataclasses.replace(r, points=30) if r.rule_key == "docker" else r for r in PY_CONFIG.rules],
    )
    assert score_application(heavier_docker, data).score == pytest.approx(83.33, abs=0.01)


@pytest.mark.parametrize(
    ("score", "route"),
    [
        (80, ScoreRoute.SHORTLIST),
        (79.99, ScoreRoute.REVIEW),
        (60, ScoreRoute.REVIEW),
        (59.99, ScoreRoute.REJECT),
    ],
)
def test_route_boundaries(score: float, route: ScoreRoute) -> None:
    assert route_for(score, 80, 60) is route


def test_no_active_rules_is_an_error() -> None:
    with pytest.raises(NoActiveRulesError):
        score_application(dataclasses.replace(PY_CONFIG, rules=[]), scoring_input())


def test_input_hash_is_stable_and_order_independent() -> None:
    a = scoring_input(skills=["python", "docker"], experience_years=3, cv_text="x")
    b = scoring_input(skills=["docker", "python"], experience_years=3, cv_text="x")
    c = scoring_input(skills=["docker", "python"], experience_years=4, cv_text="x")
    assert input_hash(a) == input_hash(b) != input_hash(c)
