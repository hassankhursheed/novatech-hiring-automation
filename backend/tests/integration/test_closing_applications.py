"""A closed application leaves nothing open, and the interviewer hears about every cancelled booking.

Runs against the throwaway database (see conftest.py); every test is rolled back.
"""

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.integration.conftest import DatabaseUrls
from tests.integration.test_interview_scheduling import fails, interviewed_and_selected_booking, invite
from tests.integration.test_lifecycle import (
    BILAL_RECRUITER,
    Conn,
    call,
    candidate_ctx,
    one,
    shortlisted_application,
    staff,
)


@pytest.fixture
def db(database_urls: DatabaseUrls) -> Iterator[Conn]:
    with psycopg.connect(database_urls.owner, row_factory=dict_row) as conn:
        yield conn
        conn.rollback()


def cancellation_notices(conn: Conn, interview_id: Any) -> list[str]:
    rows = call(
        conn,
        "SELECT status FROM ops.scheduled_actions WHERE entity_id = %s AND action_type = 'NOTIFY_INTERVIEW_CANCELLED'",
        interview_id,
    )
    return [r["status"] for r in rows]


def test_rejecting_a_shortlisted_candidate_closes_the_invitation(db: Conn) -> None:
    app = shortlisted_application(db)
    inv = invite(db, app["application_id"])
    slot = one(db, "SELECT * FROM api.interview_slot_options(%s, 1)", inv["interview_id"])
    one(
        db,
        "SELECT * FROM api.transition_application_status(%s, 'REJECTED', 'position filled', %s::jsonb)",
        app["application_id"],
        staff(BILAL_RECRUITER),
    )
    interview = one(db, "SELECT status, cancel_reason FROM hiring.interviews WHERE id = %s", inv["interview_id"])
    assert interview == {"status": "CANCELLED", "cancel_reason": "application closed as REJECTED"}
    fails(  # the candidate's old link can no longer book a time
        db,
        "INTERVIEW_NOT_OPEN",
        "SELECT * FROM api.confirm_interview_slot(%s, %s, %s::jsonb)",
        inv["interview_id"],
        slot["slot_id"],
        candidate_ctx(db, app["application_id"]),
    )
    assert cancellation_notices(db, inv["interview_id"]) == []  # nothing was booked: no one to tell
    pending = call(
        db,
        "SELECT action_type FROM ops.scheduled_actions WHERE application_id = %s AND status = 'PENDING'",
        app["application_id"],
    )
    assert [p["action_type"] for p in pending] == ["SEND_REJECTION_NOTICE"]


def test_withdrawing_a_booked_interview_frees_the_slot_and_tells_the_interviewer(db: Conn) -> None:
    app = interviewed_and_selected_booking(db)
    slot_id = one(db, "SELECT slot_id FROM hiring.interviews WHERE id = %s", app["interview_id"])["slot_id"]
    call(
        db,
        "SELECT * FROM api.withdraw_application(%s, 'accepted another offer', %s::jsonb)",
        app["application_id"],
        staff(BILAL_RECRUITER),
    )
    assert one(db, "SELECT status FROM hiring.interview_slots WHERE id = %s", slot_id)["status"] == "OPEN"
    assert cancellation_notices(db, app["interview_id"]) == ["PENDING"]  # survives the closing clean-up


def test_a_candidate_cancelling_a_booking_tells_the_interviewer(db: Conn) -> None:
    app = interviewed_and_selected_booking(db)
    call(
        db,
        "SELECT * FROM api.cancel_interview(%s, 'family emergency', %s::jsonb)",
        app["interview_id"],
        candidate_ctx(db, app["application_id"]),
    )
    assert cancellation_notices(db, app["interview_id"]) == ["PENDING"]
    assert (
        one(db, "SELECT status FROM hiring.applications WHERE id = %s", app["application_id"])["status"]
        == "SHORTLISTED"
    )
