"""FastAPI application factory."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai.analyzer import CandidateAnalyzer
from app.ai.llm import LangChainStructuredLLM
from app.ai.schemas import CandidateAnalysisWire
from app.api.routes import health, intake, screening
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import CorrelationAndLoggingMiddleware
from app.repositories.reference import ReferenceRepository
from app.services.storage import LocalStorage

log = get_logger(__name__)

_PROVIDER_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "google": "GOOGLE_API_KEY",
}


def _build_analyzer(settings: Settings) -> CandidateAnalyzer:
    if settings.llm_provider == "none":
        log.warning("ai_disabled", reason="LLM_PROVIDER=none; analyses will fall back to manual review")
        return CandidateAnalyzer(None)
    key_env = _PROVIDER_KEY_ENV[settings.llm_provider]
    if not os.environ.get(key_env, "").strip():
        log.warning("ai_disabled", reason=f"{key_env} is not set; analyses will fall back to manual review")
        return CandidateAnalyzer(None)
    try:
        llm = LangChainStructuredLLM(settings, CandidateAnalysisWire, run_name="candidate_analysis")
    except Exception as exc:  # misconfiguration must not take the whole API down
        log.error("ai_init_failed", provider=settings.llm_provider, error=str(exc))
        return CandidateAnalyzer(None)
    return CandidateAnalyzer(llm)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.app_env != "test")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings)
        await db.open()
        app.state.db = db
        app.state.reference_repo = ReferenceRepository(db)
        app.state.storage = LocalStorage(settings.storage_dir)
        app.state.analyzer = _build_analyzer(settings)
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
            await db.close()
            log.info("shutdown")

    docs = settings.expose_api_docs and not settings.is_production
    app = FastAPI(
        title="NovaTech Hiring Automation API",
        version=settings.app_version,
        description="Validation, rule-based scoring, advisory AI analysis and documents for the hiring workflows.",
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
            allow_headers=["Content-Type", "X-Correlation-ID", "Idempotency-Key"],
            expose_headers=["X-Correlation-ID"],
            max_age=600,
        )
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(intake.public_router)
    app.include_router(intake.router)
    app.include_router(screening.router)
    return app


app = create_app()
