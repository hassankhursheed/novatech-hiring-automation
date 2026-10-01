"""Internal JSON contracts shared by n8n workflows and the database api.* functions.

`ApplicationSubmission` is deliberately lenient (it is whatever a form or job board sent us).
`NormalizedApplication` is strict: it is the contract every downstream workflow relies on.
"""

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    ERROR = "ERROR"  # submission unusable -> INVALID (recorded with reason, never dropped)
    REVIEW = "REVIEW"  # incomplete/suspicious -> manual review with reason
    WARNING = "WARNING"  # informational only


class ValidationOutcome(StrEnum):
    VALID = "VALID"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    INVALID = "INVALID"


class ValidationIssue(BaseModel):
    field: str
    code: str
    severity: Severity
    message: str


class ApplicationSubmission(BaseModel):
    """Raw inbound application. Types are loose on purpose; validation happens in the validator."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    position: str | None = Field(default=None, description="Position code (PY_DEV) or title (Python Developer)")
    experience_years: str | float | int | None = None
    skills: list[str] | str | None = None
    expected_salary: str | float | int | None = None
    salary_currency: str | None = None
    available_from: str | date | None = Field(default=None, description="Earliest joining date")
    notice_period_days: str | int | None = None
    current_company: str | None = None
    current_title: str | None = None
    city: str | None = None
    linkedin_url: str | None = None
    cover_letter: str | None = None
    cv_ref: str | None = Field(default=None, description="Storage key returned by POST /v1/public/cv")
    consent: bool | str | None = None


class NormalizedApplication(BaseModel):
    """Canonical application contract used by every downstream workflow."""

    model_config = ConfigDict(extra="forbid")

    full_name: str | None
    email: str | None
    phone: str | None = Field(description="E.164, e.g. +923001234567")
    position_code: str | None
    position_title: str | None
    experience_years: float | None
    skills: list[str] = Field(description="Lower-case canonical skill names")
    expected_salary: int | None
    salary_currency: str
    available_from: date | None
    notice_period_days: int | None
    current_company: str | None
    current_title: str | None
    city: str | None
    linkedin_url: str | None
    cover_letter: str | None
    cv_storage_key: str | None
    cv_text: str | None
    consent: bool


class ValidationResult(BaseModel):
    outcome: ValidationOutcome
    issues: list[ValidationIssue]
    application: NormalizedApplication
    validator_version: str
    correlation_id: str | None = None


# ---- Screening -------------------------------------------------------------------------------


class ScoreRoute(StrEnum):
    SHORTLIST = "SHORTLIST"
    REVIEW = "REVIEW"
    REJECT = "REJECT"


class ApplicationRef(BaseModel):
    application_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")


class RuleResult(BaseModel):
    rule_key: str
    label: str
    rule_type: str
    points_possible: int
    points_awarded: int
    matched: list[str]
    evidence: str


class ScoreResult(BaseModel):
    application_id: str
    position_code: str
    scoring_version: int
    input_hash: str
    points_awarded: int
    points_possible: int
    score: float
    route: ScoreRoute
    shortlist_min_score: float
    review_min_score: float
    breakdown: list[RuleResult]


class ScreeningDecisionType(StrEnum):
    SHORTLISTED = "SHORTLISTED"
    SCREENING_REVIEW = "SCREENING_REVIEW"
    REJECTED = "REJECTED"


class AISummaryInput(BaseModel):
    status: str
    recommendation: str | None = None
    fallback_reason: str | None = None


class ScoreSummaryInput(BaseModel):
    route: ScoreRoute
    score: float


class DecideRequest(BaseModel):
    score: ScoreSummaryInput
    ai: AISummaryInput | None = None


class ScreeningDecision(BaseModel):
    decision: ScreeningDecisionType
    reason: str
    policy_version: str
    inputs: dict[str, Any]
