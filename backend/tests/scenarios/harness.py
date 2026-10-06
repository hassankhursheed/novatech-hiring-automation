"""Scenario harness: drives the running stack the way people and systems do, and collects the evidence.

Inputs go only through public interfaces: the intake webhook (n8n), CV upload, signed-link portal API, staff API and
the authenticated ops webhooks. Evidence is read from PostgreSQL (status history, logs, errors, notifications) and
from Mailpit (the emails people actually receive).

The one privileged action is time travel, used instead of waiting days: moving a timer's run_at (or a deadline) to
now. It touches only scheduling data, never a business record's state, which still changes only through the
workflows and api.* functions.
"""

import contextlib
import io
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

BACKEND = os.environ.get("SCENARIO_BACKEND_URL", "http://backend:8000")
N8N = os.environ.get("SCENARIO_N8N_URL", "http://n8n:5678")
MAILPIT = os.environ.get("SCENARIO_MAILPIT_URL", "http://mailpit:8025")

STAFF = {
    "sana": "00000000-0000-4000-8000-000000000001",  # HR admin, recruiter
    "bilal": "00000000-0000-4000-8000-000000000002",  # recruiter
    "ayesha": "00000000-0000-4000-8000-000000000003",  # hiring manager, interviewer, approver L1
    "usman": "00000000-0000-4000-8000-000000000004",  # interviewer
    "fatima": "00000000-0000-4000-8000-000000000005",  # interviewer
    "hamza": "00000000-0000-4000-8000-000000000006",  # hiring manager, interviewer, approver L1
    "omar": "00000000-0000-4000-8000-000000000007",  # approver L2
    "zainab": "00000000-0000-4000-8000-000000000008",  # IT admin
}
SYSTEM_CTX = {"actor_type": "SYSTEM", "actor_id": "scenario-runner", "workflow_name": "SCENARIOS"}
SETTLED_STATUSES = {"VALIDATED", "SCORED", "NEW", "VALIDATING"}


class CheckFailed(AssertionError):
    pass


