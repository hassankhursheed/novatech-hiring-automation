"""Screening endpoints (internal, called by n8n WF-03). All are read-only computations:
n8n persists results through the database api.* functions and owns the orchestration."""

from fastapi import APIRouter, Depends, Request

from app.ai.analyzer import CandidateAnalyzer
from app.ai.schemas import AnalysisResult
from app.api.deps import get_analyzer, get_reference_repo
from app.core.context import get_correlation_id
from app.core.errors import UnprocessableError
from app.core.faults import raise_if_injected
from app.core.logging import get_logger
from app.core.security import require_internal_api_key
from app.domain.contracts import ApplicationRef, DecideRequest, ScoreResult, ScreeningDecision
from app.domain.scoring import NoActiveRulesError, score_application
from app.domain.screening_policy import POLICY_VERSION, PolicySettings, decide
from app.repositories.reference import ReferenceRepository

log = get_logger(__name__)

router = APIRouter(prefix="/v1/screening", tags=["screening"], dependencies=[Depends(require_internal_api_key)])


@router.post("/score", response_model=ScoreResult, summary="Rule-based score from the configured rules")
async def score(
    request: Request, body: ApplicationRef, repo: ReferenceRepository = Depends(get_reference_repo)
) -> ScoreResult:
    await raise_if_injected(request, "score")
    bundle = await repo.scoring_bundle(body.application_id)
    try:
        outcome = score_application(bundle.config, bundle.data)
    except NoActiveRulesError as exc:
        raise UnprocessableError(str(exc), code="NO_ACTIVE_RULES") from exc

    log.info(
        "application_scored",
        application_id=body.application_id,
        score=outcome.score,
        route=outcome.route.value,
        scoring_version=bundle.config.version,
    )
    return ScoreResult(
        application_id=bundle.application_id,
        position_code=bundle.config.position_code,
        scoring_version=bundle.config.version,
        input_hash=outcome.input_hash,
        points_awarded=outcome.points_awarded,
        points_possible=outcome.points_possible,
        score=outcome.score,
        route=outcome.route,
        shortlist_min_score=bundle.config.shortlist_min_score,
        review_min_score=bundle.config.review_min_score,
        breakdown=outcome.breakdown,
    )


@router.post("/ai-analysis", response_model=AnalysisResult, summary="Advisory AI analysis (validated, with fallback)")
async def ai_analysis(
    request: Request,
    body: ApplicationRef,
    repo: ReferenceRepository = Depends(get_reference_repo),
    analyzer: CandidateAnalyzer = Depends(get_analyzer),
) -> AnalysisResult:
    fault = await raise_if_injected(request, "ai")
    ctx, company = await repo.candidate_context(body.application_id)
    return await analyzer.analyze(ctx, company=company, correlation_id=get_correlation_id(), fault=fault)


@router.post(
    "/decide", response_model=ScreeningDecision, summary="Screening decision policy (rules decide, AI advises)"
)
async def decide_screening(
    request: Request, body: DecideRequest, repo: ReferenceRepository = Depends(get_reference_repo)
) -> ScreeningDecision:
    await raise_if_injected(request, "decide")
    config = await repo.settings(["screening.ai_enabled", "screening.auto_reject_enabled"])
    policy = PolicySettings(
        ai_enabled=bool(config.get("screening.ai_enabled", True)),
        auto_reject_enabled=bool(config.get("screening.auto_reject_enabled", True)),
    )
    result = decide(body.score, body.ai, policy)
    return ScreeningDecision(
        decision=result.decision,
        reason=result.reason,
        policy_version=POLICY_VERSION,
        inputs={
            "score": body.score.model_dump(mode="json"),
            "ai": body.ai.model_dump(mode="json") if body.ai else None,
            "settings": {"ai_enabled": policy.ai_enabled, "auto_reject_enabled": policy.auto_reject_enabled},
        },
    )
