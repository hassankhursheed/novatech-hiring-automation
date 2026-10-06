"""AI output schemas.

Two models on purpose:
  * `CandidateAnalysisWire` is what we ask the model to produce. Provider structured-output APIs do not
    enforce numeric bounds, so it only fixes the shape and the enum.
  * `CandidateAnalysis` is the validated domain object: ranges, lengths and normalisation are checked
    HERE, in our code, before anything is stored or used (the brief requires explicit validation).
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Recommendation(StrEnum):
    SHORTLIST = "SHORTLIST"
    REVIEW = "REVIEW"
    REJECT = "REJECT"


class CandidateAnalysisWire(BaseModel):
    """Advisory screening analysis of one application. Scores are integers from 0 (none) to 10 (excellent)."""

    technical_strength: int = Field(description="0-10: depth of job-relevant technical or functional skills")
    experience_relevance: int = Field(description="0-10: how relevant the experience is to this position")
    communication_indication: int = Field(description="0-10: communication quality evidenced by the CV/cover letter")
    missing_skills: list[str] = Field(description="Important skills for the role that the candidate does not show")
    summary: str = Field(description="2-3 neutral, factual sentences about job-relevant strengths and gaps")
    recommendation: Literal["SHORTLIST", "REVIEW", "REJECT"] = Field(
        description="Advisory only: SHORTLIST, REVIEW (needs a human look) or REJECT"
    )


class CandidateAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    technical_strength: int = Field(ge=0, le=10)
    experience_relevance: int = Field(ge=0, le=10)
    communication_indication: int = Field(ge=0, le=10)
    missing_skills: list[str] = Field(default_factory=list, max_length=15)
    summary: str = Field(min_length=10, max_length=1000)
    recommendation: Recommendation

    @field_validator("missing_skills")
    @classmethod
    def _clean_skills(cls, value: list[str]) -> list[str]:
        cleaned = [s.strip().lower()[:60] for s in value if s and s.strip()]
        return list(dict.fromkeys(cleaned))

    @field_validator("summary")
    @classmethod
    def _clean_summary(cls, value: str) -> str:
        return " ".join(value.replace("**", "").split())


class AnalysisStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FALLBACK = "FALLBACK"


class AnalysisResult(BaseModel):
    """Response of POST /v1/screening/ai-analysis; stored by api.record_ai_analysis()."""

    application_id: str
    status: AnalysisStatus
    prompt_version: str
    input_hash: str
    provider: str | None
    model: str | None
    attempts: int
    latency_ms: int
    trace_id: str | None = None
    fallback_reason: str | None = None
    analysis: CandidateAnalysis | None = None


# ---- interview assessment (after the scorecard) -----------------------------------------------------------
class InterviewRecommendation(StrEnum):
    SELECT = "SELECT"
    REVIEW = "REVIEW"
    REJECT = "REJECT"


class EvidenceAlignment(StrEnum):
    ALIGNED = "ALIGNED"
    PARTIAL = "PARTIAL"
    CONTRADICTORY = "CONTRADICTORY"


class InterviewAssessmentWire(BaseModel):
    """Advisory second opinion on one interview scorecard, written for the hiring manager."""

    recommendation: Literal["SELECT", "REVIEW", "REJECT"] = Field(
        description="Advisory only: SELECT when ratings and comments clearly support hiring for this role, "
        "REJECT when they clearly do not, otherwise REVIEW"
    )
    evidence_alignment: Literal["ALIGNED", "PARTIAL", "CONTRADICTORY"] = Field(
        description="Do the interviewer's written comments support the numeric ratings? "
        "ALIGNED, PARTIAL (important ratings lack support) or CONTRADICTORY (comments contradict ratings)"
    )
    strengths: list[str] = Field(description="Up to 4 job-relevant strengths shown in the interview, each short")
    concerns: list[str] = Field(description="Up to 4 job-relevant concerns or open questions, each short")
    summary: str = Field(description="2-3 neutral sentences for the hiring manager, verifiable against the scorecard")


def _short_items(value: list[str]) -> list[str]:
    cleaned = [" ".join(s.split())[:160] for s in value if s and s.strip()]
    return list(dict.fromkeys(cleaned))


class InterviewAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation: InterviewRecommendation
    evidence_alignment: EvidenceAlignment
    strengths: list[str] = Field(default_factory=list, max_length=6)
    concerns: list[str] = Field(default_factory=list, max_length=6)
    summary: str = Field(min_length=10, max_length=1200)

    @field_validator("strengths", "concerns")
    @classmethod
    def _clean_items(cls, value: list[str]) -> list[str]:
        return _short_items(value)

    @field_validator("summary")
    @classmethod
    def _clean_summary(cls, value: str) -> str:
        return " ".join(value.replace("**", "").split())

    @model_validator(mode="after")
    def _contradiction_needs_a_person(self) -> "InterviewAssessment":
        # Rule, not preference: when the comments contradict the ratings, neither SELECT nor REJECT is trustworthy.
        if self.evidence_alignment is EvidenceAlignment.CONTRADICTORY:
            self.recommendation = InterviewRecommendation.REVIEW
        return self


class AssessmentResult(BaseModel):
    """Response of POST /v1/interviews/ai-assessment; stored by api.record_interview_assessment()."""

    application_id: str
    interview_id: str
    status: AnalysisStatus
    prompt_version: str
    input_hash: str
    provider: str | None
    model: str | None
    attempts: int
    latency_ms: int
    trace_id: str | None = None
    fallback_reason: str | None = None
    assessment: InterviewAssessment | None = None
