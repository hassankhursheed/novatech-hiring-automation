"""Staff actions and read models for the HR portal and internal tools.

Authentication (see deps.staff_actor): the portal sends the session token from the passwordless email sign-in;
internal tools send X-API-Key plus X-Staff-Id. The browser never holds the API key. The staff member is the actor of
every database call, so roles, segregation of duties and the state machine are enforced by the database.
"""

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Query

from app.api.deps import get_hiring_repo, get_n8n, get_staff_directory, staff_actor
from app.core.context import get_correlation_id
from app.core.errors import ConflictError, NotFoundError, UnauthorizedError
from app.domain.hiring_contracts import (
    ApprovalRequest,
    CancelRequest,
    FeedbackRequest,
    OfferTerms,
    TransitionRequest,
)
from app.repositories.hiring import HiringRepository, StaffDirectory
from app.services.n8n import N8nClient

router = APIRouter(prefix="/v1/staff", tags=["staff"])

# Read models exposed to staff tools (fixed allow-list of reporting views).
QUEUES = {
    "screening-review": "v_manual_review_queue",
    "offer-approvals": "v_pending_offer_approvals",
    "interview-feedback": "v_pending_interview_feedback",
    "open-offers": "v_open_offers",
    "overdue-onboarding": "v_overdue_onboarding_tasks",
    "errors": "v_error_queue",
    "scheduled-actions": "v_scheduled_actions",
    "pipeline": "v_pipeline_by_status",
    "workflow-health": "v_workflow_health_today",
}

UuidPath = Annotated[str, Path(pattern=r"^[0-9a-fA-F-]{36}$")]


async def staff_ctx(
    actor: str = Depends(staff_actor), repo: HiringRepository = Depends(get_hiring_repo)
) -> dict[str, Any]:
    if not await repo.staff_exists(actor):
        raise UnauthorizedError("unknown or inactive staff member", code="UNKNOWN_STAFF_ACTOR")
    return {
        "actor_type": "STAFF",
        "actor_id": actor,
        "workflow_name": "STAFF-API",
        "workflow_version": "1.0.0",
        "execution_id": get_correlation_id() or "staff-api",
    }


Ctx = dict[str, Any]


@router.post("/applications/{application_id}/transition", summary="Review decision (e.g. shortlist from review)")
async def transition_application(
    body: TransitionRequest,
    background: BackgroundTasks,
    application_id: UuidPath,
    ctx: Ctx = Depends(staff_ctx),
    repo: HiringRepository = Depends(get_hiring_repo),
    n8n: N8nClient = Depends(get_n8n),
) -> dict[str, Any]:
    row = await repo.transition(application_id, body.to_status, body.reason, ctx, body.expected_from)
    if row["changed"]:
        background.add_task(n8n.kick, f"staff moved application to {body.to_status}")
    return dict(row)


@router.post("/applications/{application_id}/withdraw", summary="Close an application (candidate withdrew)")
async def withdraw_application(
    body: CancelRequest,
    application_id: UuidPath,
    ctx: Ctx = Depends(staff_ctx),
    repo: HiringRepository = Depends(get_hiring_repo),
) -> dict[str, Any]:
    return dict(await repo.withdraw(application_id, body.reason, ctx))


@router.post("/applications/{application_id}/offers", summary="Draft or revise an offer")
async def create_offer(
    body: OfferTerms,
    background: BackgroundTasks,
    application_id: UuidPath,
    ctx: Ctx = Depends(staff_ctx),
    repo: HiringRepository = Depends(get_hiring_repo),
    n8n: N8nClient = Depends(get_n8n),
) -> dict[str, Any]:
    row = await repo.create_offer(application_id, body.model_dump(mode="json", exclude_none=True), ctx)
    if row["created"]:
        background.add_task(n8n.kick, "offer drafted")
    return dict(row)


@router.post("/offers/{offer_id}/approval", summary="Approve or reject an offer level")
async def decide_offer_approval(
    body: ApprovalRequest,
    background: BackgroundTasks,
    offer_id: UuidPath,
    ctx: Ctx = Depends(staff_ctx),
    repo: HiringRepository = Depends(get_hiring_repo),
    n8n: N8nClient = Depends(get_n8n),
) -> dict[str, Any]:
    level = body.level
    if level is None:
        offer = await repo.offer_snapshot(offer_id)
        if offer["next_level"] is None:
            raise ConflictError(f"offer {offer['offer_code']} has no pending approval", code="OFFER_NOT_PENDING")
        level = int(offer["next_level"])
    row = await repo.decide_approval(offer_id, level, body.decision.value, body.reason, ctx)
    if row["changed"]:
        background.add_task(n8n.kick, "offer approval decided")
    return dict(row)


@router.post("/offers/{offer_id}/close-negotiation", summary="End a negotiation without agreement")
async def close_negotiation(
    body: CancelRequest,
    background: BackgroundTasks,
    offer_id: UuidPath,
    ctx: Ctx = Depends(staff_ctx),
    repo: HiringRepository = Depends(get_hiring_repo),
    n8n: N8nClient = Depends(get_n8n),
) -> dict[str, Any]:
    row = await repo.close_negotiation(offer_id, body.reason, ctx)
    if row["changed"]:
        background.add_task(n8n.kick, "negotiation closed")
    return dict(row)


