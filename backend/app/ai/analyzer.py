"""Advisory candidate analysis with explicit validation, one bounded retry and a safe fallback.

Flow:
  1. Build a PII-minimised, injection-hardened prompt.
  2. Call the model for structured output.
  3. Validate shape AND ranges with our own schema (CandidateAnalysis).
  4. Invalid/malformed -> exactly one corrective retry -> still invalid -> FALLBACK (routes to manual review).
  5. Refusal -> FALLBACK immediately (retrying a refusal does not help).
Transport errors (provider down) propagate as retryable 503s so the orchestrator applies backoff;
there is no retry loop inside this service beyond the single corrective attempt.
"""

import hashlib
import time
from dataclasses import dataclass

from pydantic import ValidationError

from app.ai import prompts
from app.ai.llm import LLMOutcome, StructuredLLM
from app.ai.redaction import redact
from app.ai.schemas import AnalysisResult, AnalysisStatus, CandidateAnalysis
from app.core.faults import Fault
from app.core.logging import get_logger

log = get_logger(__name__)

MAX_ATTEMPTS = 2  # first try + one corrective retry


@dataclass(frozen=True)
class CandidateContext:
    application_id: str
    candidate_name: str | None
    position_title: str
    department: str
    min_experience_years: float
    position_description: str | None
    screened_skills: list[str]
    experience_years: float | None
    skills: list[str]
    current_title: str | None
    cover_letter: str | None
    cv_text: str | None


def build_messages(ctx: CandidateContext, company: str) -> tuple[str, str]:
    names = [ctx.candidate_name] if ctx.candidate_name else []
    system = prompts.SYSTEM_PROMPT.format(company=company)
    user = prompts.USER_PROMPT.format(
        position_title=ctx.position_title,
        department=ctx.department,
        min_experience=f"{ctx.min_experience_years:g}",
        position_description=ctx.position_description or "n/a",
        screened_skills=", ".join(ctx.screened_skills) or "n/a",
        experience_years="not stated" if ctx.experience_years is None else f"{ctx.experience_years:g}",
        skills=", ".join(ctx.skills) or "none listed",
        current_title=redact(ctx.current_title, extra_terms=names) or "not stated",
        cover_letter=redact(ctx.cover_letter, extra_terms=names, max_chars=3000) or "(none)",
        cv_text=redact(ctx.cv_text, extra_terms=names) or "(no CV text available)",
    )
    return system, user


def _validation_summary(exc: ValidationError) -> str:
    parts = [f"{'.'.join(str(p) for p in e['loc']) or 'output'}: {e['msg']}" for e in exc.errors()[:5]]
    return "; ".join(parts)


_MALFORMED_SAMPLE: dict[str, object] = {  # used only by fault injection to prove the fallback path
    "technical_strength": "very strong",
    "experience_relevance": 14,
    "summary": "",
    "recommendation": "HIRE_NOW",
}


class CandidateAnalyzer:
    def __init__(self, llm: StructuredLLM | None) -> None:
        self._llm = llm

    async def analyze(
        self, ctx: CandidateContext, *, company: str, correlation_id: str | None, fault: Fault | None = None
    ) -> AnalysisResult:
        system, user = build_messages(ctx, company)
        provider = self._llm.provider if self._llm else None
        model = self._llm.model if self._llm else None
        input_hash = hashlib.sha256(
            "\x1f".join([prompts.PROMPT_VERSION, provider or "", model or "", system, user]).encode()
        ).hexdigest()
        started = time.perf_counter()

        def result(
            status: AnalysisStatus,
            attempts: int,
            *,
            analysis: CandidateAnalysis | None = None,
            reason: str | None = None,
            trace_id: str | None = None,
        ) -> AnalysisResult:
            return AnalysisResult(
                application_id=ctx.application_id,
                status=status,
                prompt_version=prompts.PROMPT_VERSION,
                input_hash=input_hash,
                provider=provider,
                model=model,
                attempts=attempts,
                latency_ms=int((time.perf_counter() - started) * 1000),
                trace_id=trace_id,
                fallback_reason=reason,
                analysis=analysis,
            )

        if self._llm is None:
            return result(AnalysisStatus.FALLBACK, 0, reason="AI_DISABLED: no LLM provider configured")

        last_error = "unknown"
        trace_id: str | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            prompt = user if attempt == 1 else user + prompts.RETRY_SUFFIX.format(error=last_error)
            outcome = await self._llm.generate(system, prompt, session_id=correlation_id)
            trace_id = outcome.trace_id or trace_id
            if fault is not None and fault.mode == "malformed":
                outcome = LLMOutcome(
                    parsed=dict(_MALFORMED_SAMPLE),
                    parsing_error=None,
                    stop_reason="end_turn",
                    trace_id=outcome.trace_id,
                )

            if outcome.stop_reason == "refusal":
                log.warning("ai_refusal", application_id=ctx.application_id, attempt=attempt)
                return result(
                    AnalysisStatus.FALLBACK,
                    attempt,
                    reason="MODEL_REFUSAL: the model declined to assess",
                    trace_id=trace_id,
                )

            if outcome.parsed is None:
                last_error = f"output was not valid structured JSON ({outcome.parsing_error or 'no content'})"
            else:
                try:
                    analysis = CandidateAnalysis.model_validate(outcome.parsed)
                except ValidationError as exc:
                    last_error = _validation_summary(exc)
                else:
                    log.info(
                        "ai_analysis_completed",
                        application_id=ctx.application_id,
                        attempts=attempt,
                        recommendation=analysis.recommendation.value,
                    )
                    return result(AnalysisStatus.COMPLETED, attempt, analysis=analysis, trace_id=trace_id)

            log.warning("ai_output_invalid", application_id=ctx.application_id, attempt=attempt, error=last_error)

        return result(
            AnalysisStatus.FALLBACK, MAX_ATTEMPTS, reason=f"MALFORMED_OUTPUT: {last_error}"[:500], trace_id=trace_id
        )
