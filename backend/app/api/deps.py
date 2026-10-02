"""FastAPI dependencies. Long-lived resources are created in the app lifespan and stored on app.state."""

import uuid
from collections.abc import Awaitable, Callable

from fastapi import Depends, Header, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.ai.analyzer import CandidateAnalyzer
from app.ai.report_writer import ReportWriter
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.errors import UnauthorizedError
from app.core.links import LinkClaims, LinkPurpose, LinkSigner
from app.core.security import require_internal_api_key
from app.repositories.hiring import HiringRepository, StaffDirectory
from app.repositories.reference import ReferenceRepository
from app.services.n8n import N8nClient
from app.services.storage import Storage


def get_db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def get_reference_repo(request: Request) -> ReferenceRepository:
    repo: ReferenceRepository = request.app.state.reference_repo
    return repo


def get_hiring_repo(request: Request) -> HiringRepository:
    repo: HiringRepository = request.app.state.hiring_repo
    return repo


def get_staff_directory(request: Request) -> StaffDirectory:
    directory: StaffDirectory = request.app.state.staff_directory
    return directory


def get_storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


def get_analyzer(request: Request) -> CandidateAnalyzer:
    analyzer: CandidateAnalyzer = request.app.state.analyzer
    return analyzer


def get_report_writer(request: Request) -> ReportWriter:
    writer: ReportWriter = request.app.state.report_writer
    return writer


def get_link_signer(request: Request) -> LinkSigner:
    signer: LinkSigner = request.app.state.link_signer
    return signer


def get_n8n(request: Request) -> N8nClient:
    client: N8nClient = request.app.state.n8n
    return client


_bearer = HTTPBearer(auto_error=False, description="Signed link token from the email")


def link_claims(*purposes: LinkPurpose) -> Callable[..., Awaitable[LinkClaims]]:
    """Dependency that accepts only link tokens issued for one of `purposes`."""
    allowed = set(purposes)

    async def dependency(
        request: Request, credentials: HTTPAuthorizationCredentials | None = Security(_bearer)
    ) -> LinkClaims:
        if credentials is None or not credentials.credentials:
            raise UnauthorizedError("missing link token", code="LINK_TOKEN_MISSING")
        return get_link_signer(request).verify(credentials.credentials, allowed)

    return dependency


async def staff_actor(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Header(default=None, include_in_schema=False),
    x_staff_id: str | None = Header(default=None, description="Staff member id (internal tools with X-API-Key)"),
    settings: Settings = Depends(get_settings),
) -> str:
    """The staff member acting. Two ways in:

    * the HR portal: `Authorization: Bearer <session>` from the email sign-in (purpose STAFF_SESSION);
    * internal tools and server-side callers: a valid `X-API-Key` plus `X-Staff-Id`.
    Either way the database re-checks that the staff member is active and allowed to act.
    """
    if credentials is not None and credentials.credentials:
        return get_link_signer(request).verify(credentials.credentials, {LinkPurpose.STAFF_SESSION}).subject_id
    require_internal_api_key(x_api_key, settings)
    if not x_staff_id:
        raise UnauthorizedError("sign in, or send X-API-Key with X-Staff-Id", code="STAFF_AUTH_REQUIRED")
    try:
        return str(uuid.UUID(x_staff_id))
    except ValueError as exc:
        raise UnauthorizedError("X-Staff-Id must be a staff member id", code="STAFF_ID_INVALID") from exc
