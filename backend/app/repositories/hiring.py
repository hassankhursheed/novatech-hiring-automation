"""Hiring actions taken by people (candidates through signed links, staff through the portal).

Every write is a call to an api.* database function with the person as the actor. The database enforces the
state machine, ownership (a candidate can only act on their own application), segregation of duties and
idempotency; this module only forwards the request and shapes what people are allowed to see.
"""

from datetime import date
from typing import Any

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from app.ai.interview_assessor import InterviewContext
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
        """Latest scorecard of the application, with the AI assessment of exactly that scorecard (if any)."""
        row = await self._db.fetch_one(
            """SELECT a.id::text AS application_id, a.status, a.application_score, f.interview_score,
                      f.recommendation, f.interview_code, f.interview_id::text AS interview_id,
                      ia.status AS ai_status, ia.recommendation AS ai_recommendation,
                      ia.evidence_alignment AS ai_evidence_alignment, ia.fallback_reason AS ai_fallback_reason,
                      coalesce((SELECT value::text::boolean FROM hiring.settings
                                 WHERE key = 'evaluation.ai_enabled'), false) AS ai_enabled
                 FROM hiring.applications a
                 LEFT JOIN LATERAL (
                   SELECT fb.interview_score, fb.recommendation, i.interview_code, i.id AS interview_id
                     FROM hiring.interviews i JOIN hiring.interview_feedback fb ON fb.interview_id = i.id
                    WHERE i.application_id = a.id
                    ORDER BY i.round DESC LIMIT 1) f ON true
                 LEFT JOIN LATERAL (
                   SELECT x.status, x.recommendation, x.evidence_alignment, x.fallback_reason
                     FROM hiring.interview_assessments x
                    WHERE x.interview_id = f.interview_id
                    ORDER BY (x.status = 'COMPLETED') DESC, x.created_at DESC LIMIT 1) ia ON true
                WHERE a.id = %s""",
            (application_id,),
        )
        if row is None:
            raise NotFoundError(f"application {application_id} does not exist", code="APPLICATION_NOT_FOUND")
        if row["interview_score"] is None:
            raise ConflictError("no interview feedback has been recorded yet", code="FEEDBACK_MISSING")
        return row

    async def interview_assessment_context(self, application_id: str) -> tuple[InterviewContext, str]:
        """What the AI reads about the latest scorecard: position, screening results and the scorecard itself."""
        row = await self._db.fetch_one(
            """SELECT a.id::text AS application_id, i.id::text AS interview_id, c.full_name, s.full_name AS interviewer,
                      p.title, p.department, p.description, a.application_score,
                      fb.technical_skills, fb.communication, fb.problem_solving, fb.experience, fb.team_fit,
                      fb.interview_score, fb.recommendation, fb.comments,
                      ai.summary AS screening_summary, ai.missing_skills,
                      (SELECT value #>> '{}' FROM hiring.settings WHERE key = 'company.name') AS company
                 FROM hiring.applications a
                 JOIN hiring.candidates c ON c.id = a.candidate_id
                 JOIN hiring.job_positions p ON p.id = a.job_position_id
                 JOIN LATERAL (
                   SELECT i.* FROM hiring.interviews i
                    WHERE i.application_id = a.id
                      AND EXISTS (SELECT 1 FROM hiring.interview_feedback x WHERE x.interview_id = i.id)
                    ORDER BY i.round DESC LIMIT 1) i ON true
                 JOIN hiring.interview_feedback fb ON fb.interview_id = i.id
                 JOIN hiring.staff_members s ON s.id = fb.interviewer_id
                 LEFT JOIN LATERAL (
                   SELECT x.summary, x.missing_skills FROM hiring.ai_analyses x
                    WHERE x.application_id = a.id AND x.status = 'COMPLETED'
                    ORDER BY x.created_at DESC LIMIT 1) ai ON true
                WHERE a.id = %s""",
            (application_id,),
        )
        if row is None:
            raise ConflictError("no interview feedback has been recorded yet", code="FEEDBACK_MISSING")
        ctx = InterviewContext(
            application_id=row["application_id"],
            interview_id=row["interview_id"],
            candidate_name=row["full_name"],
            interviewer_name=row["interviewer"],
            position_title=row["title"],
            department=row["department"],
            position_description=row["description"],
            screening_score=float(row["application_score"]) if row["application_score"] is not None else None,
            screening_summary=row["screening_summary"],
            missing_skills=list(row["missing_skills"] or []),
            technical_skills=row["technical_skills"],
            communication=row["communication"],
            problem_solving=row["problem_solving"],
            experience=row["experience"],
            team_fit=row["team_fit"],
            interview_score=float(row["interview_score"]),
            interviewer_recommendation=row["recommendation"],
            comments=row["comments"],
        )
        return ctx, str(row["company"] or "NovaTech")

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


