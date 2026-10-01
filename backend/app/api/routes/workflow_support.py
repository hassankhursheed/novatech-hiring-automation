"""Internal endpoints called by n8n for interviews, offers and reporting. Pure computations: n8n persists the
results through the api.* database functions.

  POST /v1/links                    signed link for an email (candidate or staff action)
  POST /v1/interviews/evaluate      combined score and decision after an interview
  POST /v1/offers/document          render and store the offer letter PDF
  POST /v1/reports/daily-summary    report prose (optional AI behind a numeric guardrail) + metrics table
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool

from app.ai.report_writer import ReportWriter
from app.api.deps import get_hiring_repo, get_link_signer, get_report_writer, get_storage
from app.core.context import get_correlation_id
from app.core.errors import ConflictError, UnprocessableError
from app.core.faults import raise_if_injected
from app.core.links import LinkSigner
from app.core.logging import get_logger
from app.core.security import require_internal_api_key
from app.domain.evaluation import POLICY_VERSION, EvaluationConfigError, EvaluationSettings, evaluate
from app.domain.hiring_contracts import (
    DailySummaryRequest,
    DailySummaryResult,
    EvaluationRequest,
    EvaluationResult,
    LinkRequest,
    LinkResponse,
    MetricRow,
    MetricSection,
    OfferDocumentResult,
    OfferRef,
)
from app.domain.report_summary import REPORT_SECTIONS
from app.repositories.hiring import HiringRepository, as_date
from app.services.offer_document import OfferDocument, render_offer_letter
from app.services.storage import Storage, offer_document_key

log = get_logger(__name__)

router = APIRouter(dependencies=[Depends(require_internal_api_key)])

DEFAULT_PORTAL_URL = "http://localhost:5173"
_DOCUMENT_STATUSES = {"APPROVED", "SENT", "ACCEPTED", "DECLINED", "NEGOTIATION", "EXPIRED"}


@router.post("/v1/links", response_model=LinkResponse, tags=["links"], summary="Signed link for an email")
async def create_link(
    body: LinkRequest,
    signer: LinkSigner = Depends(get_link_signer),
    repo: HiringRepository = Depends(get_hiring_repo),
) -> LinkResponse:
    token, expires_at = signer.issue(body.purpose, str(body.subject_id), str(body.entity_id), body.expires_at)
    portal = str((await repo.settings(["company.portal_url"])).get("company.portal_url") or DEFAULT_PORTAL_URL)
    # The token goes in the URL fragment: browsers never send it to the portal server or to proxies.
    url = f"{portal.rstrip('/')}/{body.purpose.portal_path}#token={token}"
    return LinkResponse(purpose=body.purpose, url=url, token=token, expires_at=expires_at)


@router.post(
    "/v1/interviews/evaluate",
    response_model=EvaluationResult,
    tags=["interviews"],
    summary="Combined score (screening + interview) and decision",
)
async def evaluate_interview(
    request: Request, body: EvaluationRequest, repo: HiringRepository = Depends(get_hiring_repo)
) -> EvaluationResult:
    await raise_if_injected(request, "evaluate")
    inputs = await repo.evaluation_inputs(str(body.application_id))
    cfg = await repo.settings(
        [
            "evaluation.application_weight",
            "evaluation.interview_weight",
            "evaluation.select_min_score",
            "evaluation.review_min_score",
        ]
    )
    settings = EvaluationSettings(
        application_weight=float(cfg.get("evaluation.application_weight", 0.3)),
        interview_weight=float(cfg.get("evaluation.interview_weight", 0.7)),
        select_min_score=float(cfg.get("evaluation.select_min_score", 75)),
        review_min_score=float(cfg.get("evaluation.review_min_score", 60)),
    )
    app_score = float(inputs["application_score"]) if inputs["application_score"] is not None else None
    try:
        outcome = evaluate(app_score, float(inputs["interview_score"]), str(inputs["recommendation"]), settings)
    except EvaluationConfigError as exc:
        raise UnprocessableError(str(exc), code="INVALID_EVALUATION_SETTINGS") from exc

    log.info("interview_evaluated", application_id=inputs["application_id"], decision=outcome.decision.value)
    return EvaluationResult(
        application_id=inputs["application_id"],
        application_status=inputs["status"],
        decision=outcome.decision,
        final_score=outcome.final_score,
        application_score=app_score,
        interview_score=float(inputs["interview_score"]),
        recommendation=str(inputs["recommendation"]),
        weights={"application": outcome.application_weight, "interview": outcome.interview_weight},
        thresholds={"select_min_score": settings.select_min_score, "review_min_score": settings.review_min_score},
        reason=outcome.reason,
        policy_version=POLICY_VERSION,
    )


async def build_offer_document(
    repo: HiringRepository, storage: Storage, offer_id: str
) -> tuple[dict[str, Any], OfferDocument, str]:
    offer = await repo.offer_snapshot(offer_id)
    if offer["status"] not in _DOCUMENT_STATUSES:
        raise ConflictError(f"offer {offer['offer_code']} is {offer['status']}", code="OFFER_NOT_APPROVED")
    company, careers_email = await repo.daily_report_context()
    issued_on = as_date(offer["issued_on"])
    document = await run_in_threadpool(
        render_offer_letter, offer, company=company, careers_email=careers_email, issued_on=issued_on
    )
    key = offer_document_key(offer["offer_code"], int(offer["revision"]), issued_on.year)
    await run_in_threadpool(storage.save, key, document.content)
    return offer, document, key


@router.post(
    "/v1/offers/document", response_model=OfferDocumentResult, tags=["offers"], summary="Render the offer letter PDF"
)
async def generate_offer_document(
    request: Request,
    body: OfferRef,
    repo: HiringRepository = Depends(get_hiring_repo),
    storage: Storage = Depends(get_storage),
) -> OfferDocumentResult:
    await raise_if_injected(request, "document")
    offer, document, key = await build_offer_document(repo, storage, str(body.offer_id))
    return OfferDocumentResult(
        offer_id=str(body.offer_id),
        offer_code=offer["offer_code"],
        document_key=key,
        sha256=document.sha256,
        size_bytes=len(document.content),
    )


@router.post(
    "/v1/reports/daily-summary",
    response_model=DailySummaryResult,
    tags=["reports"],
    summary="Daily report text (AI prose is rejected if it contains any number not in the metrics)",
)
async def daily_summary(
    body: DailySummaryRequest,
    writer: ReportWriter = Depends(get_report_writer),
    repo: HiringRepository = Depends(get_hiring_repo),
) -> DailySummaryResult:
    company, _ = await repo.daily_report_context()
    result = await writer.write(body.metrics, body.report_date, company=company, correlation_id=get_correlation_id())
    sections = [
        MetricSection(
            title=title, rows=[MetricRow(key=key, label=label, value=body.metrics.get(key)) for key, label in rows]
        )
        for title, rows in REPORT_SECTIONS
    ]
    return DailySummaryResult(
        report_date=body.report_date,
        summary=result.summary,
        summary_source=result.source,
        fallback_reason=result.fallback_reason,
        sections=sections,
    )
