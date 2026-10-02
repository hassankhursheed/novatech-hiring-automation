"""Deterministic offline model (LLM_PROVIDER=stub) for demos and automated scenario tests.

It implements the same StructuredLLM interface as the real client, so every AI code path (validation, corrective
retry, fallback, numeric guardrail, fault injection) runs exactly as in production, without an API key and with
reproducible results. It reads the same prompt the real model gets and applies transparent heuristics; it is not an
assessment of anyone. Refused in production (see Settings).
"""

import json
import re
from typing import Any

from pydantic import BaseModel

from app.ai.llm import LLMOutcome

STUB_MODEL = "stub-deterministic-1"


def _field(prompt: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}:[ \t]*(.*)$", prompt, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _number(text: str) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def candidate_analysis(prompt: str) -> dict[str, Any]:
    screened = [s.strip().lower() for s in _field(prompt, "Skills the company screens for").split(",") if s.strip()]
    declared = {s.strip().lower() for s in _field(prompt, "Declared skills").split(",") if s.strip()}
    application = prompt.split("<application>", 1)[-1].lower()
    matched = [
        s for s in screened if s in declared or re.search(rf"(?<![a-z0-9]){re.escape(s)}(?![a-z0-9])", application)
    ]
    missing = [s for s in screened if s not in matched][:6]
    years = _number(_field(prompt, "Stated experience (years)")) or 0.0
    minimum = _number(_field(prompt, "Minimum experience (years)")) or 0.0
    letter = application.split("cover letter:", 1)[-1].split("cv text:", 1)[0].strip()

    coverage = len(matched) / len(screened) if screened else 0.5
    technical = round(min(10, 2 + 8 * coverage))
    experience = round(min(10, 3 + 7 * min(years / max(minimum * 2, 2), 1))) if years else 2
    communication = 7 if len(letter) > 120 and letter != "(none)" else 5
    if technical >= 7 and years >= minimum:
        recommendation = "SHORTLIST"
    elif technical <= 3 or (minimum and years < minimum / 2):
        recommendation = "REJECT"
    else:
        recommendation = "REVIEW"
    summary = (
        f"Shows {len(matched)} of {len(screened) or 'the'} screened skills"
        f"{' (' + ', '.join(matched[:5]) + ')' if matched else ''} with {years:g} year(s) of stated experience "
        f"against a minimum of {minimum:g}. "
        + (f"Not evidenced: {', '.join(missing[:4])}." if missing else "No screened skill is missing.")
    )
    return {
        "technical_strength": technical,
        "experience_relevance": experience,
        "communication_indication": communication,
        "missing_skills": missing,
        "summary": summary,
        "recommendation": recommendation,
    }


def daily_summary(prompt: str) -> dict[str, Any]:
    metrics = json.loads(prompt.split("Metrics JSON:", 1)[-1].strip() or "{}")

    def n(key: str) -> int:
        value = metrics.get(key)
        return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0

    attention = n("manual_intervention_required")
    return {
        "summary": (
            f"The team received {n('applications_received')} applications and shortlisted {n('shortlisted')}. "
            f"{n('offers_sent')} offers went out and {n('offers_accepted')} were accepted, while "
            f"{n('employees_onboarded')} new employees completed onboarding. "
            + (
                f"{attention} items still need a person's attention."
                if attention
                else "Nothing is waiting for a person."
            )
        )
    }


class StubStructuredLLM:
    provider = "stub"
    model = STUB_MODEL

    def __init__(self, schema: type[BaseModel]) -> None:
        self._schema = schema.__name__

    async def generate(self, system: str, user: str, *, session_id: str | None) -> LLMOutcome:
        if self._schema == "CandidateAnalysisWire":
            return LLMOutcome(candidate_analysis(user), None, "end_turn")
        if self._schema == "DailySummaryWire":
            return LLMOutcome(daily_summary(user), None, "end_turn")
        return LLMOutcome(None, f"stub model has no answer for {self._schema}", "end_turn")