@dataclass
class Candidate:
    label: str
    email: str
    full_name: str
    position: str
    correlation_id: str
    idempotency_key: str
    payload: dict[str, Any]
    application_id: str | None = None
    candidate_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class Harness:
    def __init__(self) -> None:
        keys = os.environ.get("SCENARIO_API_KEY", "")
        self.api_key = keys.split(",")[0].strip()
        if not self.api_key:
            raise SystemExit("SCENARIO_API_KEY (the stack's internal API key) must be set for the scenario runner")
        self.run = uuid.uuid4().hex[:6]
        self.http = httpx.Client(timeout=90)
        self.db = psycopg.connect(os.environ["SCENARIO_DATABASE_URL"], row_factory=dict_row, autocommit=True)
        self.started = self.one("SELECT now() AS t")["t"]
        self._seq = 0

    # ---- evidence ---------------------------------------------------------------------------------------
    def q(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        converted = tuple(Jsonb(p) if isinstance(p, dict) else p for p in params)
        cur = self.db.execute(sql, converted)
        return cur.fetchall() if cur.description else []

    def one(self, sql: str, *params: Any) -> dict[str, Any]:
        rows = self.q(sql, *params)
        return rows[0] if rows else {}

    def snapshot(self, c: Candidate) -> dict[str, Any]:
        return self.one("SELECT api.application_snapshot(%s::uuid) AS s", c.application_id)["s"] or {}

    def status(self, c: Candidate) -> str:
        return self.snapshot(c).get("status", "")

    def path(self, c: Candidate) -> list[str]:
        rows = self.q(
            "SELECT to_status FROM hiring.candidate_status_history WHERE application_id = %s ORDER BY id",
            c.application_id,
        )
        return [r["to_status"] for r in rows]

    def logs(self, c: Candidate, action: str) -> list[dict[str, Any]]:
        return self.q(
            "SELECT * FROM ops.automation_logs WHERE correlation_id = %s AND action = %s ORDER BY id",
            c.correlation_id,
            action,
        )

    def errors(self, c: Candidate) -> list[dict[str, Any]]:
        return self.q(
            "SELECT * FROM ops.automation_errors WHERE correlation_id = %s ORDER BY created_at", c.correlation_id
        )

    def actions(self, c: Candidate, action_type: str | None = None) -> list[dict[str, Any]]:
        return self.q(
            """SELECT * FROM ops.scheduled_actions WHERE application_id = %s AND (%s::text IS NULL OR action_type = %s)
               ORDER BY created_at""",
            c.application_id,
            action_type,
            action_type,
        )

    def notifications(self, c: Candidate, template: str | None = None) -> list[dict[str, Any]]:
        return self.q(
            """SELECT * FROM ops.notifications WHERE correlation_id = %s AND (%s::text IS NULL OR template_key = %s)
               ORDER BY created_at""",
            c.correlation_id,
            template,
            template,
        )

    # ---- mail ---------------------------------------------------------------------------------------------
    def emails(self, to: str, subject: str = "") -> list[dict[str, Any]]:
        response = self.http.get(f"{MAILPIT}/api/v1/search", params={"query": f"to:{to}", "limit": 200})
        response.raise_for_status()
        return [m for m in response.json()["messages"] if subject.lower() in m["Subject"].lower()]

    def email_html(self, message: dict[str, Any]) -> str:
        return str(self.http.get(f"{MAILPIT}/api/v1/message/{message['ID']}").json()["HTML"])

    def link_token(self, to: str, subject: str) -> str:
        found = self.emails(to, subject)
        if not found:
            raise CheckFailed(f"no email to {to} with subject containing {subject!r}")
        match = re.search(r"#token=([A-Za-z0-9._-]+)", self.email_html(found[0]))  # newest first
        if not match:
            raise CheckFailed(f"email {found[0]['Subject']!r} has no signed link")
        return match.group(1)

    # ---- inputs ---------------------------------------------------------------------------------------------
    def upload_cv(self, text: str) -> str:
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=A4)
        y = 800
        for line in text.splitlines() or [text]:
            pdf.drawString(50, y, line[:110])
            y -= 16
        pdf.save()
        files = {"file": ("cv.pdf", buffer.getvalue(), "application/pdf")}
        response = self.http.post(f"{BACKEND}/v1/public/cv", files=files)
        response.raise_for_status()
        return str(response.json()["cv_ref"])

    def unique_phone(self) -> str:
        self._seq += 1
        digits = int(self.run, 16) % 10_000 * 1000 + self._seq
        return f"0300{digits:07d}"[-11:]

    def make(self, label: str, first: str, last: str, position: str, **fields: Any) -> dict[str, Any]:
        """Application payload with a per-run unique identity."""
        payload: dict[str, Any] = {
            "full_name": f"{first} {last}",
            "email": f"{first}.{last}.{self.run}@example.com".lower(),
            "phone": self.unique_phone(),
            "position": position,
            "consent": True,
            "available_from": (date.today() + timedelta(days=30)).isoformat(),
        }
        payload.update(fields)
        payload["_label"] = label
        return payload

    def submit(self, payload: dict[str, Any], *, key: str | None = None, fault: str | None = None) -> Candidate:
        body = {k: v for k, v in payload.items() if not k.startswith("_")}
        key = key or f"scn-{self.run}-{uuid.uuid4().hex[:12]}"
        headers = {"Idempotency-Key": key, "Content-Type": "application/json"}
        if fault:
            headers["X-Fault-Inject"] = fault
        response = self.http.post(f"{N8N}/webhook/applications", json=body, headers=headers)
        if response.status_code not in (200, 202):
            raise CheckFailed(f"intake answered HTTP {response.status_code}: {response.text[:300]}")
        return Candidate(
            label=payload.get("_label", ""),
            email=str(body.get("email") or ""),
            full_name=str(body.get("full_name") or ""),
            position=str(body.get("position") or ""),
            correlation_id=response.json()["correlation_id"],
            idempotency_key=key,
            payload=body,
            extra={"http_status": response.status_code, "response": response.json()},
        )

    def resubmit(self, c: Candidate) -> httpx.Response:
        return self.http.post(
            f"{N8N}/webhook/applications",
            json=c.payload,
            headers={"Idempotency-Key": c.idempotency_key, "Content-Type": "application/json"},
        )

    def event(self, c: Candidate) -> dict[str, Any]:
        return self.one("SELECT * FROM ops.processed_events WHERE correlation_id = %s", c.correlation_id)

    def wait_intake(self, c: Candidate, timeout: float = 90) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            event = self.event(c)
            if event.get("status") in ("COMPLETED", "FAILED"):
                result = event.get("result") or {}
                c.application_id = result.get("application_id") or c.application_id
                c.candidate_id = result.get("candidate_id") or c.candidate_id
                return event
            time.sleep(1)
        raise CheckFailed(f"intake of {c.label} did not complete in {timeout:.0f}s")

    def portal(self, method: str, path: str, token: str, json: dict[str, Any] | None = None) -> httpx.Response:
        return self.http.request(
            method, f"{BACKEND}/v1/portal/{path}", json=json, headers={"Authorization": f"Bearer {token}"}
        )

    def staff(
        self, path: str, json: dict[str, Any] | None = None, *, who: str = "sana", method: str = "POST"
    ) -> httpx.Response:
        headers = {"X-API-Key": self.api_key, "X-Staff-Id": STAFF[who]}
        return self.http.request(method, f"{BACKEND}/v1/staff/{path}", json=json, headers=headers)

    def ops_webhook(self, path: str, json: dict[str, Any] | None = None) -> httpx.Response:
        return self.http.post(f"{N8N}/webhook/ops/{path}", json=json or {}, headers={"X-API-Key": self.api_key})

    # ---- orchestration ----------------------------------------------------------------------------------------
    def kick(self) -> None:
        with contextlib.suppress(httpx.HTTPError):  # the every-minute schedule still runs
            self.ops_webhook("kick", {"reason": "scenario runner"})

    def pending_work(self) -> int:
        row = self.one(
            """SELECT (SELECT count(*) FROM ops.scheduled_actions
                        WHERE status IN ('PENDING', 'RUNNING') AND run_at <= now()) +
                      (SELECT count(*) FROM ops.processed_events
                        WHERE status NOT IN ('COMPLETED', 'FAILED') AND first_received_at >= %s) AS n""",
            self.started,
        )
        return int(row["n"])

    def settle(self, timeout: float = 240) -> None:
        """Run the dispatcher until no due work is left (twice in a row, to catch follow-up actions)."""
        deadline = time.monotonic() + timeout
        quiet = 0
        while time.monotonic() < deadline:
            if self.pending_work() == 0:
                quiet += 1
                if quiet >= 2:
                    return
                time.sleep(1.5)
                continue
            quiet = 0
            self.kick()
            time.sleep(2.5)
        raise CheckFailed(f"work still pending after {timeout:.0f}s: {self.pending_work()} item(s)")

    # ---- time travel (scheduling data only) ---------------------------------------------------------------------
    def fast_forward(self, c: Candidate, *action_types: str) -> list[str]:
        rows = self.q(
            """UPDATE ops.scheduled_actions SET run_at = now()
                WHERE application_id = %s AND action_type = ANY(%s) AND status = 'PENDING' RETURNING action_type""",
            c.application_id,
            list(action_types),
        )
        return [r["action_type"] for r in rows]

    def expire_offer_deadline(self, offer_id: str) -> None:
        """The offer's validity window passes (sent 6 days ago, expired a minute ago)."""
        self.q(
            """UPDATE hiring.offers SET sent_at = now() - interval '6 days', expires_at = now() - interval '1 minute'
                WHERE id = %s""",
            offer_id,
        )

    def overdue_task(self, employee_id: str, task_key: str, days: int = 3) -> None:
        self.q(
            "UPDATE hiring.onboarding_tasks SET due_date = current_date - %s WHERE employee_id = %s AND task_key = %s",
            days,
            employee_id,
            task_key,
        )

    def set_rule_points(self, position: str, rule_key: str, points: int) -> None:
        """HR admin changes a scoring rule (configuration, not code): the documented SQL from configuration.md."""
        self.q(
            """UPDATE hiring.scoring_rules SET points = %s
                WHERE rule_key = %s AND job_position_id = (SELECT id FROM hiring.job_positions WHERE code = %s)""",
            points,
            rule_key,
            position,
        )

    def scoring_version(self, position: str) -> int:
        return int(
            self.one(
                """SELECT sc.version FROM hiring.scoring_configs sc
                     JOIN hiring.job_positions p ON p.id = sc.job_position_id WHERE p.code = %s""",
                position,
            )["version"]
        )

    def clear_test_directive(self, c: Candidate) -> None:
        """The operator "fixes the cause" of an injected failure before replaying it."""
        self.q("SELECT api.set_test_directive(%s, '', %s::jsonb)", c.correlation_id, SYSTEM_CTX)
