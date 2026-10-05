from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.routes import auth
from app.core.config import Settings, get_settings
from app.core.links import LinkPurpose, LinkSigner
from tests.conftest import API_KEY, HR_ID, FakeN8n

STAFF = {
    "staff_id": HR_ID,
    "full_name": "Sana Malik",
    "email": "sana.malik@novatech.example",
    "roles": ["HR_ADMIN", "RECRUITER"],
    "job_title": "HR Manager",
    "department": "People",
    "is_active": True,
}


class FakeDirectory:
    def __init__(self) -> None:
        self.consumed: set[str] = set()

    async def by_email(self, email: str) -> dict[str, Any] | None:
        return STAFF if email.lower() == STAFF["email"] else None

    async def profile(self, staff_id: str) -> dict[str, Any] | None:
        return STAFF if staff_id == HR_ID else None

    async def consume_link(self, token_id: str, purpose: str, subject_id: str, expires_at: Any, ctx: Any) -> bool:
        if token_id in self.consumed:
            return False
        self.consumed.add(token_id)
        return True

    async def applications(self, status: str | None, search: str | None, limit: int, offset: int) -> list[dict]:
        return [{"application_code": "APP-2026-00001", "status": status or "SHORTLISTED", "search": search}]

    async def onboarding_board(self) -> list[dict[str, Any]]:
        return []

    async def active_staff(self) -> list[dict[str, Any]]:
        return [{k: STAFF[k] for k in ("full_name", "email", "roles", "job_title")}]


@pytest.fixture(autouse=True)
def directory(client: TestClient) -> FakeDirectory:
    fake = FakeDirectory()
    client.app.state.staff_directory = fake  # type: ignore[attr-defined]
    auth._LAST_LINK.clear()
    auth._FAILURES.clear()
    return fake


@pytest.fixture
def demo_password(client: TestClient) -> Iterator[str]:
    settings = Settings(staff_demo_password="Demo-pass-2026")
    client.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]
    yield "Demo-pass-2026"
    client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def login_token() -> str:
    token, _ = LinkSigner.from_settings(get_settings()).issue(LinkPurpose.STAFF_LOGIN, HR_ID, HR_ID)
    return token


def test_login_link_is_emailed_only_for_active_staff_and_never_reveals_accounts(
    client: TestClient, n8n: FakeN8n
) -> None:
    sent: list[dict[str, Any]] = []

    async def notify(message: dict[str, Any]) -> bool:
        sent.append(message)
        return True

    n8n.notify = notify  # type: ignore[attr-defined]
    known = client.post("/v1/auth/staff/login-link", json={"email": "Sana.Malik@novatech.example"})
    unknown = client.post("/v1/auth/staff/login-link", json={"email": "nobody@novatech.example"})
    again = client.post("/v1/auth/staff/login-link", json={"email": "sana.malik@novatech.example"})
    assert known.status_code == unknown.status_code == again.status_code == 202
    assert known.json() == unknown.json()  # same answer either way
    assert len(sent) == 1  # unknown address ignored, repeat within a minute throttled
    assert sent[0]["recipient"] == STAFF["email"] and "/staff/login#token=" in sent[0]["html"]


def test_sign_in_link_works_exactly_once_and_session_opens_staff_endpoints(client: TestClient) -> None:
    token = login_token()
    first = client.post("/v1/auth/staff/session", headers={"Authorization": f"Bearer {token}"})
    second = client.post("/v1/auth/staff/session", headers={"Authorization": f"Bearer {token}"})
    assert first.status_code == 200 and first.json()["staff"]["full_name"] == "Sana Malik"
    assert second.status_code == 401 and second.json()["code"] == "LINK_ALREADY_USED"

    session = {"Authorization": f"Bearer {first.json()['token']}"}
    assert client.get("/v1/staff/me", headers=session).json()["email"] == STAFF["email"]
    listed = client.get("/v1/staff/applications?status=SCREENING_REVIEW&search=APP", headers=session).json()
    assert listed[0]["status"] == "SCREENING_REVIEW" and listed[0]["search"] == "APP"


def test_other_tokens_cannot_be_used_as_a_session(client: TestClient) -> None:
    login = {"Authorization": f"Bearer {login_token()}"}
    assert client.get("/v1/staff/me", headers=login).status_code == 401  # a sign-in link is not a session
    candidate, _ = LinkSigner.from_settings(get_settings()).issue(LinkPurpose.OFFER_RESPONSE, HR_ID, HR_ID)
    assert client.get("/v1/staff/me", headers={"Authorization": f"Bearer {candidate}"}).status_code == 401
    assert client.get("/v1/staff/me").json()["code"] in {"API_KEY_MISSING", "STAFF_AUTH_REQUIRED"}


def test_internal_tools_still_use_the_service_key(client: TestClient) -> None:
    response = client.get("/v1/staff/me", headers={"X-API-Key": API_KEY, "X-Staff-Id": HR_ID})
    assert response.status_code == 200 and response.json()["staff_id"] == HR_ID


# ---- demo password sign-in -----------------------------------------------------------------------------------
def test_password_sign_in_is_off_unless_configured(client: TestClient) -> None:
    assert client.get("/v1/auth/staff/options").json() == {"email_link": True, "password": False}
    off = client.post("/v1/auth/staff/password-login", json={"email": STAFF["email"], "password": "anything"})
    assert off.status_code == 404 and off.json()["code"] == "PASSWORD_LOGIN_DISABLED"


def test_demo_password_signs_in_any_active_staff_member(client: TestClient, demo_password: str) -> None:
    options = client.get("/v1/auth/staff/options").json()
    assert options["password"] is True and options["demo_login"]["email"] == STAFF["email"]  # the HR admin
    assert options["demo_login"]["password"] == demo_password
    ok = client.post(
        "/v1/auth/staff/password-login", json={"email": "SANA.MALIK@novatech.example", "password": demo_password}
    )
    assert ok.status_code == 200 and ok.json()["staff"]["full_name"] == "Sana Malik"
    session = {"Authorization": f"Bearer {ok.json()['token']}"}
    assert client.get("/v1/staff/me", headers=session).json()["staff_id"] == HR_ID


def test_wrong_password_and_unknown_email_get_the_same_answer_then_throttle(
    client: TestClient, demo_password: str
) -> None:
    wrong = client.post("/v1/auth/staff/password-login", json={"email": STAFF["email"], "password": "nope"})
    unknown = client.post(
        "/v1/auth/staff/password-login", json={"email": "x@novatech.example", "password": demo_password}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["code"] == unknown.json()["code"] == "INVALID_CREDENTIALS"
    for _ in range(4):
        client.post("/v1/auth/staff/password-login", json={"email": STAFF["email"], "password": "nope"})
    locked = client.post("/v1/auth/staff/password-login", json={"email": STAFF["email"], "password": demo_password})
    assert locked.status_code == 429 and locked.json()["code"] == "TOO_MANY_ATTEMPTS"


def test_demo_password_is_refused_in_production() -> None:
    with pytest.raises(ValueError, match="STAFF_DEMO_PASSWORD"):
        Settings(
            app_env="production", internal_api_keys="k" * 30, link_signing_secret="s" * 40, staff_demo_password="x"
        )
