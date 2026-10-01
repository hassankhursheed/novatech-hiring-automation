"""Actions people take from an email link (candidates, interviewers, approvers).

Authentication is the signed link token (`Authorization: Bearer <token>`), bound to one person, one entity and
one purpose. The person is the actor of every database call, so the database checks that they own the
application, that the state still allows the action, and that the approver is allowed to approve.

After a successful write the dispatcher is kicked so the next step (calendar event, offer letter, onboarding)
starts within seconds.

Candidate:    GET  /v1/portal/interview            POST /v1/portal/interview/confirm   POST /v1/portal/interview/cancel
              GET  /v1/portal/offer                GET  /v1/portal/offer/document      POST /v1/portal/offer/respond
Interviewer:  GET  /v1/portal/feedback             POST /v1/portal/feedback
Approver:     GET  /v1/portal/approval             POST /v1/portal/approval
"""

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from app.api.deps import get_hiring_repo, get_n8n, get_storage, link_claims
from app.api.routes.workflow_support import build_offer_document
from app.core.errors import ConflictError, NotFoundError
from app.core.links import LinkClaims, LinkPurpose
from app.domain.hiring_contracts import (
    ApprovalRequest,
    CancelRequest,
    FeedbackRequest,
    OfferResponseRequest,
    SlotChoice,
)
from app.repositories.hiring import HiringRepository
from app.services.n8n import N8nClient
from app.services.storage import Storage

router = APIRouter(prefix="/v1/portal", tags=["portal (signed links)"])

slot_link = link_claims(LinkPurpose.INTERVIEW_SLOT)
offer_link = link_claims(LinkPurpose.OFFER_RESPONSE)
feedback_link = link_claims(LinkPurpose.INTERVIEW_FEEDBACK)
approval_link = link_claims(LinkPurpose.OFFER_APPROVAL)


def _result(row: Any) -> dict[str, Any]:
    return dict(row)


# ---- candidate: interview -------------------------------------------------------------------------------
@router.get("/interview", summary="Interview invitation with the open slots")
async def view_interview(
    claims: LinkClaims = Depends(slot_link), repo: HiringRepository = Depends(get_hiring_repo)
) -> dict[str, Any]:
    return await repo.interview_for_candidate(claims.entity_id, claims.subject_id)


@router.post("/interview/confirm", summary="Book one of the offered slots")
async def confirm_interview(
    body: SlotChoice,
    background: BackgroundTasks,
    claims: LinkClaims = Depends(slot_link),
    repo: HiringRepository = Depends(get_hiring_repo),
    n8n: N8nClient = Depends(get_n8n),
) -> dict[str, Any]:
    row = await repo.confirm_slot(claims.entity_id, str(body.slot_id), claims.ctx())
    if row["changed"]:
        background.add_task(n8n.kick, "interview confirmed")
    return _result(row)


@router.post("/interview/cancel", summary="Cancel a booked interview (the slot is released)")
async def cancel_interview(
    body: CancelRequest,
    background: BackgroundTasks,
    claims: LinkClaims = Depends(slot_link),
    repo: HiringRepository = Depends(get_hiring_repo),
    n8n: N8nClient = Depends(get_n8n),
) -> dict[str, Any]:
    await repo.interview_for_candidate(claims.entity_id, claims.subject_id)  # ownership check before writing
    row = await repo.cancel_interview(claims.entity_id, body.reason, claims.ctx())
    if row["changed"]:
        background.add_task(n8n.kick, "interview cancelled")
    return _result(row)


# ---- candidate: offer -----------------------------------------------------------------------------------
def _candidate_offer_view(offer: dict[str, Any]) -> dict[str, Any]:
    """What a candidate may see: the terms, never internal scores, approvals or approvers."""
    app = offer["application"]
    manager = offer.get("reporting_manager") or {}
    return {
        "offer_code": offer["offer_code"],
        "revision": offer["revision"],
        "status": offer["status"],
        "position_title": app["position"]["title"],
        "department": offer["department"],
        "monthly_salary": offer["monthly_salary"],
        "currency": str(offer["currency"]).strip(),
        "joining_date": offer["joining_date"],
        "probation_months": offer["probation_months"],
        "reporting_manager": manager.get("full_name"),
        "expires_at": offer["expires_at"],
        "candidate_response": offer["candidate_response"],
        "candidate_first_name": str(app["candidate"]["full_name"]).split(" ")[0],
        "company": app["company"]["name"],
        "document_available": offer["status"] in {"SENT", "ACCEPTED", "DECLINED", "NEGOTIATION", "EXPIRED"},
    }


@router.get("/offer", summary="Offer terms for the candidate")
async def view_offer(
    claims: LinkClaims = Depends(offer_link), repo: HiringRepository = Depends(get_hiring_repo)
) -> dict[str, Any]:
    return _candidate_offer_view(await repo.offer_for_candidate(claims.entity_id, claims.subject_id))