class StaffDirectory:
    """Staff identity and the HR portal's read models (lists, detail pages, boards)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def by_email(self, email: str) -> DictRow | None:
        return await self._db.fetch_one(
            """SELECT id::text AS staff_id, full_name, email, roles, job_title, is_active
                 FROM hiring.staff_members WHERE lower(email) = lower(%s)""",
            (email.strip(),),
        )

    async def profile(self, staff_id: str) -> DictRow | None:
        return await self._db.fetch_one(
            """SELECT id::text AS staff_id, full_name, email, roles, job_title, department, is_active
                 FROM hiring.staff_members WHERE id = %s""",
            (staff_id,),
        )

    async def active_staff(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """SELECT full_name, email, roles, job_title FROM hiring.staff_members
                WHERE is_active ORDER BY array_position(ARRAY['HR_ADMIN', 'RECRUITER', 'APPROVER_L2', 'HIRING_MANAGER',
                      'INTERVIEWER', 'IT_ADMIN'], roles[1]), full_name"""
        )
        return [dict(r) for r in rows]

    async def consume_link(self, token_id: str, purpose: str, subject_id: str, expires_at: Any, ctx: Ctx) -> bool:
        row = await self._db.fetch_one(
            "SELECT api.consume_link_token(%s, %s, %s, %s, %s) AS ok",
            (token_id, purpose, subject_id, expires_at, Jsonb(ctx)),
        )
        return bool(row and row["ok"])

    async def applications(
        self, status: str | None, search: str | None, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        pattern = f"%{search.strip()}%" if search and search.strip() else None
        rows = await self._db.fetch_all(
            """SELECT a.id::text AS application_id, a.application_code, a.status, a.status_changed_at, a.created_at,
                      a.application_score, a.interview_score, a.final_score, a.ai_recommendation, a.review_reason,
                      c.full_name, c.email, p.code AS position_code, p.title AS position_title,
                      s.stage, s.awaits_human
                 FROM hiring.applications a
                 JOIN hiring.candidates c ON c.id = a.candidate_id
                 JOIN hiring.job_positions p ON p.id = a.job_position_id
                 JOIN hiring.application_statuses s ON s.code = a.status
                WHERE (%(status)s::text IS NULL OR a.status = %(status)s)
                  AND (%(pattern)s::text IS NULL OR c.full_name ILIKE %(pattern)s OR c.email ILIKE %(pattern)s
                       OR a.application_code ILIKE %(pattern)s)
                ORDER BY a.status_changed_at DESC
                LIMIT %(limit)s OFFSET %(offset)s""",
            {"status": status, "pattern": pattern, "limit": limit, "offset": offset},
        )
        return [dict(r) for r in rows]

    async def application_detail(self, application_id: str) -> dict[str, Any]:
        snapshot = await self._db.fetch_one("SELECT api.application_snapshot(%s::uuid) AS s", (application_id,))
        if snapshot is None or snapshot["s"] is None:
            raise NotFoundError("application not found", code="APPLICATION_NOT_FOUND")
        app: dict[str, Any] = snapshot["s"]
        many = self._db.fetch_all
        app["history"] = [
            dict(r)
            for r in await many(
                """SELECT from_status, to_status, reason, actor_type, actor_id, workflow_name, changed_at
                 FROM hiring.candidate_status_history WHERE application_id = %s ORDER BY id""",
                (application_id,),
            )
        ]
        app["scores"] = [
            dict(r)
            for r in await many(
                """SELECT scoring_version, score, route, points_awarded, points_possible, breakdown, created_at
                 FROM hiring.application_scores WHERE application_id = %s ORDER BY created_at DESC""",
                (application_id,),
            )
        ]
        app["ai_analyses"] = [
            dict(r)
            for r in await many(
                """SELECT status, provider, model, prompt_version, technical_strength, experience_relevance,
                      communication_indication, missing_skills, summary, recommendation, fallback_reason, attempts,
                      created_at
                 FROM hiring.ai_analyses WHERE application_id = %s ORDER BY created_at DESC""",
                (application_id,),
            )
        ]
        app["interviews"] = [
            r["s"]
            for r in await many(
                """SELECT api.interview_snapshot(i.id) - 'application' AS s FROM hiring.interviews i
                WHERE i.application_id = %s ORDER BY i.round""",
                (application_id,),
            )
        ]
        app["feedback"] = [
            dict(r)
            for r in await many(
                """SELECT i.interview_code, f.technical_skills, f.communication, f.problem_solving, f.experience,
                          f.team_fit,
                      f.interview_score, f.recommendation, f.comments, f.submitted_at
                 FROM hiring.interview_feedback f JOIN hiring.interviews i ON i.id = f.interview_id
                WHERE i.application_id = %s ORDER BY f.submitted_at""",
                (application_id,),
            )
        ]
        app["interview_assessments"] = [
            dict(r)
            for r in await many(
                """SELECT i.interview_code, x.status, x.provider, x.model, x.prompt_version, x.recommendation,
                      x.evidence_alignment, x.strengths, x.concerns, x.summary, x.fallback_reason, x.attempts,
                      x.created_at
                 FROM hiring.interview_assessments x JOIN hiring.interviews i ON i.id = x.interview_id
                WHERE x.application_id = %s ORDER BY x.created_at DESC""",
                (application_id,),
            )
        ]
        app["offers"] = [
            r["s"]
            for r in await many(
                """SELECT api.offer_snapshot(o.id) - 'application' AS s FROM hiring.offers o
                WHERE o.application_id = %s ORDER BY o.revision""",
                (application_id,),
            )
        ]
        employee = await self._db.fetch_one(
            "SELECT api.employee_snapshot(e.id) AS s FROM hiring.employees e WHERE e.application_id = %s",
            (application_id,),
        )
        app["employee"] = employee["s"] if employee else None
        app["notifications"] = [
            dict(r)
            for r in await many(
                """SELECT template_key, recipient, status, channel, created_at, sent_at
                 FROM ops.notifications WHERE application_id = %s ORDER BY created_at""",
                (application_id,),
            )
        ]
        app["staff_transitions"] = [
            dict(r)
            for r in await many(
                """SELECT t.to_status, t.description FROM hiring.status_transitions t
                WHERE t.from_status = %s AND 'STAFF' = ANY (t.allowed_actor_types) AND t.managed_by IS NULL
                ORDER BY t.to_status""",
                (app["status"],),
            )
        ]
        app["timeline"] = [
            dict(r)
            for r in await many(
                """SELECT occurred_at, source, workflow_name, action, outcome, from_status, to_status, actor,
                          error_code,
                      error_message
                 FROM reporting.trace(%s)""",
                (app["correlation_id"],),
            )
        ]
        return app

    async def onboarding_board(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """SELECT api.employee_snapshot(e.id) AS s FROM hiring.employees e
                WHERE e.status = 'ONBOARDING' ORDER BY e.joining_date, e.employee_code"""
        )
        return [r["s"] for r in rows]


def as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))
