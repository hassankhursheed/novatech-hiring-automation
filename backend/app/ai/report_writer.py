"""Optional AI prose for the daily management report, behind a numeric guardrail.

SQL produces every number. The model only turns them into a short narrative. Its text is rejected (and the
deterministic summary is used) when it is missing, malformed, refused, or contains any number not present
in the metrics. A provider outage also falls back: the report must go out even when the AI does not answer.
"""

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.ai.llm import StructuredLLM
from app.core.errors import UpstreamRejectedError, UpstreamUnavailableError
from app.core.logging import get_logger
from app.domain.report_summary import template_summary, unsupported_numbers

log = get_logger(__name__)


class DailySummaryWire(BaseModel):
    """Structured output requested from the model (no numeric bounds: some providers reject them)."""

    summary: str = Field(description="3 to 5 plain sentences for the management team")


class DailySummary(BaseModel):
    summary: str = Field(min_length=20, max_length=1500)


SYSTEM_PROMPT = (
    "You write the daily recruitment operations summary for {company}'s management team.\n"
    "Rules:\n"
    "- Use ONLY the numbers in the metrics JSON, exactly as given. Never calculate totals, differences, "
    "averages or percentages, and never estimate.\n"
    "- Write 3 to 5 short, neutral sentences: recruitment progress first, then offers and onboarding, then "
    "anything that needs attention (failures, manual review, overdue tasks).\n"
    "- Do not mention candidates by name, and do not give hiring advice."
)


@dataclass(frozen=True)
class ReportSummaryResult:
    summary: str
    source: str  # AI | TEMPLATE
    fallback_reason: str | None
    model: str | None


class ReportWriter:
    def __init__(self, llm: StructuredLLM | None) -> None:
        self._llm = llm

    async def write(
        self, metrics: dict[str, Any], report_date: date, *, company: str, correlation_id: str | None
    ) -> ReportSummaryResult:
        fallback = template_summary(metrics, report_date)
        if self._llm is None:
            return ReportSummaryResult(fallback, "TEMPLATE", "AI is disabled", None)

        user = f"Report date: {report_date.isoformat()}\nMetrics JSON:\n{json.dumps(metrics, sort_keys=True)}"
        try:
            outcome = await self._llm.generate(SYSTEM_PROMPT.format(company=company), user, session_id=correlation_id)
        except (UpstreamUnavailableError, UpstreamRejectedError) as exc:
            log.warning("report_summary_ai_unavailable", code=exc.code)
            return ReportSummaryResult(fallback, "TEMPLATE", f"AI unavailable ({exc.code})", self._llm.model)

        if outcome.stop_reason == "refusal" or outcome.parsed is None:
            reason = (
                "AI refused" if outcome.stop_reason == "refusal" else f"malformed AI output: {outcome.parsing_error}"
            )
            return ReportSummaryResult(fallback, "TEMPLATE", reason, self._llm.model)
        try:
            text = DailySummary.model_validate(outcome.parsed).summary.strip()
        except ValidationError as exc:
            return ReportSummaryResult(fallback, "TEMPLATE", f"invalid AI output: {exc.error_count()} error(s)", None)

        bad = unsupported_numbers(text, metrics, report_date)
        if bad:
            log.warning("report_summary_rejected", unsupported_numbers=bad)
            return ReportSummaryResult(
                fallback,
                "TEMPLATE",
                f"AI text rejected: numbers not in the metrics ({', '.join(bad)})",
                self._llm.model,
            )
        return ReportSummaryResult(text, "AI", None, self._llm.model)
