"""Hiring actions taken by people (candidates through signed links, staff through the portal).

Every write is a call to an api.* database function with the person as the actor. The database enforces the
state machine, ownership (a candidate can only act on their own application), segregation of duties and
idempotency; this module only forwards the request and shapes what people are allowed to see.
"""

from datetime import date
from typing import Any

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from app.core.db import Database
from app.core.errors import ConflictError, NotFoundError

Ctx = dict[str, Any]


class HiringRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def _one(self, sql: str, *params: Any) -> DictRow:
        row = await self._db.fetch_one(sql, tuple(Jsonb(p) if isinstance(p, dict) else p for p in params))
        if row is None:  # api functions either return a row or raise; guard anyway
            raise NotFoundError("not found")
        return row

    # ---- settings -------------------------------------------------------------------------------------
    async def settings(self, keys: list[str]) -> dict[str, Any]:
        rows = await self._db.fetch_all("SELECT key, value FROM hiring.settings WHERE key = ANY(%s)", (keys,))
        return {r["key"]: r["value"] for r in rows}

    # ---- interviews -----------------------------------------------------------------------------------
    async def interview_for_candidate(self, interview_id: str, candidate_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one(
            """SELECT i.id::text AS interview_id, i.interview_code, i.round, i.status, i.mode,
                      i.scheduled_start, i.scheduled_end, i.meeting_url, a.application_code,
                      a.status AS application_status,
                      c.id::text AS candidate_id, split_part(c.full_name, ' ', 1) AS first_name,
                      p.title AS position_title, s.full_name AS interviewer_name,
                      (SELECT sa.run_at FROM ops.scheduled_actions sa
                        WHERE sa.entity_id = i.id AND sa.action_type = 'INTERVIEW_INVITE_EXPIRY'
                          AND sa.status = 'PENDING'
                        ORDER BY sa.run_at LIMIT 1) AS respond_by
                 FROM hiring.interviews i
                 JOIN hiring.applications a ON a.id = i.application_id
                 JOIN hiring.candidates c ON c.id = a.candidate_id
                 JOIN hiring.job_positions p ON p.id = a.job_position_id
                 JOIN hiring.staff_members s ON s.id = i.interviewer_id
                WHERE i.id = %s""",
            (interview_id,),
        )
        if row is None or row["candidate_id"] != candidate_id:
            raise NotFoundError("interview not found", code="INTERVIEW_NOT_FOUND")
        view = dict(row)
        view.pop("candidate_id")
        view["slots"] = (
            [
                dict(r)
                for r in await self._db.fetch_all("SELECT * FROM api.interview_slot_options(%s, 12)", (interview_id,))
            ]
            if row["status"] == "INVITED"
            else []
        )
        return view

    async def interview_for_staff(self, interview_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one(
            """SELECT i.id::text AS interview_id, i.interview_code, i.round, i.status, i.mode, i.scheduled_start,
                      i.scheduled_end, i.meeting_url, i.interviewer_id::text AS interviewer_id,
                      s.full_name AS interviewer_name, a.application_code, c.full_name AS candidate_name,
                      p.title AS position_title, a.experience_years, a.skills, a.application_score,
                      EXISTS (SELECT 1 FROM hiring.interview_feedback f WHERE f.interview_id = i.id)
                        AS feedback_submitted
                 FROM hiring.interviews i
                 JOIN hiring.applications a ON a.id = i.application_id
                 JOIN hiring.candidates c ON c.id = a.candidate_id
                 JOIN hiring.job_positions p ON p.id = a.job_position_id
                 JOIN hiring.staff_members s ON s.id = i.interviewer_id
                WHERE i.id = %s""",
            (interview_id,),
        )
        if row is None:
            raise NotFoundError("interview not found", code="INTERVIEW_NOT_FOUND")
        return dict(row)

    async def confirm_slot(self, interview_id: str, slot_id: str, ctx: Ctx) -> DictRow:
        return await self._one("SELECT * FROM api.confirm_interview_slot(%s, %s, %s)", interview_id, slot_id, ctx)

    async def cancel_interview(self, interview_id: str, reason: str, ctx: Ctx) -> DictRow:
        return await self._one("SELECT * FROM api.cancel_interview(%s, %s, %s)", interview_id, reason, ctx)

    async def mark_no_show(self, interview_id: str, ctx: Ctx) -> DictRow:
        return await self._one("SELECT * FROM api.mark_interview_no_show(%s, %s)", interview_id, ctx)

    async def submit_feedback(self, interview_id: str, feedback: dict[str, Any], ctx: Ctx) -> DictRow:
        return await self._one("SELECT * FROM api.submit_interview_feedback(%s, %s, %s)", interview_id, feedback, ctx)

    async def evaluation_inputs(self, application_id: str) -> DictRow:
        row = await self._db.fetch_one(
            """SELECT a.id::text AS application_id, a.status, a.application_score, f.interview_score,
                      f.recommendation, f.interview_code
                 FROM hiring.applications a
                 LEFT JOIN LATERAL (
                   SELECT fb.interview_score, fb.recommendation, i.interview_code
                     FROM hiring.interviews i JOIN hiring.interview_feedback fb ON fb.interview_id = i.id
                    WHERE i.application_id = a.id
                    ORDER BY i.round DESC LIMIT 1) f ON true
                WHERE a.id = %s""",
            (application_id,),
        )
        if row is None:
            raise NotFoundError(f"application {application_id} does not exist", code="APPLICATION_NOT_FOUND")
        if row["interview_score"] is None:
            raise ConflictError("no interview feedback has been recorded yet", code="FEEDBACK_MISSING")
        return row

    # ---- offers ---------------------------------------------------------------------------------------
    async def offer_snapshot(self, offer_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one(
            """SELECT api.offer_snapshot(o.id) AS offer,
                      (o.created_at AT TIME ZONE
                         (SELECT value #>> '{}' FROM hiring.settings WHERE key = 'company.timezone'))::date AS issued_on
                 FROM hiring.offers o WHERE o.id = %s""",
            (offer_id,),
        )
        if row is None or row["offer"] is None:
            raise NotFoundError("offer not found", code="OFFER_NOT_FOUND")
        offer: dict[str, Any] = row["offer"]
        offer["issued_on"] = row["issued_on"]
        return offer

    async def offer_for_candidate(self, offer_id: str, candidate_id: str) -> dict[str, Any]:
        offer = await self.offer_snapshot(offer_id)
        if offer["application"]["candidate"]["candidate_id"] != candidate_id:
            raise NotFoundError("offer not found", code="OFFER_NOT_FOUND")
        return offer

    async def respond_to_offer(self, offer_id: str, response: str, message: str | None, ctx: Ctx) -> DictRow:
        return await self._one("SELECT * FROM api.respond_to_offer(%s, %s, %s, %s)", offer_id, response, message, ctx)

    async def decide_approval(self, offer_id: str, level: int, decision: str, reason: str | None, ctx: Ctx) -> DictRow:
        return await self._one(
            "SELECT * FROM api.decide_offer_approval(%s, %s::smallint, %s, %s, %s)",
            offer_id,
            level,
            decision,
            reason,
            ctx,
        )

    async def create_offer(self, application_id: str, terms: dict[str, Any], ctx: Ctx) -> DictRow:
        return await self._one("SELECT * FROM api.create_offer(%s, %s, %s)", application_id, terms, ctx)

    async def close_negotiation(self, offer_id: str, reason: str, ctx: Ctx) -> DictRow:
        return await self._one("SELECT * FROM api.close_negotiation(%s, %s, %s)", offer_id, reason, ctx)

    # ---- applications, onboarding, operations ---------------------------------------------------------
    async def transition(
        self, application_id: str, to_status: str, reason: str, ctx: Ctx, expected: str | None
    ) -> DictRow:
        return await self._one(
            "SELECT * FROM api.transition_application_status(%s, %s, %s, %s, %s)",
            application_id,
            to_status,
            reason,
            ctx,
            expected,
        )

    async def withdraw(self, application_id: str, reason: str, ctx: Ctx) -> DictRow:
        return await self._one("SELECT * FROM api.withdraw_application(%s, %s, %s)", application_id, reason, ctx)

    async def complete_task(self, task_id: str, ctx: Ctx) -> DictRow:
        return await self._one("SELECT * FROM api.complete_onboarding_task(%s, %s)", task_id, ctx)

    async def error_entry(self, error_id: str) -> DictRow:
        row = await self._db.fetch_one(
            "SELECT id::text AS error_id, status, replay_workflow FROM ops.automation_errors WHERE id = %s", (error_id,)
        )
        if row is None:
            raise NotFoundError("error not found", code="ERROR_NOT_FOUND")
        return row

    async def ops_overview(self) -> dict[str, Any]:
        row = await self._db.fetch_one("SELECT * FROM reporting.v_ops_overview")
        return dict(row) if row else {}

    async def queue(self, view: str, limit: int) -> list[dict[str, Any]]:
        # `view` comes from a fixed allow-list in the route, never from user input.
        return [dict(r) for r in await self._db.fetch_all(f"SELECT * FROM reporting.{view} LIMIT %s", (limit,))]  # noqa: S608

    async def staff_exists(self, staff_id: str) -> bool:
        row = await self._db.fetch_one("SELECT is_active FROM hiring.staff_members WHERE id = %s", (staff_id,))
        return bool(row and row["is_active"])

    async def daily_report_context(self) -> tuple[str, str]:
        values = await self.settings(["company.name", "company.careers_email"])
        return str(values.get("company.name") or "the company"), str(values.get("company.careers_email") or "")


def as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))
