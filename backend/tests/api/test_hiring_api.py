from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.links import LinkPurpose, LinkSigner
from tests.conftest import (
    API_KEY,
    APPROVER_ID,
    CANDIDATE_ID,
    HR_ID,
    INTERVIEW_ID,
    INTERVIEWER_ID,
    OFFER_ID,
    SLOT_ID,
    FakeHiringRepo,
    FakeN8n,
    MemoryStorage,
    offer_snapshot,
)

HEADERS = {"X-API-Key": API_KEY, "X-Correlation-ID": "COR-20261001-0001"}
APP_ID = "00000000-0000-4000-8000-00000000aaaa"


def bearer(purpose: LinkPurpose, subject: str, entity: str) -> dict[str, str]:
    token, _ = LinkSigner.from_settings(get_settings()).issue(purpose, subject, entity)
    return {"Authorization": f"Bearer {token}"}


# ---- internal endpoints (n8n) ----------------------------------------------------------------------------
def test_link_endpoint_builds_portal_url_with_token_in_fragment(client: TestClient) -> None:
    expires = (datetime.now(UTC) + timedelta(days=4)).isoformat()
    response = client.post(
        "/v1/links",
        json={
            "purpose": "INTERVIEW_SLOT",
            "subject_id": CANDIDATE_ID,
            "entity_id": INTERVIEW_ID,
            "expires_at": expires,
        },
        headers=HEADERS,
    )
    body = response.json()
    assert response.status_code == 200
    assert body["url"].startswith("https://careers.novatech.example/candidate/interview#token=")
    assert body["url"].endswith(body["token"])
    assert client.post("/v1/links", json={"purpose": "INTERVIEW_SLOT"}).status_code == 401


def test_evaluate_interview(client: TestClient, hiring_repo: FakeHiringRepo) -> None:
    body = client.post("/v1/interviews/evaluate", json={"application_id": APP_ID}, headers=HEADERS).json()
    assert body["decision"] == "SELECTED" and body["final_score"] == 85.8
    assert body["weights"] == {"application": 0.3, "interview": 0.7}

    hiring_repo.settings_values["evaluation.select_min_score"] = 90  # config change applies to the next request
    assert (
        client.post("/v1/interviews/evaluate", json={"application_id": APP_ID}, headers=HEADERS).json()["decision"]
        == "INTERVIEW_REVIEW"
    )

    hiring_repo.settings_values["evaluation.review_min_score"] = 95
    bad = client.post("/v1/interviews/evaluate", json={"application_id": APP_ID}, headers=HEADERS)
    assert bad.status_code == 422 and bad.json()["code"] == "INVALID_EVALUATION_SETTINGS"


def test_offer_document_requires_approval_and_is_stored(
    client: TestClient, hiring_repo: FakeHiringRepo, storage: MemoryStorage
) -> None:
    pending = client.post("/v1/offers/document", json={"offer_id": OFFER_ID}, headers=HEADERS)
    assert pending.status_code == 409 and pending.json()["code"] == "OFFER_NOT_APPROVED"

    hiring_repo.offer = offer_snapshot("APPROVED")
    first = client.post("/v1/offers/document", json={"offer_id": OFFER_ID}, headers=HEADERS).json()
    again = client.post("/v1/offers/document", json={"offer_id": OFFER_ID}, headers=HEADERS).json()
    assert first["document_key"] == "offers/2026/OFF-2026-0001-r1.pdf"
    assert first["sha256"] == again["sha256"] and storage.files[first["document_key"]][:4] == b"%PDF"


def test_daily_summary_falls_back_to_template_and_returns_sections(client: TestClient) -> None:
    metrics = {"applications_received": 3, "shortlisted": 1, "manual_intervention_required": 0}
    body = client.post(
        "/v1/reports/daily-summary", json={"report_date": "2026-10-01", "metrics": metrics}, headers=HEADERS
    ).json()
    assert body["summary_source"] == "TEMPLATE" and "3 application(s)" in body["summary"]
    assert body["sections"][0]["rows"][0] == {
        "key": "applications_received",
        "label": "Applications received",
        "value": 3,
    }


