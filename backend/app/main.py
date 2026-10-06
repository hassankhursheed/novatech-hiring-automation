"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.ai.analyzer import CandidateAnalyzer
from app.ai.interview_assessor import InterviewAssessor
from app.ai.llm import LangChainStructuredLLM, StructuredLLM
from app.ai.report_writer import DailySummaryWire, ReportWriter
from app.ai.schemas import CandidateAnalysisWire, InterviewAssessmentWire
from app.ai.stub import StubStructuredLLM
from app.api.routes import auth, health, intake, portal, screening, staff, workflow_support
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.errors import register_exception_handlers
from app.core.links import LinkSigner
from app.core.logging import configure_logging, get_logger
from app.core.middleware import CorrelationAndLoggingMiddleware
from app.repositories.hiring import HiringRepository, StaffDirectory
from app.repositories.reference import ReferenceRepository
from app.services.n8n import N8nClient
from app.services.storage import LocalStorage

log = get_logger(__name__)


def _build_llm(settings: Settings, schema: type[BaseModel], run_name: str) -> StructuredLLM | None:
    """The configured LLM, or None when AI is disabled or misconfigured (callers then fall back)."""
    if settings.llm_provider == "none":
        log.warning("ai_disabled", feature=run_name, reason="LLM_PROVIDER=none")
        return None
    if settings.llm_provider == "stub":
        log.warning(
            "ai_stub", feature=run_name, reason="LLM_PROVIDER=stub: deterministic test model (automated tests only)"
        )
        return StubStructuredLLM(schema)
    if not settings.llm_api_key:
        log.warning("ai_disabled", feature=run_name, reason="MISTRAL_API_KEY is not set")
        return None
    try:
        return LangChainStructuredLLM(settings, schema, run_name=run_name)
    except Exception as exc:  # misconfiguration must not take the whole API down
        log.error("ai_init_failed", feature=run_name, provider=settings.llm_provider, error=str(exc))
        return None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.app_env != "test")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings)
        await db.open()
        app.state.db = db
        app.state.reference_repo = ReferenceRepository(db)
        app.state.hiring_repo = HiringRepository(db)
        app.state.staff_directory = StaffDirectory(db)
        app.state.storage = LocalStorage(settings.storage_dir)
        app.state.analyzer = CandidateAnalyzer(_build_llm(settings, CandidateAnalysisWire, "candidate_analysis"))
        app.state.interview_assessor = InterviewAssessor(
            _build_llm(settings, InterviewAssessmentWire, "interview_assessment")
        )
        app.state.report_writer = ReportWriter(_build_llm(settings, DailySummaryWire, "daily_report_summary"))
        app.state.link_signer = LinkSigner.from_settings(settings)
        app.state.n8n = N8nClient(settings)
        log.info(
            "startup",
            env=settings.app_env,
            version=settings.app_version,
            ai_provider=settings.llm_provider,
            ai_model=settings.llm_model,
            fault_injection=settings.fault_injection_enabled,
        )
        try:
            yield
        finally:
            await app.state.n8n.close()
            await db.close()
            log.info("shutdown")

    docs = settings.expose_api_docs and not settings.is_production
    app = FastAPI(
        title="NovaTech Hiring Automation API",
        version=settings.app_version,
        description=(
            "Validation, scoring, advisory AI, interview evaluation, offer documents, signed candidate/staff links "
            "and staff actions for the hiring workflows."
        ),
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )

    app.add_middleware(CorrelationAndLoggingMiddleware)
    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "Authorization", "X-Correlation-ID", "Idempotency-Key"],
            expose_headers=["X-Correlation-ID"],
            max_age=600,
        )
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(intake.public_router)
    app.include_router(intake.router)
    app.include_router(screening.router)
    app.include_router(workflow_support.router)
    app.include_router(portal.router)
    app.include_router(staff.router)
    app.include_router(auth.router)
    return app


app = create_app()
