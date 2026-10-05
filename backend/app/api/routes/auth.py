"""Passwordless staff sign-in for the HR portal.

1. POST /v1/auth/staff/login-link {email}  -> always 202 (no account enumeration). For an active staff member a
   single-use sign-in link (15 minutes) is emailed through n8n (WF-09 -> SWF-02).
2. The portal opens /staff/login#token=... and POSTs /v1/auth/staff/session with that token. The token is consumed
   exactly once (api.consume_link_token) and exchanged for a session token (8 hours, purpose STAFF_SESSION).
3. Every /v1/staff/* call sends the session token; the database re-checks the staff member on every action.

A company with an identity provider replaces steps 1-2 with OIDC; the session and everything after stay the same.

Demo installations can also set STAFF_DEMO_PASSWORD: every active staff account then signs in with that shared
password (POST /v1/auth/staff/password-login). It is off by default and refused when APP_ENV=production.
"""

import hashlib
import hmac
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from xml.sax.saxutils import escape

from fastapi import APIRouter, BackgroundTasks, Depends, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.api.deps import get_hiring_repo, get_link_signer, get_n8n, get_staff_directory, link_claims
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, TooManyRequestsError, UnauthorizedError
from app.core.links import LinkClaims, LinkPurpose, LinkSigner
from app.core.logging import get_logger
from app.repositories.hiring import HiringRepository, StaffDirectory
from app.services.n8n import N8nClient

log = get_logger(__name__)

router = APIRouter(prefix="/v1/auth/staff", tags=["staff sign-in"])

_LAST_LINK: dict[str, float] = {}  # per-process throttle; the reverse proxy adds a global rate limit
_THROTTLE_SECONDS = 60
GENERIC_ANSWER = "If this address belongs to an active staff account, a sign-in link is on its way."


class LoginLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: EmailStr


def _sign_in_email(name: str, url: str, minutes: int, company: str) -> str:
    return (
        "<div style='font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.55;color:#1d2939'>"
        f"<p>Hi {escape(name)},</p><p>Use this link to sign in to the {escape(company)} hiring portal. "
        f"It works once and expires in {minutes} minutes.</p>"
        f"<p><a href='{escape(url)}' style='background:#1570ef;color:#fff;padding:10px 16px;border-radius:6px;"
        "text-decoration:none'>Sign in</a></p>"
        "<p style='color:#667085;font-size:12px'>If you did not ask to sign in, you can ignore this email.</p></div>"
    )


@router.post("/login-link", status_code=status.HTTP_202_ACCEPTED, summary="Email a single-use sign-in link")
async def request_login_link(
    body: LoginLinkRequest,
    background: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    signer: LinkSigner = Depends(get_link_signer),
    directory: StaffDirectory = Depends(get_staff_directory),
    repo: HiringRepository = Depends(get_hiring_repo),
    n8n: N8nClient = Depends(get_n8n),
) -> dict[str, str]:
    email = str(body.email).lower()
    now = time.monotonic()
    staff = await directory.by_email(email)
    if staff is None or not staff["is_active"]:
        log.info("staff_login_link_ignored", reason="unknown or inactive address")
        return {"status": GENERIC_ANSWER}
    if now - _LAST_LINK.get(email, 0.0) < _THROTTLE_SECONDS:
        log.info("staff_login_link_throttled", staff_id=staff["staff_id"])
        return {"status": GENERIC_ANSWER}
    _LAST_LINK[email] = now

    minutes = settings.staff_login_link_minutes
    token, expires_at = signer.issue(
        LinkPurpose.STAFF_LOGIN, staff["staff_id"], staff["staff_id"], datetime.now(UTC) + timedelta(minutes=minutes)
    )
    values = await repo.settings(["company.portal_url", "company.name"])
    portal = str(values.get("company.portal_url") or "http://localhost:5173").rstrip("/")
    company = str(values.get("company.name") or "NovaTech")
    url = f"{portal}/{LinkPurpose.STAFF_LOGIN.portal_path}#token={token}"
    background.add_task(
        n8n.notify,
        {
            "dedupe_key": f"staff.login:{hashlib.sha256(token.encode()).hexdigest()[:32]}",
            "template_key": "staff.login_link",
            "recipient": staff["email"],
            "subject": f"Your sign-in link for the {company} hiring portal",
            "html": _sign_in_email(staff["full_name"], url, minutes, company),
            "entity_type": "STAFF",
            "entity_id": staff["staff_id"],
        },
    )
    log.info("staff_login_link_sent", staff_id=staff["staff_id"], expires_at=expires_at.isoformat())
    return {"status": GENERIC_ANSWER}


