"""End-to-end lifecycle of an application through the database write API (interviews, offers, onboarding).

Every test runs in a rolled-back transaction. Staff ids come from db/seed/010_novatech_config.sql.
"""

import json
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.integration.conftest import DatabaseUrls

pytestmark = pytest.mark.db

SYSTEM = {"actor_type": "SYSTEM", "actor_id": "pytest", "workflow_name": "WF-TEST"}
SANA_HR = "00000000-0000-4000-8000-000000000001"
BILAL_RECRUITER = "00000000-0000-4000-8000-000000000002"
AYESHA_L1 = "00000000-0000-4000-8000-000000000003"
USMAN_INTERVIEWER = "00000000-0000-4000-8000-000000000004"
OMAR_L2 = "00000000-0000-4000-8000-000000000007"

Conn = psycopg.Connection[dict[str, Any]]


def staff(staff_id: str) -> dict[str, str]:
    return {"actor_type": "STAFF", "actor_id": staff_id}


@pytest.fixture
def db(database_urls: DatabaseUrls) -> Iterator[Conn]:
    # Owner connection: lets tests move clocks (e.g. expire an offer) inside a rolled-back transaction.
    with psycopg.connect(database_urls.owner, row_factory=dict_row) as conn:
        yield conn
        conn.rollback()


def call(conn: Conn, sql: str, *params: Any) -> list[dict[str, Any]]:
    converted = [json.dumps(p) if isinstance(p, dict | list) else p for p in params]
    return conn.execute(sql, converted).fetchall()


def one(conn: Conn, sql: str, *params: Any) -> dict[str, Any]:
    return call(conn, sql, *params)[0]


def status_of(conn: Conn, app_id: Any) -> str:
    return one(conn, "SELECT status FROM hiring.applications WHERE id = %s", app_id)["status"]


