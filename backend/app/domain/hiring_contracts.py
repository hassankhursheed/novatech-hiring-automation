"""JSON contracts for interviews, offers, links, reports and staff actions."""

from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.links import LinkPurpose
from app.domain.evaluation import InterviewDecision

_STRICT = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---- signed links (n8n -> backend) ------------------------------------------------------------------
class LinkRequest(BaseModel):
    model_config = _STRICT

    purpose: LinkPurpose
    subject_id: UUID = Field(description="Candidate id (candidate links) or staff member id (staff links)")
    entity_id: UUID = Field(description="Interview id or offer id")
    expires_at: datetime | None = Field(default=None, description="Business deadline; capped by LINK_MAX_TTL_DAYS")


class LinkResponse(BaseModel):
    purpose: LinkPurpose
    url: str
    token: str
    expires_at: datetime


# ---- interview evaluation (n8n WF-04) ---------------------------------------------------------------
class EvaluationRequest(BaseModel):
    model_config = _STRICT

    application_id: UUID


class EvaluationResult(BaseModel):
    application_id: str
    application_status: str
    decision: InterviewDecision
    final_score: float
    application_score: float | None
    interview_score: float
    recommendation: str
    ai_recommendation: str | None = None
    ai_evidence_alignment: str | None = None
    weights: dict[str, float]
    thresholds: dict[str, float]
    reason: str
    policy_version: str


# ---- offer documents (n8n WF-05) --------------------------------------------------------------------
class OfferRef(BaseModel):
    model_config = _STRICT

    offer_id: UUID


class OfferDocumentResult(BaseModel):
    offer_id: str
    offer_code: str
    document_key: str
    sha256: str
    size_bytes: int


# ---- daily report (n8n WF-08) -----------------------------------------------------------------------
class DailySummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_date: date
    metrics: dict[str, Any]


class MetricRow(BaseModel):
    key: str
    label: str
    value: Any


class MetricSection(BaseModel):
    title: str
    rows: list[MetricRow]


class DailySummaryResult(BaseModel):
    report_date: date
    summary: str
    summary_source: str
    fallback_reason: str | None
    sections: list[MetricSection]


# ---- candidate and staff actions --------------------------------------------------------------------
class SlotChoice(BaseModel):
    model_config = _STRICT

    slot_id: UUID


class CancelRequest(BaseModel):
    model_config = _STRICT

    reason: str = Field(min_length=3, max_length=500)


class OfferResponseType(StrEnum):
    ACCEPT = "ACCEPT"
    DECLINE = "DECLINE"
    NEGOTIATE = "NEGOTIATE"


class OfferResponseRequest(BaseModel):
    model_config = _STRICT

    response: OfferResponseType
    message: str | None = Field(default=None, max_length=2000)


class Recommendation(StrEnum):
    STRONG_HIRE = "STRONG_HIRE"
    HIRE = "HIRE"
    NO_HIRE = "NO_HIRE"
    STRONG_NO_HIRE = "STRONG_NO_HIRE"


class FeedbackRequest(BaseModel):
    model_config = _STRICT

    technical_skills: int = Field(ge=1, le=5)
    communication: int = Field(ge=1, le=5)
    problem_solving: int = Field(ge=1, le=5)
    experience: int = Field(ge=1, le=5)
    team_fit: int = Field(ge=1, le=5)
    recommendation: Recommendation
    comments: str = Field(min_length=3, max_length=4000)


class ApprovalDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalRequest(BaseModel):
    model_config = _STRICT

    decision: ApprovalDecision
    reason: str | None = Field(default=None, max_length=1000)
    level: int | None = Field(default=None, ge=1, le=2, description="Defaults to the next pending level")


class TransitionRequest(BaseModel):
    model_config = _STRICT

    to_status: str = Field(pattern=r"^[A-Z_]{3,40}$")
    reason: str = Field(min_length=3, max_length=1000)
    expected_from: str | None = Field(default=None, pattern=r"^[A-Z_]{3,40}$")


class OfferTerms(BaseModel):
    model_config = _STRICT

    monthly_salary: int | None = Field(default=None, gt=0, le=100_000_000)
    joining_date: date | None = None
    probation_months: int | None = Field(default=None, ge=0, le=12)
    department: str | None = Field(default=None, max_length=120)
    reporting_manager_id: UUID | None = None


class ActionResult(BaseModel):
    """Result of a person's action: what changed, plus whatever the database function returned."""

    model_config = ConfigDict(extra="allow")

    changed: bool | None = None
