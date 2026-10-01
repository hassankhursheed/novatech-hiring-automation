"""Intake endpoints.

Public (browser):  GET  /v1/public/positions, POST /v1/public/cv
Internal (n8n):    POST /v1/intake/validate   - pure: validates + normalises, never writes
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel

from app.api.deps import get_reference_repo, get_storage
from app.core.config import Settings, get_settings
from app.core.context import get_correlation_id
from app.core.errors import BadRequestError, PayloadTooLargeError
from app.core.faults import raise_if_injected
from app.core.logging import get_logger
from app.core.security import require_internal_api_key
from app.domain.contracts import ApplicationSubmission, ValidationResult
from app.domain.validation import CvInfo, ValidationContext, validate_submission
from app.repositories.reference import ReferenceRepository
from app.services import cv_extraction
from app.services.storage import Storage, new_cv_key, validate_cv_key

log = get_logger(__name__)

public_router = APIRouter(prefix="/v1/public", tags=["public"])
router = APIRouter(prefix="/v1/intake", tags=["intake"], dependencies=[Depends(require_internal_api_key)])


class PositionOut(BaseModel):
    code: str
    title: str
    department: str
    employment_type: str
    min_experience_years: float
    description: str | None


class CvUploadResult(BaseModel):
    cv_ref: str
    file_type: str
    size_bytes: int
    pages: int | None
    text_extracted: bool


@public_router.get("/positions", response_model=list[PositionOut], summary="Open positions for the careers page")
async def list_positions(repo: ReferenceRepository = Depends(get_reference_repo)) -> list[dict[str, object]]:
    return await repo.open_positions_public()


@public_router.post("/cv", response_model=CvUploadResult, status_code=201, summary="Upload a CV (PDF or DOCX)")
async def upload_cv(
    request: Request,
    file: UploadFile = File(...),
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> CvUploadResult:
    await raise_if_injected(request, "cv")
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise PayloadTooLargeError(f"CV must be at most {settings.max_upload_mb} MB", code="CV_TOO_LARGE")
    if not data:
        raise BadRequestError("the uploaded file is empty", code="CV_EMPTY")
    try:
        document = cv_extraction.extract(data)
    except cv_extraction.UnsupportedDocumentError as exc:
        raise BadRequestError(str(exc), code="CV_UNSUPPORTED") from exc

    key = new_cv_key(document.extension)
    storage.save(key, data)
    storage.save_text(key, document.text)
    log.info("cv_uploaded", cv_ref=key, file_type=document.extension, size=len(data), text_chars=len(document.text))
    return CvUploadResult(
        cv_ref=key,
        file_type=document.extension,
        size_bytes=len(data),
        pages=document.pages,
        text_extracted=bool(document.text.strip()),
    )


@router.post("/validate", response_model=ValidationResult, summary="Validate and normalise an application")
async def validate_application(
    request: Request,
    submission: ApplicationSubmission,
    repo: ReferenceRepository = Depends(get_reference_repo),
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> ValidationResult:
    await raise_if_injected(request, "validate")

    config = await repo.settings(["company.currency", "company.timezone"])
    timezone = str(config.get("company.timezone") or settings.company_timezone)
    ctx = ValidationContext(
        positions=await repo.positions(),
        skill_aliases=await repo.skill_aliases(),
        default_phone_region=settings.default_phone_region,
        default_currency=str(config.get("company.currency") or "PKR"),
        today=datetime.now(ZoneInfo(timezone)).date(),
    )

    cv: CvInfo | None = None
    if submission.cv_ref:
        try:
            key = validate_cv_key(submission.cv_ref)
            cv = CvInfo(exists=storage.exists(key), text=storage.read_text(key))
        except ValueError:
            cv = CvInfo(exists=False, text=None)

    result = validate_submission(submission, ctx, cv, correlation_id=get_correlation_id())
    log.info("application_validated", outcome=result.outcome.value, issues=[i.code for i in result.issues])
    return result