def shortlisted_application(conn: Conn, salary: int = 220000) -> dict[str, Any]:
    key = uuid.uuid4().hex
    event = one(
        conn,
        "SELECT * FROM api.register_event('WEB_FORM', %s, 'APPLICATION_SUBMITTED', %s::jsonb, %s::jsonb)",
        f"life-{key}",
        {"k": key},
        SYSTEM,
    )
    app = one(
        conn,
        "SELECT * FROM api.submit_application(%s, %s::jsonb, %s::jsonb)",
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
                "expected_salary": salary,
                "available_from": "2099-01-01",
                "consent": True,
            },
        },
        SYSTEM,
    )
    call(
        conn,
        "SELECT * FROM api.record_application_score(%s, %s::jsonb, %s::jsonb)",
        app["application_id"],
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
    call(
        conn,
        "SELECT * FROM api.apply_screening_decision(%s, %s::jsonb, %s::jsonb)",
        app["application_id"],
        {"decision": "SHORTLISTED", "reason": "score 90"},
        SYSTEM,
    )
    return app


def candidate_ctx(conn: Conn, app_id: Any) -> dict[str, str]:
    cid = one(conn, "SELECT candidate_id FROM hiring.applications WHERE id = %s", app_id)["candidate_id"]
    return {"actor_type": "CANDIDATE", "actor_id": str(cid)}


def interviewed_and_selected(conn: Conn, salary: int = 220000) -> dict[str, Any]:
    app = shortlisted_application(conn, salary)
    inv = one(conn, "SELECT * FROM api.create_interview_invitation(%s, %s::jsonb)", app["application_id"], SYSTEM)
    slot = one(conn, "SELECT * FROM api.interview_slot_options(%s, 1)", inv["interview_id"])
    call(
        conn,
        "SELECT * FROM api.confirm_interview_slot(%s, %s, %s::jsonb)",
        inv["interview_id"],
        slot["slot_id"],
        candidate_ctx(conn, app["application_id"]),
    )
    call(
        conn,
        "SELECT * FROM api.submit_interview_feedback(%s, %s::jsonb, %s::jsonb)",
        inv["interview_id"],
        {
            "technical_skills": 5,
            "communication": 4,
            "problem_solving": 5,
            "experience": 4,
            "team_fit": 4,
            "recommendation": "HIRE",
            "comments": "Strong backend fundamentals.",
        },
        staff(USMAN_INTERVIEWER),
    )
    call(
        conn,
        "SELECT * FROM api.apply_interview_decision(%s, %s::jsonb, %s::jsonb)",
        app["application_id"],
        {"decision": "SELECTED", "reason": "final score 87", "final_score": 87},
        SYSTEM,
    )
    return {**app, "interview_id": inv["interview_id"], "slot_id": slot["slot_id"]}


def offered(conn: Conn, salary: int = 220000) -> dict[str, Any]:
    app = interviewed_and_selected(conn, salary)
    offer = one(conn, "SELECT * FROM api.create_offer(%s, '{}'::jsonb, %s::jsonb)", app["application_id"], SYSTEM)
    call(
        conn,
        "SELECT * FROM api.decide_offer_approval(%s, 1::smallint, 'APPROVED', NULL, %s::jsonb)",
        offer["offer_id"],
        staff(AYESHA_L1),
    )
    if offer["required_approval_levels"] == 2:
        call(
            conn,
            "SELECT * FROM api.decide_offer_approval(%s, 2::smallint, 'APPROVED', NULL, %s::jsonb)",
            offer["offer_id"],
            staff(OMAR_L2),
        )
    call(conn, "SELECT * FROM api.mark_offer_sent(%s, 'offers/test.pdf', %s::jsonb)", offer["offer_id"], SYSTEM)
    return {**app, "offer_id": offer["offer_id"]}


def actions(conn: Conn, entity_id: Any) -> dict[str, str]:
    rows = call(conn, "SELECT action_type, status FROM ops.scheduled_actions WHERE entity_id = %s", entity_id)
    return {r["action_type"]: r["status"] for r in rows}


# ---- interviews ------------------------------------------------------------------------------------


def test_invitation_is_idempotent_and_schedules_timers(db: Conn) -> None:
    app = shortlisted_application(db)
    first = one(db, "SELECT * FROM api.create_interview_invitation(%s, %s::jsonb)", app["application_id"], SYSTEM)
    again = one(db, "SELECT * FROM api.create_interview_invitation(%s, %s::jsonb)", app["application_id"], SYSTEM)
    assert first["created"] is True and again["created"] is False
    assert first["interview_id"] == again["interview_id"] and first["interviewer_id"] == uuid.UUID(USMAN_INTERVIEWER)
    assert actions(db, first["interview_id"]) == {
        "INTERVIEW_INVITE_REMINDER": "PENDING",
        "INTERVIEW_INVITE_EXPIRY": "PENDING",
    }


def test_confirmation_books_slot_cancels_reminders_and_schedules_feedback_timers(db: Conn) -> None:
    app = shortlisted_application(db)
    inv = one(db, "SELECT * FROM api.create_interview_invitation(%s, %s::jsonb)", app["application_id"], SYSTEM)
    slot = one(db, "SELECT * FROM api.interview_slot_options(%s, 1)", inv["interview_id"])
    ctx = candidate_ctx(db, app["application_id"])
    confirmed = one(
        db, "SELECT * FROM api.confirm_interview_slot(%s, %s, %s::jsonb)", inv["interview_id"], slot["slot_id"], ctx
    )
    replay = one(
        db, "SELECT * FROM api.confirm_interview_slot(%s, %s, %s::jsonb)", inv["interview_id"], slot["slot_id"], ctx
    )
    assert confirmed["changed"] is True and replay["changed"] is False
    assert status_of(db, app["application_id"]) == "INTERVIEW_SCHEDULED"
    assert actions(db, inv["interview_id"]) == {
        "INTERVIEW_INVITE_REMINDER": "CANCELLED",
        "INTERVIEW_INVITE_EXPIRY": "CANCELLED",
        "FEEDBACK_REMINDER": "PENDING",
        "FEEDBACK_ESCALATION": "PENDING",
    }


def test_slot_cannot_be_double_booked_and_links_are_personal(db: Conn) -> None:
    first = shortlisted_application(db)
    second = shortlisted_application(db)
    inv1 = one(db, "SELECT * FROM api.create_interview_invitation(%s, %s::jsonb)", first["application_id"], SYSTEM)
    inv2 = one(db, "SELECT * FROM api.create_interview_invitation(%s, %s::jsonb)", second["application_id"], SYSTEM)
    slot = one(db, "SELECT * FROM api.interview_slot_options(%s, 1)", inv1["interview_id"])

    with pytest.raises(psycopg.Error) as wrong_person:
        call(
            db,
            "SELECT * FROM api.confirm_interview_slot(%s, %s, %s::jsonb)",
            inv1["interview_id"],
            slot["slot_id"],
            candidate_ctx(db, second["application_id"]),
        )
    assert "NOT_YOUR_APPLICATION" in str(wrong_person.value)
    db.rollback()

    first = shortlisted_application(db)
    second = shortlisted_application(db)
    inv1 = one(db, "SELECT * FROM api.create_interview_invitation(%s, %s::jsonb)", first["application_id"], SYSTEM)
    inv2 = one(db, "SELECT * FROM api.create_interview_invitation(%s, %s::jsonb)", second["application_id"], SYSTEM)
    slot = one(db, "SELECT * FROM api.interview_slot_options(%s, 1)", inv1["interview_id"])
    call(
        db,
        "SELECT * FROM api.confirm_interview_slot(%s, %s, %s::jsonb)",
        inv1["interview_id"],
        slot["slot_id"],
        candidate_ctx(db, first["application_id"]),
    )
    with pytest.raises(psycopg.Error) as taken:
        call(
            db,
            "SELECT * FROM api.confirm_interview_slot(%s, %s, %s::jsonb)",
            inv2["interview_id"],
            slot["slot_id"],
            candidate_ctx(db, second["application_id"]),
        )
    assert "SLOT_UNAVAILABLE" in str(taken.value)


def test_unconfirmed_invitation_expires_to_review(db: Conn) -> None:
    app = shortlisted_application(db)
    inv = one(db, "SELECT * FROM api.create_interview_invitation(%s, %s::jsonb)", app["application_id"], SYSTEM)
    result = one(db, "SELECT * FROM api.expire_interview_invitation(%s, %s::jsonb)", inv["interview_id"], SYSTEM)
    assert result["status"] == "EXPIRED" and status_of(db, app["application_id"]) == "SCREENING_REVIEW"


def test_only_the_assigned_interviewer_submits_feedback_once(db: Conn) -> None:
    app = shortlisted_application(db)
    inv = one(db, "SELECT * FROM api.create_interview_invitation(%s, %s::jsonb)", app["application_id"], SYSTEM)
    slot = one(db, "SELECT * FROM api.interview_slot_options(%s, 1)", inv["interview_id"])
    call(
        db,
        "SELECT * FROM api.confirm_interview_slot(%s, %s, %s::jsonb)",
        inv["interview_id"],
        slot["slot_id"],
        candidate_ctx(db, app["application_id"]),
    )
    feedback = {
        "technical_skills": 2,
        "communication": 2,
        "problem_solving": 1,
        "experience": 2,
        "team_fit": 3,
        "recommendation": "NO_HIRE",
        "comments": "Struggled with basics.",
    }
    with pytest.raises(psycopg.Error) as wrong:
        call(
            db,
            "SELECT * FROM api.submit_interview_feedback(%s, %s::jsonb, %s::jsonb)",
            inv["interview_id"],
            feedback,
            staff(OMAR_L2),
        )
    assert "NOT_ASSIGNED_INTERVIEWER" in str(wrong.value)
    db.rollback()


def test_feedback_moves_to_interviewed_and_replay_is_harmless(db: Conn) -> None:
    app = shortlisted_application(db)
    inv = one(db, "SELECT * FROM api.create_interview_invitation(%s, %s::jsonb)", app["application_id"], SYSTEM)
    slot = one(db, "SELECT * FROM api.interview_slot_options(%s, 1)", inv["interview_id"])
    call(
        db,
        "SELECT * FROM api.confirm_interview_slot(%s, %s, %s::jsonb)",
        inv["interview_id"],
        slot["slot_id"],
        candidate_ctx(db, app["application_id"]),
    )
    feedback = {
        "technical_skills": 2,
        "communication": 2,
        "problem_solving": 1,
        "experience": 2,
        "team_fit": 3,
        "recommendation": "NO_HIRE",
        "comments": "Struggled with basics.",
    }
    first = one(
        db,
        "SELECT * FROM api.submit_interview_feedback(%s, %s::jsonb, %s::jsonb)",
        inv["interview_id"],
        feedback,
        staff(USMAN_INTERVIEWER),
    )
    again = one(
        db,
        "SELECT * FROM api.submit_interview_feedback(%s, %s::jsonb, %s::jsonb)",
        inv["interview_id"],
        feedback,
        staff(USMAN_INTERVIEWER),
    )
    assert float(first["interview_score"]) == 40.0 and again["replayed"] is True
    assert status_of(db, app["application_id"]) == "INTERVIEWED"
    assert actions(db, inv["interview_id"])["FEEDBACK_REMINDER"] == "CANCELLED"
    call(
        db,
        "SELECT * FROM api.apply_interview_decision(%s, %s::jsonb, %s::jsonb)",
        app["application_id"],
        {"decision": "REJECTED", "reason": "final score 47"},
        SYSTEM,
    )
    assert status_of(db, app["application_id"]) == "REJECTED"


# ---- offers ----------------------------------------------------------------------------------------


def test_standard_offer_single_approval_send_accept_onboard(db: Conn) -> None:
    app = interviewed_and_selected(db, salary=220000)
    offer = one(db, "SELECT * FROM api.create_offer(%s, '{}'::jsonb, %s::jsonb)", app["application_id"], SYSTEM)
    again = one(db, "SELECT * FROM api.create_offer(%s, '{}'::jsonb, %s::jsonb)", app["application_id"], SYSTEM)
    assert offer["required_approval_levels"] == 1 and again["created"] is False
    assert status_of(db, app["application_id"]) == "OFFER_PENDING_APPROVAL"

    with pytest.raises(psycopg.Error) as not_approver:
        call(
            db,
            "SELECT * FROM api.decide_offer_approval(%s, 1::smallint, 'APPROVED', NULL, %s::jsonb)",
            offer["offer_id"],
            staff(BILAL_RECRUITER),
        )
    assert "NOT_AN_APPROVER" in str(not_approver.value)
    db.rollback()


def test_full_journey_to_onboarded_employee_exactly_once(db: Conn) -> None:
    app = offered(db, salary=220000)
    assert status_of(db, app["application_id"]) == "OFFERED"
    assert set(actions(db, app["offer_id"])) >= {"OFFER_REMINDER", "OFFER_FINAL_REMINDER", "OFFER_EXPIRY"}

    ctx = candidate_ctx(db, app["application_id"])
    one(db, "SELECT * FROM api.respond_to_offer(%s, 'ACCEPT', 'Happy to join!', %s::jsonb)", app["offer_id"], ctx)
    assert status_of(db, app["application_id"]) == "ACCEPTED"
    assert actions(db, app["offer_id"])["OFFER_REMINDER"] == "CANCELLED"

    emp = one(db, "SELECT * FROM api.create_employee_from_offer(%s, %s::jsonb)", app["offer_id"], SYSTEM)
    again = one(db, "SELECT * FROM api.create_employee_from_offer(%s, %s::jsonb)", app["offer_id"], SYSTEM)
    assert emp["created"] is True and again["created"] is False and emp["employee_id"] == again["employee_id"]
    assert emp["employee_code"].startswith("NT-") and emp["company_email"].startswith("hira.saleem")
    assert emp["task_count"] == 10 and status_of(db, app["application_id"]) == "ONBOARDING"

    call(db, "SELECT api.mark_account_provisioned(%s, %s::jsonb)", emp["employee_id"], SYSTEM)
    tasks = call(
        db, "SELECT id FROM hiring.onboarding_tasks WHERE employee_id = %s AND status = 'PENDING'", emp["employee_id"]
    )
    assert len(tasks) == 9  # create_accounts completed by the simulated provisioning
    for task in tasks:
        call(db, "SELECT * FROM api.complete_onboarding_task(%s, %s::jsonb)", task["id"], staff(SANA_HR))
    assert status_of(db, app["application_id"]) == "ONBOARDED"


def test_high_salary_requires_two_distinct_approvers(db: Conn) -> None:
    app = interviewed_and_selected(db, salary=320000)
    offer = one(db, "SELECT * FROM api.create_offer(%s, '{}'::jsonb, %s::jsonb)", app["application_id"], SYSTEM)
    assert offer["required_approval_levels"] == 2 and offer["monthly_salary"] == 320000
    level1 = one(
        db,
        "SELECT * FROM api.decide_offer_approval(%s, 1::smallint, 'APPROVED', NULL, %s::jsonb)",
        offer["offer_id"],
        staff(AYESHA_L1),
    )
    assert level1["offer_status"] == "PENDING_APPROVAL" and level1["next_level"] == 2
    assert actions(db, offer["offer_id"])["REQUEST_OFFER_APPROVAL"] == "PENDING"
    level2 = one(
        db,
        "SELECT * FROM api.decide_offer_approval(%s, 2::smallint, 'APPROVED', NULL, %s::jsonb)",
        offer["offer_id"],
        staff(OMAR_L2),
    )
    assert level2["offer_status"] == "APPROVED" and actions(db, offer["offer_id"])["SEND_OFFER"] == "PENDING"


def test_level_two_cannot_decide_before_level_one(db: Conn) -> None:
    app = interviewed_and_selected(db, salary=320000)
    offer = one(db, "SELECT * FROM api.create_offer(%s, '{}'::jsonb, %s::jsonb)", app["application_id"], SYSTEM)
    with pytest.raises(psycopg.Error) as exc:
        call(
            db,
            "SELECT * FROM api.decide_offer_approval(%s, 2::smallint, 'APPROVED', NULL, %s::jsonb)",
            offer["offer_id"],
            staff(OMAR_L2),
        )
    assert "LEVEL_1_REQUIRED" in str(exc.value)


def test_approver_rejection_stops_send_path_and_blocks_auto_redraft(db: Conn) -> None:
    app = interviewed_and_selected(db)
    offer = one(db, "SELECT * FROM api.create_offer(%s, '{}'::jsonb, %s::jsonb)", app["application_id"], SYSTEM)
    rejected = one(
        db,
        "SELECT * FROM api.decide_offer_approval(%s, 1::smallint, 'REJECTED', %s, %s::jsonb)",
        offer["offer_id"],
        "salary above budget for this team",
        staff(AYESHA_L1),
    )
    assert rejected["offer_status"] == "REJECTED_BY_APPROVER"
    assert status_of(db, app["application_id"]) == "SELECTED"
    assert "SEND_OFFER" not in actions(db, offer["offer_id"])
    redraft = one(db, "SELECT * FROM api.create_offer(%s, '{}'::jsonb, %s::jsonb)", app["application_id"], SYSTEM)
    assert redraft["status"] == "REVISION_REQUIRED" and redraft["created"] is False
    revised = one(
        db,
        "SELECT * FROM api.create_offer(%s, %s::jsonb, %s::jsonb)",
        app["application_id"],
        {"monthly_salary": 200000},
        staff(SANA_HR),
    )
    assert revised["created"] is True and revised["revision"] == 2


def test_rejection_requires_a_reason(db: Conn) -> None:
    app = interviewed_and_selected(db)
    offer = one(db, "SELECT * FROM api.create_offer(%s, '{}'::jsonb, %s::jsonb)", app["application_id"], SYSTEM)
    with pytest.raises(psycopg.errors.CheckViolation):
        call(
            db,
            "SELECT * FROM api.decide_offer_approval(%s, 1::smallint, 'REJECTED', NULL, %s::jsonb)",
            offer["offer_id"],
            staff(AYESHA_L1),
        )


def test_decline_negotiate_and_revised_offer(db: Conn) -> None:
    declined = offered(db)
    one(
        db,
        "SELECT * FROM api.respond_to_offer(%s, 'DECLINE', 'Accepted another offer', %s::jsonb)",
        declined["offer_id"],
        candidate_ctx(db, declined["application_id"]),
    )
    assert status_of(db, declined["application_id"]) == "DECLINED"

    negotiating = offered(db)
    one(
        db,
        "SELECT * FROM api.respond_to_offer(%s, 'NEGOTIATE', 'Could you do 250k?', %s::jsonb)",
        negotiating["offer_id"],
        candidate_ctx(db, negotiating["application_id"]),
    )
    assert status_of(db, negotiating["application_id"]) == "NEGOTIATION"
    revised = one(
        db,
        "SELECT * FROM api.create_offer(%s, %s::jsonb, %s::jsonb)",
        negotiating["application_id"],
        {"monthly_salary": 240000},
        staff(SANA_HR),
    )
    old = one(db, "SELECT status FROM hiring.offers WHERE id = %s", negotiating["offer_id"])
    assert revised["revision"] == 2 and old["status"] == "SUPERSEDED"
    assert status_of(db, negotiating["application_id"]) == "OFFER_PENDING_APPROVAL"


def backdate_offer(conn: Conn, offer_id: Any) -> None:
    conn.execute(
        "UPDATE hiring.offers SET sent_at = now() - interval '10 days', expires_at = now() - interval '1 minute' "
        "WHERE id = %s",
        (offer_id,),
    )


def test_unanswered_offer_expires(db: Conn) -> None:
    app = offered(db)
    early = one(db, "SELECT * FROM api.expire_offer(%s, %s::jsonb)", app["offer_id"], SYSTEM)
    assert early["changed"] is False
    backdate_offer(db, app["offer_id"])
    with pytest.raises(psycopg.Error) as late:
        call(
            db,
            "SELECT * FROM api.respond_to_offer(%s, 'ACCEPT', NULL, %s::jsonb)",
            app["offer_id"],
            candidate_ctx(db, app["application_id"]),
        )
    assert late.value.sqlstate == "NT410"
    db.rollback()

    app = offered(db)
    backdate_offer(db, app["offer_id"])
    expired = one(db, "SELECT * FROM api.expire_offer(%s, %s::jsonb)", app["offer_id"], SYSTEM)
    assert expired["status"] == "EXPIRED" and status_of(db, app["application_id"]) == "OFFER_EXPIRED"


# ---- onboarding and operations -----------------------------------------------------------------------


def test_overdue_tasks_are_claimed_once_per_interval(db: Conn) -> None:
    app = offered(db)
    one(
        db,
        "SELECT * FROM api.respond_to_offer(%s, 'ACCEPT', NULL, %s::jsonb)",
        app["offer_id"],
        candidate_ctx(db, app["application_id"]),
    )
    emp = one(db, "SELECT * FROM api.create_employee_from_offer(%s, %s::jsonb)", app["offer_id"], SYSTEM)
    db.execute(
        "UPDATE hiring.onboarding_tasks SET due_date = current_date - 3 WHERE employee_id = %s AND task_key = "
        "'collect_documents'",
        (emp["employee_id"],),
    )
    first = call(db, "SELECT * FROM api.claim_overdue_onboarding_tasks(%s::jsonb)", SYSTEM)
    second = call(db, "SELECT * FROM api.claim_overdue_onboarding_tasks(%s::jsonb)", SYSTEM)
    mine = [r for r in first if r["employee_code"] == emp["employee_code"]]
    assert len(mine) == 1 and mine[0]["days_overdue"] >= 3
    assert not [r for r in second if r["employee_code"] == emp["employee_code"]]
    overview = one(db, "SELECT overdue_onboarding_tasks FROM reporting.v_ops_overview")
    assert overview["overdue_onboarding_tasks"] >= 1


def test_withdrawal_releases_slot_and_closes_everything(db: Conn) -> None:
    app = shortlisted_application(db)
    inv = one(db, "SELECT * FROM api.create_interview_invitation(%s, %s::jsonb)", app["application_id"], SYSTEM)
    slot = one(db, "SELECT * FROM api.interview_slot_options(%s, 1)", inv["interview_id"])
    ctx = candidate_ctx(db, app["application_id"])
    call(db, "SELECT * FROM api.confirm_interview_slot(%s, %s, %s::jsonb)", inv["interview_id"], slot["slot_id"], ctx)
    call(db, "SELECT * FROM api.withdraw_application(%s, 'took another job', %s::jsonb)", app["application_id"], ctx)
    assert status_of(db, app["application_id"]) == "WITHDRAWN"
    assert one(db, "SELECT status FROM hiring.interview_slots WHERE id = %s", slot["slot_id"])["status"] == "OPEN"
    assert actions(db, inv["interview_id"])["FEEDBACK_REMINDER"] == "CANCELLED"


def test_error_alerts_are_claimed_at_most_once(db: Conn) -> None:
    error = {"workflow_name": "WF-TEST", "error_class": "NON_RETRYABLE", "error_code": "X", "error_message": "boom"}
    error_id = one(
        db, "SELECT api.record_error(%s::jsonb, %s::jsonb) AS id", {**error, "entity_id": uuid.uuid4().hex}, SYSTEM
    )["id"]
    first = [r["error_id"] for r in call(db, "SELECT * FROM api.claim_unalerted_errors(%s::jsonb)", SYSTEM)]
    second = [r["error_id"] for r in call(db, "SELECT * FROM api.claim_unalerted_errors(%s::jsonb)", SYSTEM)]
    assert error_id in first and error_id not in second
