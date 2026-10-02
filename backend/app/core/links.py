"""Signed, expiring links for people who act from an email: candidates, interviewers and approvers.

A link token is a JWT (HS256). It names exactly one subject (a candidate or a staff member), one entity (an
interview or an offer) and one purpose. Because the token is the candidate's only credential:
  * it expires, never later than the business deadline and never later than LINK_MAX_TTL_DAYS;
  * it is bound to one purpose (the JWT audience), so an offer link cannot confirm an interview slot;
  * the database still checks ownership and state, so a valid token for a closed offer is refused.
The portal receives the token in the URL fragment, which browsers never send to servers or proxies. It then
calls this API with `Authorization: Bearer <token>`.
"""

import enum
import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.config import Settings
from app.core.errors import AppError, BadRequestError, UnauthorizedError
from app.core.logging import get_logger

log = get_logger(__name__)

ISSUER = "novatech-hiring"
_ALGORITHM = "HS256"


class LinkPurpose(enum.StrEnum):
    INTERVIEW_SLOT = "INTERVIEW_SLOT"  # candidate picks or cancels an interview slot
    OFFER_RESPONSE = "OFFER_RESPONSE"  # candidate views, downloads and answers an offer
    INTERVIEW_FEEDBACK = "INTERVIEW_FEEDBACK"  # interviewer submits the scorecard
    OFFER_APPROVAL = "OFFER_APPROVAL"  # approver approves or rejects one offer level
    STAFF_LOGIN = "STAFF_LOGIN"  # single-use sign-in link emailed to a staff member (15 minutes)
    STAFF_SESSION = "STAFF_SESSION"  # portal session issued in exchange for a sign-in link (never emailed)

    @property
    def actor_type(self) -> str:
        return "CANDIDATE" if self in {LinkPurpose.INTERVIEW_SLOT, LinkPurpose.OFFER_RESPONSE} else "STAFF"

    @property
    def portal_path(self) -> str:
        return {
            LinkPurpose.INTERVIEW_SLOT: "candidate/interview",
            LinkPurpose.OFFER_RESPONSE: "candidate/offer",
            LinkPurpose.INTERVIEW_FEEDBACK: "staff/feedback",
            LinkPurpose.OFFER_APPROVAL: "staff/approval",
            LinkPurpose.STAFF_LOGIN: "staff/login",
            LinkPurpose.STAFF_SESSION: "staff",
        }[self]


@dataclass(frozen=True)
class LinkClaims:
    purpose: LinkPurpose
    subject_id: str
    entity_id: str
    expires_at: datetime
    token_id: str

    def ctx(self) -> dict[str, Any]:
        """Database execution context: the person who holds the link is the actor."""
        return {
            "actor_type": self.purpose.actor_type,
            "actor_id": self.subject_id,
            "workflow_name": "PORTAL",
            "workflow_version": "1.0.0",
            "execution_id": self.token_id,
        }


class LinkExpiredError(AppError):
    status_code = 410
    code = "LINK_EXPIRED"
    title = "Link expired"


class LinkSigner:
    def __init__(self, secret: str, max_ttl: timedelta) -> None:
        if not secret:
            raise ValueError("link signing secret must not be empty")
        self._secret = secret
        self._max_ttl = max_ttl

    @classmethod
    def from_settings(cls, settings: Settings) -> "LinkSigner":
        if settings.link_signing_secret and settings.link_signing_secret.get_secret_value():
            secret = settings.link_signing_secret.get_secret_value()
        else:
            # Development only (production requires LINK_SIGNING_SECRET): derive a stable secret, so tokens
            # stay valid across workers and restarts.
            base = settings.internal_api_keys[0].get_secret_value() if settings.internal_api_keys else "dev"
            secret = hmac.new(base.encode(), b"novatech-link-signing", hashlib.sha256).hexdigest()
            log.warning("link_secret_derived", reason="LINK_SIGNING_SECRET is not set; using a derived dev secret")
        return cls(secret, timedelta(days=settings.link_max_ttl_days))

    def issue(
        self,
        purpose: LinkPurpose,
        subject_id: str,
        entity_id: str,
        expires_at: datetime | None = None,
        *,
        now: datetime | None = None,
    ) -> tuple[str, datetime]:
        now = now or datetime.now(UTC)
        cap = now + self._max_ttl
        exp = min(expires_at, cap) if expires_at else cap
        if exp <= now:
            raise BadRequestError("the link would already be expired", code="LINK_EXPIRY_IN_PAST")
        exp = exp.replace(microsecond=0)
        claims = {
            "iss": ISSUER,
            "aud": purpose.value,
            "sub": subject_id,
            "eid": entity_id,
            "jti": uuid.uuid4().hex,
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
        }
        return jwt.encode(claims, self._secret, algorithm=_ALGORITHM), exp

    def verify(self, token: str, allowed: set[LinkPurpose]) -> LinkClaims:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[_ALGORITHM],
                issuer=ISSUER,
                audience=[p.value for p in allowed],
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
                leeway=30,
            )
        except jwt.ExpiredSignatureError as exc:
            raise LinkExpiredError("this link has expired; please contact the recruitment team") from exc
        except jwt.InvalidTokenError as exc:
            raise UnauthorizedError("this link is not valid", code="LINK_INVALID") from exc
        try:
            return LinkClaims(
                purpose=LinkPurpose(claims["aud"]),
                subject_id=str(uuid.UUID(claims["sub"])),
                entity_id=str(uuid.UUID(claims["eid"])),
                expires_at=datetime.fromtimestamp(claims["exp"], UTC),
                token_id=str(claims.get("jti", "")),
            )
        except (KeyError, ValueError) as exc:
            raise UnauthorizedError("this link is not valid", code="LINK_INVALID") from exc