@router.post("/session", summary="Exchange a sign-in link (once) for a portal session")
async def create_session(
    claims: LinkClaims = Depends(link_claims(LinkPurpose.STAFF_LOGIN)),
    settings: Settings = Depends(get_settings),
    signer: LinkSigner = Depends(get_link_signer),
    directory: StaffDirectory = Depends(get_staff_directory),
) -> dict[str, Any]:
    staff = await directory.profile(claims.subject_id)
    if staff is None or not staff["is_active"]:
        raise UnauthorizedError("this account is not active", code="UNKNOWN_STAFF_ACTOR")
    if not await directory.consume_link(
        claims.token_id, LinkPurpose.STAFF_LOGIN.value, claims.subject_id, claims.expires_at, claims.ctx()
    ):
        raise UnauthorizedError("this sign-in link was already used; request a new one", code="LINK_ALREADY_USED")
    log.info("staff_session_started", staff_id=claims.subject_id, method="email_link")
    return _session(signer, settings, dict(staff))


def _session(signer: LinkSigner, settings: Settings, staff: dict[str, Any]) -> dict[str, Any]:
    token, expires_at = signer.issue(
        LinkPurpose.STAFF_SESSION,
        staff["staff_id"],
        staff["staff_id"],
        datetime.now(UTC) + timedelta(hours=settings.staff_session_hours),
    )
    return {"token": token, "expires_at": expires_at, "staff": staff}


# ---- demo password sign-in (development only) ----------------------------------------------------------
class PasswordLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


_FAILURES: dict[str, list[float]] = {}
_MAX_FAILURES = 5
_FAILURE_WINDOW = 600.0


@router.get("/options", summary="Which sign-in methods this installation offers")
async def sign_in_options(
    settings: Settings = Depends(get_settings), directory: StaffDirectory = Depends(get_staff_directory)
) -> dict[str, Any]:
    if not settings.demo_password:
        return {"email_link": True, "password": False}
    # Demo installations offer a ready-made HR login (the first active HR admin) so testers can sign in in one click.
    # Production never reaches this branch: STAFF_DEMO_PASSWORD is refused when APP_ENV=production.
    staff = await directory.active_staff()
    hr = next((s for s in staff if "HR_ADMIN" in s["roles"]), staff[0] if staff else None)
    demo_login = (
        {"email": hr["email"], "password": settings.demo_password, "full_name": hr["full_name"]} if hr else None
    )
    return {"email_link": True, "password": True, "demo_login": demo_login}


@router.post("/password-login", summary="Demo sign-in with the shared STAFF_DEMO_PASSWORD (disabled in production)")
async def password_login(
    body: PasswordLoginRequest,
    settings: Settings = Depends(get_settings),
    signer: LinkSigner = Depends(get_link_signer),
    directory: StaffDirectory = Depends(get_staff_directory),
) -> dict[str, Any]:
    expected = settings.demo_password
    if not expected:
        raise NotFoundError(
            "password sign-in is not enabled; use the email sign-in link", code="PASSWORD_LOGIN_DISABLED"
        )
    email = str(body.email).lower()
    now = time.monotonic()
    recent = [t for t in _FAILURES.get(email, []) if now - t < _FAILURE_WINDOW]
    if len(recent) >= _MAX_FAILURES:
        raise TooManyRequestsError("too many failed attempts; wait 10 minutes and try again")
    staff = await directory.by_email(email)
    password_ok = hmac.compare_digest(body.password.encode(), expected.encode())
    if not password_ok or staff is None or not staff["is_active"]:
        _FAILURES[email] = [*recent, now]
        log.warning("staff_password_login_failed", reason="bad password" if staff else "unknown address")
        raise UnauthorizedError("email or password is incorrect", code="INVALID_CREDENTIALS")
    _FAILURES.pop(email, None)
    profile = await directory.profile(staff["staff_id"])
    log.info("staff_session_started", staff_id=staff["staff_id"], method="demo_password")
    return _session(signer, settings, dict(profile or staff))
