"""Integration tests for the database write API (the rule enforcer).

Run against a migrated + seeded database:
    docker compose --profile test run --rm backend-tests
Each test runs in a transaction that is rolled back, so the database is left unchanged.
"""

import json
import os
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

pytestmark = pytest.mark.db

APP_URL = os.environ.get("DATABASE_URL_TEST")
OWNER_URL = os.environ.get("DATABASE_URL_OWNER_TEST")
SYSTEM = {
    "actor_type": "SYSTEM",
    "actor_id": "pytest",
    "workflow_name": "WF-TEST",
    "workflow_version": "1.0.0",
    "execution_id": "t-1",
}
RECRUITER = {"actor_type": "STAFF", "actor_id": "00000000-0000-4000-8000-000000000002"}

if not APP_URL:
    pytest.skip("DATABASE_URL_TEST not set", allow_module_level=True)


@pytest.fixture
def db() -> Iterator[psycopg.Connection[dict[str, Any]]]:
    with psycopg.connect(APP_URL, row_factory=dict_row) as conn:  # type: ignore[arg-type]
        yield conn
        conn.rollback()


@pytest.fixture
def owner() -> Iterator[psycopg.Connection[dict[str, Any]]]:
    if not OWNER_URL:
        pytest.skip("DATABASE_URL_OWNER_TEST not set")
    with psycopg.connect(OWNER_URL, row_factory=dict_row) as conn:
        yield conn
        conn.rollback()


def call(conn: psycopg.Connection[dict[str, Any]], sql: str, *params: Any) -> list[dict[str, Any]]:
    converted = [json.dumps(p) if isinstance(p, dict | list) else p for p in params]
    return conn.execute(sql, converted).fetchall()