@router.post("/interviews/{interview_id}/feedback", summary="Submit interview feedback")
async def submit_feedback(
    body: FeedbackRequest,
    background: BackgroundTasks,
    interview_id: UuidPath,
    ctx: Ctx = Depends(staff_ctx),
    repo: HiringRepository = Depends(get_hiring_repo),
    n8n: N8nClient = Depends(get_n8n),
) -> dict[str, Any]:
    row = await repo.submit_feedback(interview_id, body.model_dump(mode="json"), ctx)
    if not row["replayed"]:
        background.add_task(n8n.kick, "feedback submitted")
    return dict(row)


@router.post("/interviews/{interview_id}/cancel", summary="Cancel an interview (slot released)")
async def cancel_interview(
    body: CancelRequest,
    background: BackgroundTasks,
    interview_id: UuidPath,
    ctx: Ctx = Depends(staff_ctx),
    repo: HiringRepository = Depends(get_hiring_repo),
    n8n: N8nClient = Depends(get_n8n),
) -> dict[str, Any]:
    row = await repo.cancel_interview(interview_id, body.reason, ctx)
    if row["changed"]:
        background.add_task(n8n.kick, "interview cancelled")
    return dict(row)


@router.post("/interviews/{interview_id}/no-show", summary="Candidate did not attend")
async def mark_no_show(
    background: BackgroundTasks,
    interview_id: UuidPath,
    ctx: Ctx = Depends(staff_ctx),
    repo: HiringRepository = Depends(get_hiring_repo),
    n8n: N8nClient = Depends(get_n8n),
) -> dict[str, Any]:
    row = await repo.mark_no_show(interview_id, ctx)
    if row["changed"]:
        background.add_task(n8n.kick, "interview no-show")
    return dict(row)


@router.post("/onboarding-tasks/{task_id}/complete", summary="Mark an onboarding task done")
async def complete_onboarding_task(
    background: BackgroundTasks,
    task_id: UuidPath,
    ctx: Ctx = Depends(staff_ctx),
    repo: HiringRepository = Depends(get_hiring_repo),
    n8n: N8nClient = Depends(get_n8n),
) -> dict[str, Any]:
    row = await repo.complete_task(task_id, ctx)
    if row["onboarding_complete"] and row["changed"]:
        background.add_task(n8n.kick, "onboarding complete")
    return dict(row)


@router.post("/errors/{error_id}/replay", summary="Replay an error-queue entry (runs in n8n WF-07)")
async def replay_error(
    error_id: UuidPath,
    ctx: Ctx = Depends(staff_ctx),
    repo: HiringRepository = Depends(get_hiring_repo),
    n8n: N8nClient = Depends(get_n8n),
) -> dict[str, Any]:
    entry = await repo.error_entry(error_id)
    if entry["status"] not in {"OPEN", "REPLAYING"}:
        raise ConflictError(f"error {error_id} is {entry['status']}", code="ERROR_NOT_OPEN")
    return await n8n.replay(error_id, ctx["actor_id"])


@router.get("/overview", summary="Operations overview (counts that need attention)")
async def overview(_: Ctx = Depends(staff_ctx), repo: HiringRepository = Depends(get_hiring_repo)) -> dict[str, Any]:
    return await repo.ops_overview()


@router.get("/queues/{name}", summary="Work queues: " + ", ".join(QUEUES))
async def queue(
    name: str,
    limit: int = Query(default=50, ge=1, le=500),
    _: Ctx = Depends(staff_ctx),
    repo: HiringRepository = Depends(get_hiring_repo),
) -> list[dict[str, Any]]:
    view = QUEUES.get(name)
    if view is None:
        raise NotFoundError(f"unknown queue {name}; use one of {', '.join(QUEUES)}", code="QUEUE_NOT_FOUND")
    return await repo.queue(view, limit)


# ---- read models for the HR portal ------------------------------------------------------------------------------
@router.get("/me", summary="The signed-in staff member")
async def me(ctx: Ctx = Depends(staff_ctx), directory: StaffDirectory = Depends(get_staff_directory)) -> dict[str, Any]:
    profile = await directory.profile(ctx["actor_id"])
    return dict(profile or {})


@router.get("/applications", summary="Applications, newest activity first")
async def applications(
    status: str | None = Query(default=None, pattern=r"^[A-Z_]{3,40}$"),
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: Ctx = Depends(staff_ctx),
    directory: StaffDirectory = Depends(get_staff_directory),
) -> list[dict[str, Any]]:
    return await directory.applications(status, search, limit, offset)


@router.get("/applications/{application_id}", summary="Everything about one application, with its full timeline")
async def application_detail(
    application_id: UuidPath, _: Ctx = Depends(staff_ctx), directory: StaffDirectory = Depends(get_staff_directory)
) -> dict[str, Any]:
    return await directory.application_detail(application_id)


@router.get("/onboarding", summary="Employees currently onboarding, with their tasks")
async def onboarding(
    _: Ctx = Depends(staff_ctx), directory: StaffDirectory = Depends(get_staff_directory)
) -> list[dict[str, Any]]:
    return await directory.onboarding_board()