# ---- candidate links ------------------------------------------------------------------------------------
def test_candidate_views_and_confirms_slot_then_dispatcher_is_kicked(
    client: TestClient, hiring_repo: FakeHiringRepo, n8n: FakeN8n
) -> None:
    auth = bearer(LinkPurpose.INTERVIEW_SLOT, CANDIDATE_ID, INTERVIEW_ID)
    assert client.get("/v1/portal/interview", headers=auth).json()["slots"] == [{"slot_id": SLOT_ID}]
    response = client.post("/v1/portal/interview/confirm", json={"slot_id": SLOT_ID}, headers=auth)
    assert response.status_code == 200 and response.json()["changed"] is True
    name, (interview_id, slot_id, ctx) = hiring_repo.calls[-1]
    assert (name, interview_id, slot_id) == ("confirm_slot", INTERVIEW_ID, SLOT_ID)
    assert ctx["actor_type"] == "CANDIDATE" and ctx["actor_id"] == CANDIDATE_ID
    assert n8n.kicks == ["interview confirmed"]


def test_links_are_bound_to_their_purpose(client: TestClient) -> None:
    offer_token = bearer(LinkPurpose.OFFER_RESPONSE, CANDIDATE_ID, OFFER_ID)
    wrong = client.post("/v1/portal/interview/confirm", json={"slot_id": SLOT_ID}, headers=offer_token)
    assert wrong.status_code == 401 and wrong.json()["code"] == "LINK_INVALID"
    missing = client.get("/v1/portal/interview")
    assert missing.status_code == 401 and missing.json()["code"] == "LINK_TOKEN_MISSING"


def test_candidate_offer_view_hides_internal_data_and_response_is_recorded(
    client: TestClient, hiring_repo: FakeHiringRepo, n8n: FakeN8n
) -> None:
    hiring_repo.offer = offer_snapshot("SENT", expires_at="2026-10-06T10:00:00+05:00")
    auth = bearer(LinkPurpose.OFFER_RESPONSE, CANDIDATE_ID, OFFER_ID)
    view = client.get("/v1/portal/offer", headers=auth).json()
    assert view["monthly_salary"] == 220000 and view["document_available"] is True
    assert "approvals" not in view and "application_score" not in str(view)

    answer = client.post(
        "/v1/portal/offer/respond", json={"response": "ACCEPT", "message": "Happy to join"}, headers=auth
    )
    assert answer.status_code == 200 and n8n.kicks == ["offer accept"]
    assert hiring_repo.calls[-1][1][1] == "ACCEPT"


def test_candidate_downloads_offer_letter_re_rendered_when_missing(
    client: TestClient, hiring_repo: FakeHiringRepo
) -> None:
    auth = bearer(LinkPurpose.OFFER_RESPONSE, CANDIDATE_ID, OFFER_ID)
    assert client.get("/v1/portal/offer/document", headers=auth).status_code == 404  # still pending approval
    hiring_repo.offer = offer_snapshot("SENT", document_storage_key="offers/2026/OFF-2026-0001-r1.pdf")
    pdf = client.get("/v1/portal/offer/document", headers=auth)
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content[:4] == b"%PDF"


def test_another_candidates_token_cannot_see_the_offer(client: TestClient) -> None:
    other = bearer(LinkPurpose.OFFER_RESPONSE, "00000000-0000-4000-8000-0000000000c2", OFFER_ID)
    assert client.get("/v1/portal/offer", headers=other).status_code == 404