@router.get("/offer/document", summary="Offer letter PDF", response_class=Response)
async def offer_document(
    claims: LinkClaims = Depends(offer_link),
    repo: HiringRepository = Depends(get_hiring_repo),
    storage: Storage = Depends(get_storage),
) -> Response:
    offer = await repo.offer_for_candidate(claims.entity_id, claims.subject_id)
    if offer["status"] in {"PENDING_APPROVAL", "APPROVED", "REJECTED_BY_APPROVER", "SUPERSEDED", "WITHDRAWN"}:
        raise NotFoundError("this offer letter is not available", code="OFFER_DOCUMENT_UNAVAILABLE")
    content = (
        await run_in_threadpool(storage.read, offer["document_storage_key"]) if offer["document_storage_key"] else None
    )
    if content is None:  # storage lost or migrated: the letter is deterministic, so re-render it
        _, document, _ = await build_offer_document(repo, storage, claims.entity_id)
        content = document.content
    return Response(
        content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{offer["offer_code"]}.pdf"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/offer/respond", summary="Accept, decline or ask to negotiate")
async def respond_to_offer(
    body: OfferResponseRequest,
    background: BackgroundTasks,
    claims: LinkClaims = Depends(offer_link),
    repo: HiringRepository = Depends(get_hiring_repo),
    n8n: N8nClient = Depends(get_n8n),
) -> dict[str, Any]:
    row = await repo.respond_to_offer(claims.entity_id, body.response.value, body.message, claims.ctx())
    if row["changed"]:
        background.add_task(n8n.kick, f"offer {body.response.value.lower()}")
    return _result(row)


# ---- interviewer: feedback ------------------------------------------------------------------------------
@router.get("/feedback", summary="Interview details for the scorecard")
async def view_feedback_form(
    claims: LinkClaims = Depends(feedback_link), repo: HiringRepository = Depends(get_hiring_repo)
) -> dict[str, Any]:
    view = await repo.interview_for_staff(claims.entity_id)
    if view["interviewer_id"] != claims.subject_id:
        raise NotFoundError("interview not found", code="INTERVIEW_NOT_FOUND")
    return view


@router.post("/feedback", summary="Submit the interview scorecard")
async def submit_feedback(
    body: FeedbackRequest,
    background: BackgroundTasks,
    claims: LinkClaims = Depends(feedback_link),
    repo: HiringRepository = Depends(get_hiring_repo),
    n8n: N8nClient = Depends(get_n8n),
) -> dict[str, Any]:
    row = await repo.submit_feedback(claims.entity_id, body.model_dump(mode="json"), claims.ctx())
    if not row["replayed"]:
        background.add_task(n8n.kick, "feedback submitted")
    return _result(row)


# ---- approver: offer approval ---------------------------------------------------------------------------
@router.get("/approval", summary="Offer details for the approver")
async def view_approval(
    claims: LinkClaims = Depends(approval_link), repo: HiringRepository = Depends(get_hiring_repo)
) -> dict[str, Any]:
    offer = await repo.offer_snapshot(claims.entity_id)
    app = offer["application"]
    approvers = {a["id"] for a in offer.get("next_approvers") or []}
    return {
        "offer_code": offer["offer_code"],
        "revision": offer["revision"],
        "status": offer["status"],
        "candidate_name": app["candidate"]["full_name"],
        "application_code": app["application_code"],
        "position_title": app["position"]["title"],
        "department": offer["department"],
        "monthly_salary": offer["monthly_salary"],
        "currency": str(offer["currency"]).strip(),
        "expected_salary": app["expected_salary"],
        "joining_date": offer["joining_date"],
        "probation_months": offer["probation_months"],
        "application_score": app["application_score"],
        "interview_score": app["interview_score"],
        "final_score": app["final_score"],
        "required_approval_levels": offer["required_approval_levels"],
        "approval_threshold_applied": offer["approval_threshold_applied"],
        "approvals": offer["approvals"],
        "next_level": offer["next_level"],
        "can_decide": offer["status"] == "PENDING_APPROVAL" and claims.subject_id in approvers,
    }


@router.post("/approval", summary="Approve or reject the next pending level")
async def decide_approval(
    body: ApprovalRequest,
    background: BackgroundTasks,
    claims: LinkClaims = Depends(approval_link),
    repo: HiringRepository = Depends(get_hiring_repo),
    n8n: N8nClient = Depends(get_n8n),
) -> dict[str, Any]:
    level = body.level
    if level is None:
        offer = await repo.offer_snapshot(claims.entity_id)
        if offer["next_level"] is None:
            raise ConflictError(f"offer {offer['offer_code']} has no pending approval", code="OFFER_NOT_PENDING")
        level = int(offer["next_level"])
    row = await repo.decide_approval(claims.entity_id, level, body.decision.value, body.reason, claims.ctx())
    if row["changed"]:
        background.add_task(n8n.kick, f"offer approval level {level} {body.decision.value.lower()}")
    return _result(row)
