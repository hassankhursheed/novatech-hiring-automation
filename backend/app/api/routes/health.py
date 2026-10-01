from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.deps import get_db
from app.core.config import Settings, get_settings
from app.core.db import Database

router = APIRouter(tags=["health"])


@router.get("/health/live", summary="Liveness probe")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", summary="Readiness probe (database reachable)")
async def ready(db: Database = Depends(get_db), settings: Settings = Depends(get_settings)) -> JSONResponse:
    try:
        db_ok = await db.ping()
    except Exception:
        db_ok = False
    body = {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "version": settings.app_version,
        "ai_provider": settings.llm_provider,
    }
    return JSONResponse(body, status_code=200 if db_ok else 503)
