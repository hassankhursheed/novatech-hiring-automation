"""The candidate hears from us at every step, and the AI interview assessment is stored like every other AI output.

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
    SYSTEM,
    Conn,
    call,
    candidate_ctx,
    interviewed_and_selected,
    one,
    shortlisted_application,
)


@pytest.fixture
def db(database_urls: DatabaseUrls) -> Iterator[Conn]:
    with psycopg.connect(database_urls.owner, row_factory=dict_row) as conn:
        yield conn
        conn.rollback()


# Steps where nothing reaches the candidate on purpose: internal processing and internal approvals.
INTERNAL = {"NEW", "VALIDATING", "VALIDATED", "SCORED", "INTERVIEW_REVIEW", "OFFER_PENDING_APPROVAL"}


def notices(conn: Conn, app_id: Any) -> list[dict[str, Any]]:
    return call(
        conn,
        """SELECT payload->>'to_status' AS to_status, payload->>'template_key' AS template_key, payload, status
             FROM ops.scheduled_actions
            WHERE application_id = %s AND action_type = 'NOTIFY_CANDIDATE' ORDER BY created_at""",
        app_id,
    )


def test_every_candidate_facing_step_sends_an_email(db: Conn) -> None:
    rows = call(db, "SELECT code, candidate_informed FROM hiring.application_statuses")
    silent = {r["code"] for r in rows if not r["candidate_informed"]}
    assert silent == INTERNAL


def test_status_updates_are_queued_with_the_status_change(db: Conn) -> None:
    app = interviewed_and_selected(db)
    queued = {n["to_status"]: n for n in notices(db, app["application_id"])}
    assert queued["INTERVIEWED"]["template_key"] == "interview.completed"
    assert queued["SELECTED"]["template_key"] == "application.selected"
    for notice in queued.values():
        assert "reason" not in notice["payload"]  # internal reasons never reach the candidate's email
        assert notice["status"] == "PENDING"


def test_a_review_tells_the_candidate_their_application_is_being_reviewed(db: Conn) -> None:
    app = shortlisted_application(db)
    call(
        db,
        "SELECT * FROM api.expire_interview_invitation(%s, %s::jsonb)",
        one(db, "SELECT * FROM api.create_interview_invitation(%s, %s::jsonb)", app["application_id"], SYSTEM)[
            "interview_id"
        ],
        SYSTEM,
    )
    queued = notices(db, app["application_id"])
    assert [n["template_key"] for n in queued] == ["application.under_review"]
    assert queued[0]["payload"]["from_status"] == "SHORTLISTED"


def test_withdrawal_confirmation_survives_the_closing_of_the_application(db: Conn) -> None:
    app = shortlisted_application(db)
    ctx = candidate_ctx(db, app["application_id"])
    call(db, "SELECT * FROM api.withdraw_application(%s, 'took another job', %s::jsonb)", app["application_id"], ctx)
    queued = notices(db, app["application_id"])
    assert [(n["template_key"], n["status"]) for n in queued] == [("application.withdrawn", "PENDING")]


def assessment(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "status": "COMPLETED",
        "prompt_version": "interview-assessment/v1",
        "input_hash": uuid.uuid4().hex,
        "provider": "mistral",
        "model": "mistral-medium-latest",
        "attempts": 1,
        "latency_ms": 900,
        "assessment": {
            "recommendation": "SELECT",
            "evidence_alignment": "ALIGNED",
            "strengths": ["clear API design"],
            "concerns": ["limited Kubernetes exposure"],
            "summary": "Ratings and comments agree on strong backend skills.",
        },
    }
    body.update(overrides)
    return body


def test_interview_assessment_is_recorded_once_and_a_fallback_can_be_upgraded(db: Conn) -> None:
    app = interviewed_and_selected(db)
    sql = "SELECT * FROM api.record_interview_assessment(%s, %s::jsonb, %s::jsonb)"
    fallback = assessment(status="FALLBACK", assessment=None, fallback_reason="AI_PROVIDER_UNAVAILABLE")
    first = one(db, sql, app["interview_id"], fallback, SYSTEM)
    assert first["status"] == "FALLBACK" and first["replayed"] is False

    upgraded = one(db, sql, app["interview_id"], {**assessment(), "input_hash": fallback["input_hash"]}, SYSTEM)
    assert upgraded["status"] == "COMPLETED" and upgraded["recommendation"] == "SELECT"
    replay = one(db, sql, app["interview_id"], {**assessment(), "input_hash": fallback["input_hash"]}, SYSTEM)
    assert replay["replayed"] is True and replay["assessment_id"] == upgraded["assessment_id"]

    logged = one(
        db,
        "SELECT count(*) AS n FROM ops.automation_logs WHERE entity_id = %s AND action LIKE 'AI_ASSESSMENT_%%'",
        str(app["interview_id"]),
    )
    assert logged["n"] == 2  # the fallback and the upgrade; the replay writes nothing


def test_the_database_rejects_an_assessment_outside_the_contract(db: Conn) -> None:
    app = interviewed_and_selected(db)
    bad = assessment(assessment={**assessment()["assessment"], "recommendation": "HIRE_NOW"})
    with pytest.raises(psycopg.errors.CheckViolation):
        call(
            db,
            "SELECT * FROM api.record_interview_assessment(%s, %s::jsonb, %s::jsonb)",
            app["interview_id"],
            bad,
            SYSTEM,
        )
