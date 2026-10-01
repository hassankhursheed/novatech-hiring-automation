"""FastAPI dependencies. Long-lived resources are created in the app lifespan and stored on app.state."""

from fastapi import Request

from app.ai.analyzer import CandidateAnalyzer
from app.core.db import Database
from app.repositories.reference import ReferenceRepository
from app.services.storage import Storage


def get_db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def get_reference_repo(request: Request) -> ReferenceRepository:
    repo: ReferenceRepository = request.app.state.reference_repo
    return repo


def get_storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


def get_analyzer(request: Request) -> CandidateAnalyzer:
    analyzer: CandidateAnalyzer = request.app.state.analyzer
    return analyzer
