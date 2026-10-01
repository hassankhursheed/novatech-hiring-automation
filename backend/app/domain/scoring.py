"""Configurable rule-based scoring (pure). Rules and thresholds come from the database.

Score = points awarded / points possible * 100, rounded to 2 decimals.
Route: score >= shortlist_min -> SHORTLIST, >= review_min -> REVIEW, else REJECT.
"""

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.domain.contracts import RuleResult, ScoreRoute
from app.domain.normalization import find_terms


@dataclass(frozen=True)
class ScoringRule:
    rule_key: str
    label: str
    rule_type: str  # SKILL_ANY | SKILL_ALL | MIN_EXPERIENCE_YEARS | KEYWORD_ANY
    match_terms: list[str]
    min_value: float | None
    points: int


@dataclass(frozen=True)
class ScoringConfig:
    position_code: str
    version: int
    shortlist_min_score: float
    review_min_score: float
    rules: list[ScoringRule]


@dataclass(frozen=True)
class ScoringInput:
    skills: list[str]
    experience_years: float | None
    cv_text: str | None
    cover_letter: str | None
    current_title: str | None
    current_company: str | None
    skill_aliases: dict[str, str]


@dataclass(frozen=True)
class ScoringOutcome:
    points_awarded: int
    points_possible: int
    score: float
    route: ScoreRoute
    breakdown: list[RuleResult]
    input_hash: str


class NoActiveRulesError(ValueError):
    pass


def _terms_with_aliases(terms: list[str], aliases: dict[str, str]) -> dict[str, list[str]]:
    """canonical term -> all spellings (canonical + aliases) used to search free text."""
    spellings: dict[str, list[str]] = {t: [t] for t in terms}
    for alias, canonical in aliases.items():
        if canonical in spellings:
            spellings[canonical].append(alias)
    return spellings


def _skill_evidence(term_set: list[str], data: ScoringInput) -> tuple[list[str], str]:
    declared = [t for t in term_set if t in data.skills]
    if declared:
        return declared, "declared skills"
    spellings = _terms_with_aliases(term_set, data.skill_aliases)
    in_cv = [canonical for canonical, forms in spellings.items() if find_terms(data.cv_text, forms)]
    return in_cv, "CV text" if in_cv else "none"


def _free_text(data: ScoringInput) -> str:
    return "\n".join(p for p in (data.cv_text, data.cover_letter, data.current_title, data.current_company) if p)


def input_hash(data: ScoringInput) -> str:
    canonical = json.dumps(
        {
            "skills": sorted(data.skills),
            "experience_years": data.experience_years,
            "text": hashlib.sha256(_free_text(data).encode()).hexdigest(),
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def evaluate_rule(rule: ScoringRule, data: ScoringInput) -> RuleResult:
    matched: list[str] = []
    evidence = "none"
    passed = False

    if rule.rule_type == "SKILL_ANY":
        matched, evidence = _skill_evidence(rule.match_terms, data)
        passed = bool(matched)
    elif rule.rule_type == "SKILL_ALL":
        declared = [t for t in rule.match_terms if t in data.skills]
        spellings = _terms_with_aliases(rule.match_terms, data.skill_aliases)
        from_cv = [c for c, forms in spellings.items() if c not in declared and find_terms(data.cv_text, forms)]
        matched = declared + from_cv
        passed = len(set(matched)) == len(set(rule.match_terms))
        evidence = "declared skills + CV text" if from_cv else "declared skills"
    elif rule.rule_type == "MIN_EXPERIENCE_YEARS":
        years = data.experience_years
        passed = years is not None and rule.min_value is not None and years >= rule.min_value
        matched = [f"{years:g} years"] if years is not None else []
        evidence = "stated experience"
    elif rule.rule_type == "KEYWORD_ANY":
        matched = find_terms(_free_text(data), rule.match_terms)
        passed = bool(matched)
        evidence = "CV / cover letter / current role" if matched else "none"
    else:
        raise ValueError(f"unsupported rule type {rule.rule_type}")

    return RuleResult(
        rule_key=rule.rule_key,
        label=rule.label,
        rule_type=rule.rule_type,
        points_possible=rule.points,
        points_awarded=rule.points if passed else 0,
        matched=matched,
        evidence=evidence,
    )


def route_for(score: float, shortlist_min: float, review_min: float) -> ScoreRoute:
    if score >= shortlist_min:
        return ScoreRoute.SHORTLIST
    if score >= review_min:
        return ScoreRoute.REVIEW
    return ScoreRoute.REJECT


def score_application(config: ScoringConfig, data: ScoringInput) -> ScoringOutcome:
    if not config.rules:
        raise NoActiveRulesError(f"no active scoring rules configured for {config.position_code}")

    breakdown = [evaluate_rule(rule, data) for rule in config.rules]
    awarded = sum(r.points_awarded for r in breakdown)
    possible = sum(r.points_possible for r in breakdown)
    score = float((Decimal(awarded) * 100 / Decimal(possible)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return ScoringOutcome(
        points_awarded=awarded,
        points_possible=possible,
        score=score,
        route=route_for(score, config.shortlist_min_score, config.review_min_score),
        breakdown=breakdown,
        input_hash=input_hash(data),
    )