# ---- staff links ----------------------------------------------------------------------------------------
def test_interviewer_submits_feedback_through_link(client: TestClient, hiring_repo: FakeHiringRepo) -> None:
    auth = bearer(LinkPurpose.INTERVIEW_FEEDBACK, INTERVIEWER_ID, INTERVIEW_ID)
    assert client.get("/v1/portal/feedback", headers=auth).json()["candidate_name"] == "Hira Saleem"
    scorecard = {
        "technical_skills": 4,
        "communication": 4,
        "problem_solving": 5,
        "experience": 4,
        "team_fit": 4,
        "recommendation": "HIRE",
        "comments": "Solid.",
    }
    assert client.post("/v1/portal/feedback", json=scorecard, headers=auth).status_code == 200
    assert hiring_repo.calls[-1][1][2]["actor_type"] == "STAFF"
    invalid = client.post("/v1/portal/feedback", json={**scorecard, "team_fit": 6}, headers=auth)
    assert invalid.status_code == 422

    not_mine = bearer(LinkPurpose.INTERVIEW_FEEDBACK, APPROVER_ID, INTERVIEW_ID)
    assert client.get("/v1/portal/feedback", headers=not_mine).status_code == 404


def test_approver_decides_next_level_by_default(client: TestClient, hiring_repo: FakeHiringRepo) -> None:
    auth = bearer(LinkPurpose.OFFER_APPROVAL, APPROVER_ID, OFFER_ID)
    view = client.get("/v1/portal/approval", headers=auth).json()
    assert view["can_decide"] is True and view["next_level"] == 1 and view["application_score"] == 90
    assert client.post("/v1/portal/approval", json={"decision": "APPROVED"}, headers=auth).status_code == 200
    assert hiring_repo.calls[-1][1][1:3] == (1, "APPROVED")

    hiring_repo.offer = offer_snapshot("APPROVED", next_level=None)
    done = client.post("/v1/portal/approval", json={"decision": "APPROVED"}, headers=auth)
    assert done.status_code == 409 and done.json()["code"] == "OFFER_NOT_PENDING"


# ---- staff API --------------------------------------------------------------------------------------------
def test_staff_endpoints_need_key_and_known_staff(client: TestClient, hiring_repo: FakeHiringRepo) -> None:
    body = {"to_status": "SHORTLISTED", "reason": "strong portfolio"}
    url = f"/v1/staff/applications/{APP_ID}/transition"
    assert client.post(url, json=body, headers={"X-Staff-Id": HR_ID}).status_code == 401
    unknown = client.post(url, json=body, headers={**HEADERS, "X-Staff-Id": "00000000-0000-4000-8000-0000000000ff"})
    assert unknown.status_code == 401 and unknown.json()["code"] == "UNKNOWN_STAFF_ACTOR"
    ok = client.post(url, json=body, headers={**HEADERS, "X-Staff-Id": HR_ID})
    assert ok.status_code == 200 and hiring_repo.calls[-1][1][3]["actor_id"] == HR_ID


def test_staff_revises_offer_and_reads_queues(client: TestClient, hiring_repo: FakeHiringRepo, n8n: FakeN8n) -> None:
    headers = {**HEADERS, "X-Staff-Id": HR_ID}
    revised = client.post(f"/v1/staff/applications/{APP_ID}/offers", json={"monthly_salary": 240000}, headers=headers)
    assert revised.status_code == 200 and hiring_repo.calls[-1][1][1] == {"monthly_salary": 240000}
    assert n8n.kicks == ["offer drafted"]
    assert client.get("/v1/staff/queues/errors", headers=headers).json() == [{"view": "v_error_queue"}]
    assert client.get("/v1/staff/queues/users;drop", headers=headers).status_code == 404


def test_staff_replay_goes_through_n8n(client: TestClient, n8n: FakeN8n) -> None:
    error_id = "00000000-0000-4000-8000-0000000000dd"
    response = client.post(f"/v1/staff/errors/{error_id}/replay", headers={**HEADERS, "X-Staff-Id": HR_ID})
    assert response.status_code == 200 and n8n.replays == [(error_id, HR_ID)]
