"""Daily management report: deterministic text and the numeric guardrail for AI-written summaries.

Every number in the report comes from SQL (reporting.daily_metrics). An AI may rewrite the prose, but any number
in its text that is not in the input (a miscount, a computed percentage, an invented figure) rejects the AI text,
and the deterministic summary is used instead.
"""

import re
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

_NUMBER = re.compile(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?")


def _numbers(value: Any) -> Iterable[Decimal]:
    if isinstance(value, bool):
        return
    if isinstance(value, int | float | Decimal):
        yield Decimal(str(value))
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _numbers(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _numbers(item)


def allowed_numbers(metrics: Mapping[str, Any], report_date: date) -> set[Decimal]:
    allowed = {n.normalize() for n in _numbers(metrics)}
    allowed |= {Decimal(report_date.year), Decimal(report_date.month), Decimal(report_date.day)}
    return allowed


def numbers_in_text(text: str) -> list[Decimal]:
    found: list[Decimal] = []
    for match in _NUMBER.findall(text):
        try:
            found.append(Decimal(match.replace(",", "")).normalize())
        except InvalidOperation:
            continue
    return found


def unsupported_numbers(text: str, metrics: Mapping[str, Any], report_date: date) -> list[str]:
    allowed = allowed_numbers(metrics, report_date)
    return sorted({format(n, "f") for n in numbers_in_text(text) if n not in allowed})


def _n(metrics: Mapping[str, Any], key: str) -> int:
    value = metrics.get(key)
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def template_summary(metrics: Mapping[str, Any], report_date: date) -> str:
    """Plain, factual summary built only from the metrics. Used when AI is disabled or its text is rejected."""
    m = metrics
    parts = [
        f"On {report_date.isoformat()} NovaTech received {_n(m, 'applications_received')} application(s): "
        f"{_n(m, 'shortlisted')} shortlisted, {_n(m, 'rejected')} rejected and {_n(m, 'manual_review')} sent to "
        f"manual review.",
        f"Offers: {_n(m, 'offers_sent')} sent, {_n(m, 'offers_accepted')} accepted, {_n(m, 'offers_declined')} "
        f"declined, {_n(m, 'offers_expired')} expired; {_n(m, 'offers_pending_approval')} await approval.",
        f"Onboarding: {_n(m, 'employees_onboarding')} in progress, {_n(m, 'employees_onboarded')} completed, "
        f"{_n(m, 'overdue_onboarding_tasks')} overdue task(s).",
        f"Automation: {_n(m, 'workflow_executions')} workflow run(s), {_n(m, 'workflow_failures')} failure(s), "
        f"{_n(m, 'retries_recovered')} recovered after retry, {_n(m, 'duplicates_prevented')} duplicate(s) "
        f"prevented.",
    ]
    attention = _n(m, "manual_intervention_required")
    parts.append(
        f"{attention} item(s) need manual attention (error queue and review queues)."
        if attention
        else "No item needs manual attention."
    )
    return " ".join(parts)


# Order and labels of the metrics table in the report email.
REPORT_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Recruitment",
        [
            ("applications_received", "Applications received"),
            ("invalid_applications", "Invalid applications"),
            ("duplicate_submissions", "Duplicate submissions"),
            ("shortlisted", "Shortlisted"),
            ("rejected", "Rejected"),
            ("manual_review", "Sent to manual review"),
            ("interviews_pending", "Interviews pending"),
            ("selected", "Selected after interview"),
        ],
    ),
    (
        "Offers and onboarding",
        [
            ("offers_pending_approval", "Offers awaiting approval"),
            ("offers_sent", "Offers sent"),
            ("offers_accepted", "Offers accepted"),
            ("offers_declined", "Offers declined"),
            ("offers_expired", "Offers expired"),
            ("employees_onboarding", "Employees onboarding"),
            ("employees_onboarded", "Employees onboarded"),
            ("overdue_onboarding_tasks", "Overdue onboarding tasks"),
        ],
    ),
    (
        "Automation health",
        [
            ("workflow_executions", "Workflow executions"),
            ("workflow_failures", "Workflow failures"),
            ("retry_attempts", "Retry attempts"),
            ("retries_recovered", "Recovered after retry"),
            ("duplicates_prevented", "Duplicates prevented"),
            ("event_replays_ignored", "Replayed webhooks ignored"),
            ("duplicate_notifications_suppressed", "Duplicate messages suppressed"),
            ("scheduled_actions_failed", "Failed scheduled actions"),
            ("manual_intervention_required", "Items needing manual action"),
        ],
    ),
]