def new_event(conn: psycopg.Connection[dict[str, Any]], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    key = f"test-{uuid.uuid4().hex}"
    return call(
        conn,
        "SELECT * FROM api.register_event('WEB_FORM', %s, 'APPLICATION_SUBMITTED', %s::jsonb, %s::jsonb)",
        key,
        payload or {"k": key},
        SYSTEM,
    )[0]


def submission(
    email: str, *, outcome: str = "VALID", phone: str | None = None, position: str = "PY_DEV"
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "issues": [] if outcome == "VALID" else [{"code": "CV_MISSING", "message": "no CV"}],
        "validator_version": "test",
        "application": {
            "full_name": "Test Candidate",
            "email": email,
            "phone": phone,
            "position_code": position,
            "experience_years": 4,
            "skills": ["python", "fastapi"],
            "expected_salary": 180000,
            "salary_currency": "PKR",
            "available_from": "2099-01-01",
            "consent": True,
        },
    }


def submit(conn: psycopg.Connection[dict[str, Any]], email: str, **kwargs: Any) -> dict[str, Any]:
    event = new_event(conn)
    return call(
        conn,
        "SELECT * FROM api.submit_application(%s, %s::jsonb, %s::jsonb)",
        event["event_id"],
        submission(email, **kwargs),
        SYSTEM,
    )[0]


def score(
    conn: psycopg.Connection[dict[str, Any]],
    app_id: str,
    route: str = "SHORTLIST",
    value: int = 85,
    input_hash: str = "h1",
) -> dict[str, Any]:
    payload = {
        "scoring_version": 1,
        "input_hash": input_hash,
        "points_awarded": value,
        "points_possible": 100,
        "score": value,
        "route": route,
        "shortlist_min_score": 80,
        "review_min_score": 60,
        "breakdown": [],
    }
    return call(conn, "SELECT * FROM api.record_application_score(%s, %s::jsonb, %s::jsonb)", app_id, payload, SYSTEM)[
        0
    ]


# ---- idempotency ----------------------------------------------------------------------------------


def test_event_replay_is_detected_and_key_reuse_rejected(db: psycopg.Connection[dict[str, Any]]) -> None:
    first = call(
        db, "SELECT * FROM api.register_event('WEB_FORM', 'replay-key-001', 'X', '{\"a\":1}', %s::jsonb)", SYSTEM
    )[0]
    again = call(
        db, "SELECT * FROM api.register_event('WEB_FORM', 'replay-key-001', 'X', '{\"a\":1}', %s::jsonb)", SYSTEM
    )[0]
    assert first["is_replay"] is False and again["is_replay"] is True
    assert first["correlation_id"] == again["correlation_id"] and again["receive_count"] == 2
    with pytest.raises(psycopg.Error) as exc:
        call(db, "SELECT * FROM api.register_event('WEB_FORM', 'replay-key-001', 'X', '{\"a\":2}', %s::jsonb)", SYSTEM)
    assert exc.value.sqlstate == "NT409" and "IDEMPOTENCY_KEY_REUSED" in str(exc.value)


def test_replaying_the_same_event_creates_no_duplicate_records(db: psycopg.Connection[dict[str, Any]]) -> None:
    event = new_event(db)
    first = call(
        db,
        "SELECT * FROM api.submit_application(%s, %s::jsonb, %s::jsonb)",
        event["event_id"],
        submission("replay@example.com"),
        SYSTEM,
    )[0]
    second = call(
        db,
        "SELECT * FROM api.submit_application(%s, %s::jsonb, %s::jsonb)",
        event["event_id"],
        submission("replay@example.com"),
        SYSTEM,
    )[0]
    assert first["outcome"] == "ACCEPTED" and second["replayed"] is True
    assert first["application_id"] == second["application_id"]
    count = call(
        db,
        "SELECT count(*) AS n FROM hiring.applications a JOIN hiring.candidates c ON c.id = a.candidate_id "
        "WHERE c.email = 'replay@example.com'",
    )[0]["n"]
    assert count == 1


def test_second_submission_for_same_position_is_a_duplicate(db: psycopg.Connection[dict[str, Any]]) -> None:
    first = submit(db, "dup@example.com")
    second = submit(db, "DUP@example.com")
    other_position = submit(db, "dup@example.com", position="QA_ENG")
    assert second["outcome"] == "DUPLICATE" and second["application_id"] == first["application_id"]
    assert other_position["outcome"] == "ACCEPTED" and other_position["candidate_id"] == first["candidate_id"]


def test_phone_match_on_different_email_is_flagged_not_merged(db: psycopg.Connection[dict[str, Any]]) -> None:
    first = submit(db, "phone-a@example.com", phone="+923009998877")
    second = submit(db, "phone-b@example.com", phone="+923009998877")
    assert second["candidate_id"] != first["candidate_id"]
    assert second["outcome"] == "NEEDS_REVIEW" and second["status"] == "SCREENING_REVIEW"
    assert "already used by candidate" in second["reason"]


def test_invalid_submission_is_recorded_not_dropped(db: psycopg.Connection[dict[str, Any]]) -> None:
    event = new_event(db)
    result = call(
        db,
        "SELECT * FROM api.submit_application(%s, %s::jsonb, %s::jsonb)",
        event["event_id"],
        {"outcome": "INVALID", "issues": [{"code": "NO_VALID_CONTACT", "message": "no contact"}], "application": {}},
        SYSTEM,
    )[0]
    stored = call(
        db, "SELECT status, outcome, outcome_reason FROM ops.processed_events WHERE id = %s", event["event_id"]
    )[0]
    assert result["outcome"] == "INVALID"
    assert stored == {"status": "COMPLETED", "outcome": "INVALID", "outcome_reason": "no contact"}


# ---- state machine --------------------------------------------------------------------------------


def test_happy_path_history_and_outbox(db: psycopg.Connection[dict[str, Any]]) -> None:
    app = submit(db, "flow@example.com")
    assert app["status"] == "VALIDATED"
    score(db, app["application_id"])
    call(
        db,
        "SELECT * FROM api.apply_screening_decision(%s, %s::jsonb, %s::jsonb)",
        app["application_id"],
        {"decision": "SHORTLISTED", "reason": "score 85"},
        SYSTEM,
    )
    history = [
        r["to_status"]
        for r in call(
            db,
            "SELECT to_status FROM hiring.candidate_status_history WHERE application_id = %s ORDER BY id",
            app["application_id"],
        )
    ]
    assert history == ["NEW", "VALIDATING", "VALIDATED", "SCORED", "SHORTLISTED"]
    actions = {
        r["action_type"]
        for r in call(
            db, "SELECT action_type FROM ops.scheduled_actions WHERE application_id = %s", app["application_id"]
        )
    }
    assert actions == {"SCREEN_APPLICATION", "INVITE_TO_INTERVIEW"}


@pytest.mark.parametrize(
    ("target", "ctx", "state", "code"),
    [
        ("OFFERED", SYSTEM, "NT409", "INVALID_TRANSITION"),
        ("REJECTED", {"actor_type": "AI", "actor_id": "model"}, "NT403", "AI_ACTOR_FORBIDDEN"),
        ("REJECTED", SYSTEM, "NT403", "ACTOR_NOT_ALLOWED"),
        ("INTERVIEW_SCHEDULED", RECRUITER, "NT409", "TRANSITION_REQUIRES_FUNCTION"),
    ],
)
def test_forbidden_transitions(
    db: psycopg.Connection[dict[str, Any]], target: str, ctx: dict[str, str], state: str, code: str
) -> None:
    app = submit(db, f"guard-{uuid.uuid4().hex[:6]}@example.com")
    score(db, app["application_id"])
    call(
        db,
        "SELECT * FROM api.apply_screening_decision(%s, %s::jsonb, %s::jsonb)",
        app["application_id"],
        {"decision": "SHORTLISTED", "reason": "ok"},
        SYSTEM,
    )
    with pytest.raises(psycopg.Error) as exc:
        call(
            db,
            "SELECT * FROM api.transition_application_status(%s, %s, 'x', %s::jsonb)",
            app["application_id"],
            target,
            ctx,
        )
    assert exc.value.sqlstate == state and code in str(exc.value)


def test_stale_state_is_rejected(db: psycopg.Connection[dict[str, Any]]) -> None:
    app = submit(db, "stale@example.com", outcome="NEEDS_REVIEW")
    with pytest.raises(psycopg.Error) as exc:
        call(
            db,
            "SELECT * FROM api.transition_application_status(%s, 'REJECTED', 'x', %s::jsonb, 'SHORTLISTED')",
            app["application_id"],
            RECRUITER,
        )
    assert "STALE_STATE" in str(exc.value)


def test_closing_an_application_cancels_its_timers(db: psycopg.Connection[dict[str, Any]]) -> None:
    app = submit(db, "close@example.com", outcome="NEEDS_REVIEW")
    call(
        db,
        "SELECT api.schedule_action('TEST_REMINDER', 'APPLICATION', %s, %s, now() + interval '2 days', %s, '{}', "
        "%s::jsonb)",
        app["application_id"],
        app["application_id"],
        f"rem:{app['application_id']}",
        SYSTEM,
    )
    call(
        db,
        "SELECT * FROM api.transition_application_status(%s, 'REJECTED', 'no CV after follow-up', %s::jsonb)",
        app["application_id"],
        RECRUITER,
    )
    rows = {
        r["action_type"]: r["status"]
        for r in call(
            db, "SELECT action_type, status FROM ops.scheduled_actions WHERE application_id = %s", app["application_id"]
        )
    }
    assert rows["TEST_REMINDER"] == "CANCELLED" and rows["SEND_REJECTION_NOTICE"] == "PENDING"


def test_scoring_replay_is_idempotent(db: psycopg.Connection[dict[str, Any]]) -> None:
    app = submit(db, "score-replay@example.com")
    assert score(db, app["application_id"])["replayed"] is False
    assert score(db, app["application_id"])["replayed"] is True
    n = call(db, "SELECT count(*) AS n FROM hiring.application_scores WHERE application_id = %s", app["application_id"])
    assert n[0]["n"] == 1


def test_ai_output_out_of_range_is_rejected_by_the_database(db: psycopg.Connection[dict[str, Any]]) -> None:
    app = submit(db, "ai-range@example.com")
    bad = {
        "status": "COMPLETED",
        "prompt_version": "v1",
        "input_hash": "x",
        "analysis": {
            "technical_strength": 42,
            "experience_relevance": 5,
            "communication_indication": 5,
            "summary": "s",
            "recommendation": "SHORTLIST",
        },
    }
    with pytest.raises(psycopg.errors.CheckViolation):
        call(db, "SELECT * FROM api.record_ai_analysis(%s, %s::jsonb, %s::jsonb)", app["application_id"], bad, SYSTEM)


# ---- scheduler, notifications, error queue ----------------------------------------------------------


def test_notification_is_sent_once(db: psycopg.Connection[dict[str, Any]]) -> None:
    key = f"invite:{uuid.uuid4().hex}"
    args = (key, "EMAIL", "interview.invite", "c@example.com", None, "APPLICATION", "x", SYSTEM)
    sql = "SELECT * FROM api.begin_notification(%s, %s, %s, %s, %s, %s, %s, %s::jsonb)"
    first = call(db, sql, *args)[0]
    call(db, "SELECT api.finish_notification(%s, 'SENT', 'msg-1', NULL, %s::jsonb)", first["notification_id"], SYSTEM)
    second = call(db, sql, *args)[0]
    assert first["should_send"] is True and second["should_send"] is False


def test_error_queue_dedupes_and_replays(db: psycopg.Connection[dict[str, Any]]) -> None:
    error = {
        "workflow_name": "WF-03",
        "node_name": "AI",
        "error_class": "RETRYABLE",
        "error_code": "UPSTREAM",
        "error_message": "503",
        "replay_workflow": "WF-03",
        "payload": {"application_id": "a"},
    }
    ctx = {**SYSTEM, "correlation_id": f"COR-{uuid.uuid4().hex[:8]}"}
    first = call(db, "SELECT api.record_error(%s::jsonb, %s::jsonb) AS id", error, ctx)[0]["id"]
    second = call(db, "SELECT api.record_error(%s::jsonb, %s::jsonb) AS id", error, ctx)[0]["id"]
    assert first == second
    replay = call(db, "SELECT * FROM api.begin_error_replay(%s, %s::jsonb)", first, RECRUITER)[0]
    assert replay["payload"] == {"application_id": "a"} and replay["replay_workflow"] == "WF-03"
    assert (
        call(db, "SELECT api.resolve_error(%s, 'RESOLVED', 'ok', %s::jsonb) AS s", first, SYSTEM)[0]["s"] == "RESOLVED"
    )


# ---- privileges and guards (need the owner connection) ---------------------------------------------


def test_runtime_role_cannot_write_tables(db: psycopg.Connection[dict[str, Any]]) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute("UPDATE hiring.applications SET status = 'OFFERED'")


def test_status_cannot_be_updated_directly_even_by_owner(owner: psycopg.Connection[dict[str, Any]]) -> None:
    app = submit(owner, "owner-guard@example.com")
    with pytest.raises(psycopg.Error) as exc:
        owner.execute("UPDATE hiring.applications SET status = 'OFFERED' WHERE id = %s", (app["application_id"],))
    assert "STATUS_CHANGE_FORBIDDEN" in str(exc.value)


def test_history_is_append_only(owner: psycopg.Connection[dict[str, Any]]) -> None:
    submit(owner, "append-only@example.com")
    with pytest.raises(psycopg.Error) as exc:
        owner.execute("DELETE FROM hiring.candidate_status_history")
    assert "APPEND_ONLY" in str(exc.value)


def test_invalid_setting_is_rejected(owner: psycopg.Connection[dict[str, Any]]) -> None:
    with pytest.raises(psycopg.Error) as exc:
        owner.execute("UPDATE hiring.settings SET value = '\"soon\"' WHERE key = 'offer.validity'")
    assert "INVALID_SETTING" in str(exc.value)


def test_rule_change_bumps_scoring_version(owner: psycopg.Connection[dict[str, Any]]) -> None:
    before = owner.execute(
        "SELECT version FROM hiring.scoring_configs sc JOIN hiring.job_positions p "
        "ON p.id = sc.job_position_id WHERE p.code = 'PY_DEV'"
    ).fetchone()["version"]
    owner.execute(
        "UPDATE hiring.scoring_rules SET points = 25 WHERE rule_key = 'python' AND job_position_id = "
        "(SELECT id FROM hiring.job_positions WHERE code = 'PY_DEV')"
    )
    after = owner.execute(
        "SELECT version FROM hiring.scoring_configs sc JOIN hiring.job_positions p "
        "ON p.id = sc.job_position_id WHERE p.code = 'PY_DEV'"
    ).fetchone()["version"]
    assert after == before + 1


def test_scheduler_backoff_and_exhaustion(owner: psycopg.Connection[dict[str, Any]]) -> None:
    owner.execute("UPDATE ops.scheduled_actions SET status = 'DONE', completed_at = now() WHERE status = 'PENDING'")
    action = call(
        owner,
        "SELECT api.schedule_action('TEST_ACTION', 'SYSTEM', gen_random_uuid(), NULL, now(), %s, '{}', "
        "%s::jsonb) AS id",
        f"t:{uuid.uuid4().hex}",
        SYSTEM,
    )[0]["id"]
    owner.execute("UPDATE ops.scheduled_actions SET max_attempts = 2 WHERE id = %s", (action,))

    claimed = call(owner, "SELECT id FROM api.claim_scheduled_actions('pytest', 10, 60)")
    assert [r["id"] for r in claimed] == [action]
    retry = call(
        owner, "SELECT * FROM api.complete_scheduled_action(%s, 'FAILED', NULL, 'boom', %s::jsonb)", action, SYSTEM
    )[0]
    assert retry["status"] == "PENDING" and retry["next_run_at"] is not None

    owner.execute("UPDATE ops.scheduled_actions SET run_at = now() WHERE id = %s", (action,))
    call(owner, "SELECT id FROM api.claim_scheduled_actions('pytest', 10, 60)")
    final = call(
        owner, "SELECT * FROM api.complete_scheduled_action(%s, 'FAILED', NULL, 'boom', %s::jsonb)", action, SYSTEM
    )[0]
    assert final["status"] == "FAILED"
    queued = call(
        owner,
        "SELECT count(*) AS n FROM ops.automation_errors WHERE entity_id = %s::text "
        "AND error_code = 'ACTION_ATTEMPTS_EXHAUSTED'",
        action,
    )
    assert queued[0]["n"] == 1
