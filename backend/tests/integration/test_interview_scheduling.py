"""Interview deadlines and slots, meeting details, offer approvers and who may decide on an application.

Runs against the throwaway database (see conftest.py); every test is rolled back.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.integration.conftest import DatabaseUrls
from tests.integration.test_lifecycle import (
    AYESHA_L1,
    BILAL_RECRUITER,
    SANA_HR,
    SYSTEM,
    USMAN_INTERVIEWER,
    Conn,
    call,
    candidate_ctx,
    interviewed_and_selected,
    one,
    shortlisted_application,
    staff,
)

HAMZA_SALES_L1 = "00000000-0000-4000-8000-000000000006"
ZOOM = {
    "mode": "ONLINE",
    "meeting_url": "https://zoom.us/j/9988776655",
    "meeting_id": "998 877 6655",
    "meeting_passcode": "nt2026",
}


@pytest.fixture
def db(database_urls: DatabaseUrls) -> Iterator[Conn]:
    with psycopg.connect(database_urls.owner, row_factory=dict_row) as conn:
        yield conn
        conn.rollback()


def in_review(conn: Conn) -> dict[str, Any]:
    """A Python Developer application waiting in screening review."""
    key = uuid.uuid4().hex
    event = one(
        conn,
        "SELECT * FROM api.register_event('WEB_FORM', %s, 'APPLICATION_SUBMITTED', %s::jsonb, %s::jsonb)",
        f"sched-{key}",
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
                "full_name": "Ali Raza",
                "email": f"ali.{key[:8]}@example.com",
                "position_code": "PY_DEV",
                "experience_years": 2,
                "skills": ["python"],
                "expected_salary": 150000,
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
            "points_awarded": 70,
            "points_possible": 100,
            "score": 70,
            "route": "REVIEW",
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
        {"decision": "SCREENING_REVIEW", "reason": "score 70"},
        SYSTEM,
    )
    return app


def fails(conn: Conn, code: str, sql: str, *params: Any) -> None:
    with pytest.raises(psycopg.Error) as exc, conn.transaction():  # savepoint: the test can continue afterwards
        call(conn, sql, *params)
    assert code in str(exc.value)


def invite(conn: Conn, app_id: Any) -> dict[str, Any]:
    return one(conn, "SELECT * FROM api.create_interview_invitation(%s, %s::jsonb)", app_id, SYSTEM)


# ---- response deadline and slots -----------------------------------------------------------------------
def test_only_slots_before_the_deadline_day_are_offered(db: Conn) -> None:
    inv = invite(db, shortlisted_application(db)["application_id"])
    row = one(
        db,
        """SELECT i.respond_by, (i.respond_by AT TIME ZONE hiring.company_timezone())::time AS local_time,
                  (i.respond_by AT TIME ZONE hiring.company_timezone())::date AS deadline_day
             FROM hiring.interviews i WHERE i.id = %s""",
        inv["interview_id"],
    )
    assert inv["invite_expires_at"] == row["respond_by"] and str(row["local_time"]) == "00:00:00"

    slots = call(
        db,
        """SELECT o.starts_at, (o.starts_at AT TIME ZONE hiring.company_timezone())::date AS day
             FROM api.interview_slot_options(%s, 50) o""",
        inv["interview_id"],
    )
    assert slots and all(s["day"] < row["deadline_day"] for s in slots)
    assert len({s["day"] for s in slots}) >= 2  # interview.min_choice_days

    timers = {
        r["action_type"]: r["run_at"]
        for r in call(
            db, "SELECT action_type, run_at FROM ops.scheduled_actions WHERE entity_id = %s", inv["interview_id"]
        )
    }
    assert timers["INTERVIEW_INVITE_EXPIRY"] == row["respond_by"]
    assert (
        timers["INTERVIEW_INVITE_REMINDER"]
        <= one(db, "SELECT %s::timestamptz - interval '1 day' AS t", row["respond_by"])["t"]
    )


def test_a_slot_on_or_after_the_deadline_day_cannot_be_booked(db: Conn) -> None:
    app = shortlisted_application(db)
    inv = invite(db, app["application_id"])
    late = one(
        db,
        """INSERT INTO hiring.interview_slots (interviewer_id, starts_at, ends_at)
           SELECT i.interviewer_id, i.respond_by + interval '3 hours', i.respond_by + interval '3 hours 45 minutes'
             FROM hiring.interviews i WHERE i.id = %s
           RETURNING id""",
        inv["interview_id"],
    )
    assert late["id"] not in {
        s["slot_id"] for s in call(db, "SELECT * FROM api.interview_slot_options(%s, 50)", inv["interview_id"])
    }
    fails(
        db,
        "SLOT_UNAVAILABLE",
        "SELECT * FROM api.confirm_interview_slot(%s, %s, %s::jsonb)",
        inv["interview_id"],
        late["id"],
        candidate_ctx(db, app["application_id"]),
    )


def test_slots_are_generated_from_the_weekly_availability(db: Conn) -> None:
    db.execute(
        "DELETE FROM hiring.interview_slots WHERE interviewer_id = %s AND status = 'OPEN' AND starts_at > now()",
        (USMAN_INTERVIEWER,),
    )
    inv = invite(db, shortlisted_application(db)["application_id"])  # the invitation regenerates them
    assert call(db, "SELECT * FROM api.interview_slot_options(%s, 50)", inv["interview_id"])


# ---- meeting details -------------------------------------------------------------------------------------
def test_staff_shortlisting_needs_the_meeting_and_the_interview_gets_it(db: Conn) -> None:
    app = in_review(db)
    shortlist = "SELECT * FROM api.transition_application_status(%s, 'SHORTLISTED', 'good fit', %s::jsonb)"
    fails(db, "MEETING_DETAILS_REQUIRED", shortlist, app["application_id"], staff(BILAL_RECRUITER))
    fails(
        db,
        "MEETING_LINK_REQUIRED",
        "SELECT * FROM api.set_interview_meeting(%s, %s::jsonb, %s::jsonb)",
        app["application_id"],
        {"mode": "ONLINE", "meeting_id": "123"},
        staff(BILAL_RECRUITER),
    )

    saved = one(
        db,
        "SELECT * FROM api.set_interview_meeting(%s, %s::jsonb, %s::jsonb)",
        app["application_id"],
        ZOOM,
        staff(BILAL_RECRUITER),
    )
    assert saved["changed"] is True and saved["interview_id"] is None
    one(db, shortlist, app["application_id"], staff(BILAL_RECRUITER))

    inv = invite(db, app["application_id"])
    interview = one(
        db,
        "SELECT mode, meeting_url, meeting_id, meeting_passcode FROM hiring.interviews WHERE id = %s",
        inv["interview_id"],
    )
    assert interview == {k: ZOOM[k] for k in ("mode", "meeting_url", "meeting_id", "meeting_passcode")}
    logged = one(
        db,
        "SELECT details FROM ops.automation_logs WHERE action = 'MEETING_DETAILS_SET' AND entity_id = %s",
        str(app["application_id"]),
    )
    assert "nt2026" not in str(logged["details"])  # the passcode never reaches the audit log


def test_a_changed_meeting_after_booking_is_emailed_to_the_candidate(db: Conn) -> None:
    app = interviewed_and_selected_booking(db)
    hr = staff(SANA_HR)
    changed = one(
        db,
        "SELECT * FROM api.set_interview_meeting(%s, %s::jsonb, %s::jsonb)",
        app["application_id"],
        {"mode": "ONSITE", "meeting_notes": "NovaTech office, 3rd floor. Ask for HR at reception."},
        hr,
    )
    assert changed["interview_status"] == "CONFIRMED" and changed["candidate_notified"] is True
    again = one(
        db,
        "SELECT * FROM api.set_interview_meeting(%s, %s::jsonb, %s::jsonb)",
        app["application_id"],
        {"mode": "ONSITE", "meeting_notes": "NovaTech office, 3rd floor. Ask for HR at reception."},
        hr,
    )
    assert again["changed"] is False and again["candidate_notified"] is False
    queued = call(
        db,
        "SELECT status FROM ops.scheduled_actions WHERE entity_id = %s AND action_type = 'SEND_MEETING_DETAILS'",
        app["interview_id"],
    )
    assert [q["status"] for q in queued] == ["PENDING"]
    assert (
        one(db, "SELECT meeting_url FROM hiring.interviews WHERE id = %s", app["interview_id"])["meeting_url"] is None
    )


def interviewed_and_selected_booking(conn: Conn) -> dict[str, Any]:
    """A shortlisted application whose candidate booked a slot."""
    app = shortlisted_application(conn)
    inv = invite(conn, app["application_id"])
    slot = one(conn, "SELECT * FROM api.interview_slot_options(%s, 1)", inv["interview_id"])
    call(
        conn,
        "SELECT * FROM api.confirm_interview_slot(%s, %s, %s::jsonb)",
        inv["interview_id"],
        slot["slot_id"],
        candidate_ctx(conn, app["application_id"]),
    )
    return {**app, "interview_id": inv["interview_id"]}


# ---- who decides --------------------------------------------------------------------------------------------
def test_only_hr_recruiters_and_the_positions_hiring_manager_decide(db: Conn) -> None:
    app = in_review(db)
    reject = "SELECT * FROM api.transition_application_status(%s, 'REJECTED', 'not a fit', %s::jsonb)"
    fails(db, "NOT_ALLOWED_FOR_ROLE", reject, app["application_id"], staff(USMAN_INTERVIEWER))
    fails(db, "NOT_ALLOWED_FOR_ROLE", reject, app["application_id"], staff(HAMZA_SALES_L1))  # another department
    assert one(db, reject, app["application_id"], staff(AYESHA_L1))["to_status"] == "REJECTED"


def test_level_one_approval_belongs_to_the_offers_department(db: Conn) -> None:
    app = interviewed_and_selected(db)
    offer = one(db, "SELECT * FROM api.create_offer(%s, '{}'::jsonb, %s::jsonb)", app["application_id"], SYSTEM)
    approvers = one(db, "SELECT api.offer_snapshot(%s) -> 'next_approvers' AS a", offer["offer_id"])["a"]
    assert [a["id"] for a in approvers] == [AYESHA_L1]
    fails(
        db,
        "NOT_THE_DEPARTMENT_APPROVER",
        "SELECT * FROM api.decide_offer_approval(%s, 1::smallint, 'APPROVED', NULL, %s::jsonb)",
        offer["offer_id"],
        staff(HAMZA_SALES_L1),
    )


def test_drafter_cannot_approve_while_another_approver_is_available(db: Conn) -> None:
    app = interviewed_and_selected(db)
    offer = one(
        db, "SELECT * FROM api.create_offer(%s, '{}'::jsonb, %s::jsonb)", app["application_id"], staff(AYESHA_L1)
    )
    fails(
        db,
        "SELF_APPROVAL_FORBIDDEN",
        "SELECT * FROM api.decide_offer_approval(%s, 1::smallint, 'APPROVED', NULL, %s::jsonb)",
        offer["offer_id"],
        staff(AYESHA_L1),
    )


def test_a_single_approver_approves_both_levels_and_the_waiver_is_audited(db: Conn) -> None:
    db.execute(
        "UPDATE hiring.staff_members SET roles = array_remove(array_remove(roles, 'APPROVER_L1'), 'APPROVER_L2')"
    )
    db.execute("UPDATE hiring.staff_members SET roles = roles || '{APPROVER_L1,APPROVER_L2}' WHERE id = %s", (SANA_HR,))
    app = interviewed_and_selected(db, salary=320000)
    offer = one(db, "SELECT * FROM api.create_offer(%s, '{}'::jsonb, %s::jsonb)", app["application_id"], staff(SANA_HR))
    assert offer["required_approval_levels"] == 2
    decide = "SELECT * FROM api.decide_offer_approval(%s, %s::smallint, 'APPROVED', NULL, %s::jsonb)"
    assert one(db, decide, offer["offer_id"], 1, staff(SANA_HR))["next_level"] == 2
    assert one(db, decide, offer["offer_id"], 2, staff(SANA_HR))["offer_status"] == "APPROVED"
    waived = call(
        db,
        "SELECT details->>'segregation_waived' AS w FROM ops.automation_logs WHERE entity_id = %s "
        "AND action = 'OFFER_APPROVED'",
        str(offer["offer_id"]),
    )
    assert [w["w"] for w in waived] == ["true", "true"]
