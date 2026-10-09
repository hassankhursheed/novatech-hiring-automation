"""HiringRepository against the real database, connected as the least-privilege backend_app role.

API tests use an in-memory fake of this repository, so these tests are what proves the SQL itself: column names,
casts, and that backend_app has the privileges every query needs. Everything runs in one rolled-back transaction.
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb

from app.api.routes.staff import QUEUES
from app.core.errors import NotFoundError
from app.repositories.hiring import HiringRepository, StaffDirectory
from tests.integration.conftest import DatabaseUrls

pytestmark = pytest.mark.db

SYSTEM = {"actor_type": "SYSTEM", "actor_id": "pytest", "workflow_name": "WF-TEST"}
USMAN_INTERVIEWER = "00000000-0000-4000-8000-000000000004"
AYESHA_L1 = "00000000-0000-4000-8000-000000000003"
SANA_HR = "00000000-0000-4000-8000-000000000001"


class TransactionDatabase:
    """Database stand-in that runs every query on one connection inside a transaction."""

    def __init__(self, conn: psycopg.AsyncConnection[DictRow]) -> None:
        self.conn = conn

    async def fetch_one(self, query: str, params: Any = None) -> DictRow | None:
        cur = await self.conn.execute(query, params)
        return await cur.fetchone()

    async def fetch_all(self, query: str, params: Any = None) -> list[DictRow]:
        cur = await self.conn.execute(query, params)
        return await cur.fetchall()


@pytest.fixture
async def db(database_urls: DatabaseUrls) -> AsyncIterator[TransactionDatabase]:
    conn = await psycopg.AsyncConnection.connect(database_urls.app, row_factory=dict_row)
    try:
        yield TransactionDatabase(conn)
    finally:
        await conn.rollback()
        await conn.close()


@pytest.fixture
def repo(db: TransactionDatabase) -> HiringRepository:
    return HiringRepository(db)  # type: ignore[arg-type]


async def call(db: TransactionDatabase, sql: str, *params: Any) -> DictRow:
    row = await db.fetch_one(sql, tuple(Jsonb(p) if isinstance(p, dict) else p for p in params))
    assert row is not None
    return row


def staff(staff_id: str) -> dict[str, str]:
    return {"actor_type": "STAFF", "actor_id": staff_id}


async def shortlisted(db: TransactionDatabase) -> tuple[str, str]:
    key = uuid.uuid4().hex
    event = await call(
        db,
        "SELECT * FROM api.register_event('WEB_FORM', %s, 'APPLICATION_SUBMITTED', %s, %s)",
        f"repo-{key}",
        {"k": key},
        SYSTEM,
    )
    app = await call(
        db,
        "SELECT * FROM api.submit_application(%s, %s, %s)",
        event["event_id"],
        {
            "outcome": "VALID",
            "issues": [],
            "validator_version": "t",
            "application": {
                "full_name": "Hira Saleem",
                "email": f"hira.{key[:8]}@example.com",
                "position_code": "PY_DEV",
                "experience_years": 4,
                "skills": ["python"],
                "expected_salary": 220000,
                "available_from": "2099-01-01",
                "consent": True,
            },
        },
        SYSTEM,
    )
    app_id = str(app["application_id"])
    await call(
        db,
        "SELECT * FROM api.record_application_score(%s, %s, %s)",
        app_id,
        {
            "scoring_version": 1,
            "input_hash": key,
            "points_awarded": 90,
            "points_possible": 100,
            "score": 90,
            "route": "SHORTLIST",
            "shortlist_min_score": 80,
            "review_min_score": 60,
            "breakdown": [],
        },
        SYSTEM,
    )
    await call(
        db,
        "SELECT * FROM api.apply_screening_decision(%s, %s, %s)",
        app_id,
        {"decision": "SHORTLISTED", "reason": "score 90"},
        SYSTEM,
    )
    candidate = await call(db, "SELECT candidate_id::text FROM hiring.applications WHERE id = %s", app_id)
    return app_id, candidate["candidate_id"]


async def test_candidate_interview_offer_and_acceptance_through_the_repository(
    db: TransactionDatabase, repo: HiringRepository
) -> None:
    app_id, candidate_id = await shortlisted(db)
    candidate = {"actor_type": "CANDIDATE", "actor_id": candidate_id}
    invitation = await call(db, "SELECT * FROM api.create_interview_invitation(%s, %s)", app_id, SYSTEM)
    interview_id = str(invitation["interview_id"])

    view = await repo.interview_for_candidate(interview_id, candidate_id)
    assert view["status"] == "INVITED" and view["slots"] and view["respond_by"] is not None
    with pytest.raises(NotFoundError):
        await repo.interview_for_candidate(interview_id, str(uuid.uuid4()))

    meeting = {"mode": "ONLINE", "meeting_url": "https://zoom.us/j/9988776655", "meeting_passcode": "nt2026"}
    assert (await repo.set_meeting(app_id, meeting, staff(SANA_HR)))["candidate_notified"] is False

    confirmed = await repo.confirm_slot(interview_id, str(view["slots"][0]["slot_id"]), candidate)
    assert confirmed["changed"] is True
    booked = await repo.interview_for_candidate(interview_id, candidate_id)
    assert booked["status"] == "CONFIRMED" and booked["slots"] == []
    private = {"scheduled_start", "meeting_url", "meeting_id", "meeting_passcode", "meeting_notes", "interviewer_name"}
    assert not private & set(booked)  # time and meeting details only ever go by email
    assert (await repo.interview_for_staff(interview_id))["feedback_submitted"] is False

    feedback = {
        "technical_skills": 5,
        "communication": 4,
        "problem_solving": 4,
        "experience": 4,
        "team_fit": 4,
        "recommendation": "HIRE",
        "comments": "Strong.",
    }
    assert (
        float((await repo.submit_feedback(interview_id, feedback, staff(USMAN_INTERVIEWER)))["interview_score"]) == 84
    )
    inputs = await repo.evaluation_inputs(app_id)
    assert float(inputs["interview_score"]) == 84 and inputs["recommendation"] == "HIRE"
    assert inputs["ai_enabled"] is True and inputs["ai_status"] is None  # no AI assessment yet

    context, company = await repo.interview_assessment_context(app_id)
    assert context.interview_id == interview_id and context.technical_skills == 5 and company
    assert context.interviewer_recommendation == "HIRE" and context.comments == "Strong."
    recorded = await call(
        db,
        "SELECT * FROM api.record_interview_assessment(%s, %s, %s)",
        interview_id,
        {
            "status": "COMPLETED",
            "prompt_version": "interview-assessment/v1",
            "input_hash": uuid.uuid4().hex,
            "provider": "stub",
            "model": "stub-deterministic-1",
            "attempts": 1,
            "latency_ms": 5,
            "assessment": {
                "recommendation": "SELECT",
                "evidence_alignment": "ALIGNED",
                "strengths": ["technical skills rated 5/5"],
                "concerns": [],
                "summary": "Strong interview with consistent ratings.",
            },
        },
        SYSTEM,
    )
    assert recorded["status"] == "COMPLETED" and recorded["replayed"] is False
    inputs = await repo.evaluation_inputs(app_id)
    assert inputs["ai_status"] == "COMPLETED" and inputs["ai_recommendation"] == "SELECT"

    await call(
        db,
        "SELECT * FROM api.apply_interview_decision(%s, %s, %s)",
        app_id,
        {"decision": "SELECTED", "reason": "final score 87.3", "final_score": 87.3},
        SYSTEM,
    )
    offer = await repo.create_offer(app_id, {}, SYSTEM)
    offer_id = str(offer["offer_id"])
    snapshot = await repo.offer_snapshot(offer_id)
    assert snapshot["status"] == "PENDING_APPROVAL" and snapshot["issued_on"] is not None
    decided = await repo.decide_approval(offer_id, 1, "APPROVED", None, staff(AYESHA_L1))
    assert decided["offer_status"] == "APPROVED"

    await call(db, "SELECT * FROM api.mark_offer_sent(%s, %s, %s)", offer_id, "offers/2026/x-r1.pdf", SYSTEM)
    assert (await repo.offer_for_candidate(offer_id, candidate_id))["status"] == "SENT"
    with pytest.raises(NotFoundError):
        await repo.offer_for_candidate(offer_id, str(uuid.uuid4()))
    accepted = await repo.respond_to_offer(offer_id, "ACCEPT", "Happy to join", candidate)
    assert accepted["application_status"] == "ACCEPTED"


async def test_staff_actions_and_read_models(db: TransactionDatabase, repo: HiringRepository) -> None:
    app_id, _ = await shortlisted(db)
    moved = await repo.transition(app_id, "REJECTED", "position filled", staff(SANA_HR), "SHORTLISTED")
    assert moved["to_status"] == "REJECTED"

    assert await repo.staff_exists(SANA_HR) and not await repo.staff_exists(str(uuid.uuid4()))
    assert await repo.ops_overview()  # one row of operational counters
    for view in QUEUES.values():  # every queue view is readable by backend_app
        assert isinstance(await repo.queue(view, 5), list)
    company, careers = await repo.daily_report_context()
    assert company and "@" in careers
    with pytest.raises(NotFoundError):
        await repo.error_entry(str(uuid.uuid4()))


async def test_portal_read_models_and_single_use_links(db: TransactionDatabase, repo: HiringRepository) -> None:
    directory = StaffDirectory(db)  # type: ignore[arg-type]
    app_id, _ = await shortlisted(db)
    await call(db, "SELECT * FROM api.create_interview_invitation(%s, %s)", app_id, SYSTEM)

    detail = await directory.application_detail(app_id, SANA_HR)
    assert detail["status"] == "SHORTLISTED" and detail["history"] and detail["scores"]
    assert detail["permissions"] == {"can_manage": True, "can_manage_interviews": True, "is_hr_admin": True}
    assert detail["meeting_plan"] is None
    interviewer_view = await directory.application_detail(app_id, USMAN_INTERVIEWER)
    assert interviewer_view["permissions"]["can_manage"] is False
    assert detail["interviews"] and detail["timeline"] and isinstance(detail["staff_transitions"], list)
    assert detail["interview_assessments"] == []  # read by backend_app; empty until a scorecard is assessed
    assert any(t["to_status"] == "REJECTED" for t in detail["staff_transitions"])
    listed = await directory.applications("SHORTLISTED", detail["application_code"], 10, 0)
    assert [r["application_id"] for r in listed] == [app_id]
    assert isinstance(await directory.onboarding_board(), list)

    staff = await directory.by_email("SANA.MALIK@novatech.example")
    assert staff is not None and staff["staff_id"] == SANA_HR
    assert (await directory.profile(SANA_HR) or {}).get("full_name") == "Sana Malik"
    assert any(s["email"] == "sana.malik@novatech.example" for s in await directory.active_staff())
    token_id = uuid.uuid4().hex
    ctx = {"actor_type": "STAFF", "actor_id": SANA_HR, "workflow_name": "PORTAL"}
    assert await directory.consume_link(token_id, "STAFF_LOGIN", SANA_HR, "2099-01-01T00:00:00Z", ctx)
    assert not await directory.consume_link(token_id, "STAFF_LOGIN", SANA_HR, "2099-01-01T00:00:00Z", ctx)
