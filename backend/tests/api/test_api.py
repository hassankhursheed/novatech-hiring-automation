import io
from datetime import date, timedelta

from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from tests.conftest import API_KEY, FakeReferenceRepo, MemoryStorage

HEADERS = {"X-API-Key": API_KEY, "X-Correlation-ID": "COR-20260928-0001"}
SUBMISSION = {
    "full_name": "Ali Ahmed",
    "email": "ali@example.com",
    "phone": "03001234567",
    "position": "PY_DEV",
    "experience_years": 4,
    "skills": ["Python", "FastAPI"],
    "expected_salary": "180k",
    "available_from": (date.today() + timedelta(days=45)).isoformat(),
    "consent": True,
}


def make_pdf(text: str) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(72, 720, text)
    pdf.save()
    return buffer.getvalue()


def test_health_live(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}


def test_internal_endpoints_require_api_key(client: TestClient) -> None:
    missing = client.post("/v1/intake/validate", json=SUBMISSION)
    wrong = client.post("/v1/intake/validate", json=SUBMISSION, headers={"X-API-Key": "nope"})
    assert missing.status_code == wrong.status_code == 401
    assert missing.headers["content-type"].startswith("application/problem+json")
    assert missing.json()["code"] == "API_KEY_MISSING" and missing.json()["retryable"] is False


def test_rotated_key_is_accepted(client: TestClient) -> None:
    response = client.post("/v1/intake/validate", json=SUBMISSION, headers={"X-API-Key": "test-key-rotated"})
    assert response.status_code == 200


def test_validate_returns_contract_and_echoes_correlation(client: TestClient) -> None:
    response = client.post("/v1/intake/validate", json=SUBMISSION, headers=HEADERS)
    body = response.json()
    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == "COR-20260928-0001"
    assert body["correlation_id"] == "COR-20260928-0001"
    assert body["outcome"] == "NEEDS_REVIEW"  # no CV attached
    assert body["application"]["phone"] == "+923001234567"
    assert body["application"]["expected_salary"] == 180000
    assert [i["code"] for i in body["issues"]] == ["CV_MISSING"]


def test_cv_upload_then_validate_is_valid(client: TestClient, storage: MemoryStorage) -> None:
    upload = client.post(
        "/v1/public/cv", files={"file": ("cv.pdf", make_pdf("Python FastAPI engineer"), "application/pdf")}
    )
    assert upload.status_code == 201, upload.text
    cv_ref = upload.json()["cv_ref"]
    assert upload.json()["text_extracted"] is True and cv_ref in storage.files

    response = client.post("/v1/intake/validate", json={**SUBMISSION, "cv_ref": cv_ref}, headers=HEADERS)
    assert response.json()["outcome"] == "VALID"
    assert "FastAPI" in response.json()["application"]["cv_text"]


def test_cv_upload_rejects_non_documents_and_oversize(client: TestClient) -> None:
    fake = client.post("/v1/public/cv", files={"file": ("cv.pdf", b"MZ\x90\x00 not a pdf", "application/pdf")})
    assert fake.status_code == 400 and fake.json()["code"] == "CV_UNSUPPORTED"
    huge = client.post(
        "/v1/public/cv", files={"file": ("cv.pdf", b"%PDF-" + b"0" * (6 * 1024 * 1024), "application/pdf")}
    )
    assert huge.status_code == 413


def test_fault_injection_is_attempt_aware(client: TestClient) -> None:
    faulty = {**HEADERS, "X-Fault-Inject": "validate:http_503@2"}
    first = client.post("/v1/intake/validate", json=SUBMISSION, headers={**faulty, "X-Attempt": "1"})
    third = client.post("/v1/intake/validate", json=SUBMISSION, headers={**faulty, "X-Attempt": "3"})
    assert first.status_code == 503 and first.json()["retryable"] is True and "Retry-After" in first.headers
    assert third.status_code == 200

    permanent = client.post(
        "/v1/intake/validate", json=SUBMISSION, headers={**HEADERS, "X-Fault-Inject": "validate:http_400"}
    )
    assert permanent.status_code == 400 and permanent.json()["retryable"] is False


def test_score_endpoint(client: TestClient) -> None:
    response = client.post(
        "/v1/screening/score", json={"application_id": "00000000-0000-4000-8000-00000000aaaa"}, headers=HEADERS
    )
    body = response.json()
    assert response.status_code == 200
    assert body["score"] == 100.0 and body["route"] == "SHORTLIST" and body["scoring_version"] == 3
    assert len(body["breakdown"]) == 9


def test_ai_analysis_and_malformed_fault(client: TestClient) -> None:
    ok = client.post(
        "/v1/screening/ai-analysis", json={"application_id": "00000000-0000-4000-8000-00000000aaaa"}, headers=HEADERS
    )
    assert ok.status_code == 200 and ok.json()["status"] == "COMPLETED"

    bad = client.post(
        "/v1/screening/ai-analysis",
        json={"application_id": "00000000-0000-4000-8000-00000000aaaa"},
        headers={**HEADERS, "X-Fault-Inject": "ai:malformed"},
    )
    assert bad.status_code == 200 and bad.json()["status"] == "FALLBACK"
    assert bad.json()["fallback_reason"].startswith("MALFORMED_OUTPUT")


def test_decide_endpoint_reads_settings(client: TestClient, repo: FakeReferenceRepo) -> None:
    payload = {"score": {"route": "REJECT", "score": 20}, "ai": {"status": "COMPLETED", "recommendation": "REJECT"}}
    assert client.post("/v1/screening/decide", json=payload, headers=HEADERS).json()["decision"] == "REJECTED"
    repo.settings_values["screening.auto_reject_enabled"] = False
    assert client.post("/v1/screening/decide", json=payload, headers=HEADERS).json()["decision"] == "SCREENING_REVIEW"


def test_request_validation_errors_are_problem_json(client: TestClient) -> None:
    response = client.post("/v1/screening/score", json={"application_id": "not-a-uuid"}, headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_VALIDATION_FAILED"


def test_public_positions(client: TestClient) -> None:
    assert client.get("/v1/public/positions").json()[0]["code"] == "PY_DEV"
