"""Advisory AI assessment of an interview scorecard: validated, one corrective retry, safe fallback.

Same contract as the screening analysis (see analyzer.py): the model reads a PII-minimised prompt, its structured
output is validated by our own schema, malformed output gets exactly one corrective retry and then FALLBACK, a refusal
is FALLBACK at once, and provider outages propagate as retryable errors for the orchestrator's backoff.
The result is advice: app.domain.evaluation decides, and the assessment can only send a case to a person.
"""

import hashlib
import time
from dataclasses import dataclass

from pydantic import ValidationError

from app.ai import prompts
from app.ai.analyzer import _validation_summary
from app.ai.llm import LLMOutcome, StructuredLLM
from app.ai.redaction import redact
from app.ai.schemas import AnalysisStatus, AssessmentResult, InterviewAssessment
from app.core.faults import Fault
from app.core.logging import get_logger

log = get_logger(__name__)

MAX_ATTEMPTS = 2

_MALFORMED_SAMPLE: dict[str, object] = {  # fault injection only: proves the fallback path
    "recommendation": "HIRE_TODAY",
    "evidence_alignment": "MAYBE",
    "summary": "",
}


@dataclass(frozen=True)
class InterviewContext:
    application_id: str
    interview_id: str
    candidate_name: str | None
    interviewer_name: str | None
    position_title: str
    department: str
    position_description: str | None
    screening_score: float | None
    screening_summary: str | None
    missing_skills: list[str]
    technical_skills: int
    communication: int
    problem_solving: int
    experience: int
    team_fit: int
    interview_score: float
    interviewer_recommendation: str
    comments: str


def build_messages(ctx: InterviewContext, company: str) -> tuple[str, str]:
    names = [n for n in (ctx.candidate_name, ctx.interviewer_name) if n]
    system = prompts.INTERVIEW_SYSTEM_PROMPT.format(company=company)
    user = prompts.INTERVIEW_USER_PROMPT.format(
        position_title=ctx.position_title,
        department=ctx.department,
        position_description=ctx.position_description or "n/a",
        screening_score="not available" if ctx.screening_score is None else f"{ctx.screening_score:g}",
        screening_summary=redact(ctx.screening_summary, extra_terms=names) or "not available",
        missing_skills=", ".join(ctx.missing_skills) or "none recorded",
        technical_skills=ctx.technical_skills,
        communication=ctx.communication,
        problem_solving=ctx.problem_solving,
        experience=ctx.experience,
        team_fit=ctx.team_fit,
        interview_score=f"{ctx.interview_score:g}",
        interviewer_recommendation=ctx.interviewer_recommendation.replace("_", " "),
        comments=redact(ctx.comments, extra_terms=names, max_chars=4000) or "(no comments)",
    )
    return system, user


class InterviewAssessor:
    def __init__(self, llm: StructuredLLM | None) -> None:
        self._llm = llm

    @property
    def enabled(self) -> bool:
        return self._llm is not None

    async def assess(
        self, ctx: InterviewContext, *, company: str, correlation_id: str | None, fault: Fault | None = None
    ) -> AssessmentResult:
        system, user = build_messages(ctx, company)
        provider = self._llm.provider if self._llm else None
        model = self._llm.model if self._llm else None
        input_hash = hashlib.sha256(
            "\x1f".join([prompts.INTERVIEW_PROMPT_VERSION, provider or "", model or "", system, user]).encode()
        ).hexdigest()
        started = time.perf_counter()

        def result(
            status: AnalysisStatus,
            attempts: int,
            *,
            assessment: InterviewAssessment | None = None,
            reason: str | None = None,
            trace_id: str | None = None,
        ) -> AssessmentResult:
            return AssessmentResult(
                application_id=ctx.application_id,
                interview_id=ctx.interview_id,
                status=status,
                prompt_version=prompts.INTERVIEW_PROMPT_VERSION,
                input_hash=input_hash,
                provider=provider,
                model=model,
                attempts=attempts,
                latency_ms=int((time.perf_counter() - started) * 1000),
                trace_id=trace_id,
                fallback_reason=reason,
                assessment=assessment,
            )

        if self._llm is None:
            return result(AnalysisStatus.FALLBACK, 0, reason="AI_DISABLED: no LLM provider configured")

        last_error = "unknown"
        trace_id: str | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            prompt = user if attempt == 1 else user + prompts.INTERVIEW_RETRY_SUFFIX.format(error=last_error)
            outcome = await self._llm.generate(system, prompt, session_id=correlation_id)
            trace_id = outcome.trace_id or trace_id
            if fault is not None and fault.mode == "malformed":
                outcome = LLMOutcome(dict(_MALFORMED_SAMPLE), None, "end_turn", outcome.trace_id)

            if outcome.stop_reason == "refusal":
                log.warning("ai_refusal", interview_id=ctx.interview_id, attempt=attempt)
                return result(
                    AnalysisStatus.FALLBACK, attempt, reason="MODEL_REFUSAL: the model declined", trace_id=trace_id
                )
            if outcome.parsed is None:
                last_error = f"output was not valid structured JSON ({outcome.parsing_error or 'no content'})"
            else:
                try:
                    assessment = InterviewAssessment.model_validate(outcome.parsed)
                except ValidationError as exc:
                    last_error = _validation_summary(exc)
                else:
                    log.info(
                        "ai_interview_assessment_completed",
                        interview_id=ctx.interview_id,
                        attempts=attempt,
                        recommendation=assessment.recommendation.value,
                        evidence_alignment=assessment.evidence_alignment.value,
                    )
                    return result(AnalysisStatus.COMPLETED, attempt, assessment=assessment, trace_id=trace_id)
            log.warning("ai_output_invalid", interview_id=ctx.interview_id, attempt=attempt, error=last_error)

        return result(
            AnalysisStatus.FALLBACK, MAX_ATTEMPTS, reason=f"MALFORMED_OUTPUT: {last_error}"[:500], trace_id=trace_id
        )
