import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEYS", "test-key-primary,test-key-rotated")
os.environ.setdefault("FAULT_INJECTION_ENABLED", "true")
os.environ.setdefault("LLM_PROVIDER", "none")

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.ai.analyzer import CandidateAnalyzer, CandidateContext
from app.ai.llm import LLMOutcome
from app.core.config import get_settings
from app.domain.scoring import ScoringConfig, ScoringInput, ScoringRule
from app.domain.validation import PositionInfo, ValidationContext
from app.repositories.reference import ScoringBundle

API_KEY = "test-key-primary"

POSITIONS = [
    PositionInfo(
        code="PY_DEV", title="Python Developer", currency="PKR", is_open=True, salary_min=120000, salary_max=350000
    ),
    PositionInfo(code="QA_ENG", title="QA Engineer", currency="PKR", is_open=True),
    PositionInfo(code="OLD_ROLE", title="Legacy Role", currency="PKR", is_open=False),
]
ALIASES = {"py": "python", "postgres": "postgresql", "rest api": "rest apis", "github": "git"}

PY_RULES = [
    ScoringRule("python", "Python", "SKILL_ANY", ["python"], None, 20),
    ScoringRule("web_framework", "FastAPI/Django", "SKILL_ANY", ["django", "fastapi"], None, 15),
    ScoringRule("sql", "SQL", "SKILL_ANY", ["mysql", "postgresql", "sql"], None, 10),
    ScoringRule("rest_apis", "REST APIs", "SKILL_ANY", ["rest apis"], None, 10),
    ScoringRule("git", "Git", "SKILL_ANY", ["git"], None, 5),
    ScoringRule("docker", "Docker", "SKILL_ANY", ["docker"], None, 10),
    ScoringRule("cloud", "AWS/Azure", "SKILL_ANY", ["aws", "azure", "gcp"], None, 10),
    ScoringRule("experience", "3+ years", "MIN_EXPERIENCE_YEARS", [], 3, 10),
    ScoringRule("domain", "Domain", "KEYWORD_ANY", ["fintech", "saas", "e-commerce"], None, 10),
]
PY_CONFIG = ScoringConfig(
    position_code="PY_DEV", version=3, shortlist_min_score=80, review_min_score=60, rules=PY_RULES
)


def validation_context(today: date = date(2026, 9, 28)) -> ValidationContext:
    return ValidationContext(
        positions=POSITIONS, skill_aliases=ALIASES, default_phone_region="PK", default_currency="PKR", today=today
    )


def scoring_input(**overrides: Any) -> ScoringInput:
    values: dict[str, Any] = {
        "skills": [],
        "experience_years": None,
        "cv_text": None,
        "cover_letter": None,
        "current_title": None,
        "current_company": None,
        "skill_aliases": ALIASES,
    }
    values.update(overrides)
    return ScoringInput(**values)


def candidate_context(**overrides: Any) -> CandidateContext:
    values: dict[str, Any] = {
        "application_id": "00000000-0000-4000-8000-00000000aaaa",
        "candidate_name": "Ali Ahmed",
        "position_title": "Python Developer",
        "department": "Engineering",
        "min_experience_years": 1.0,
        "position_description": "Backend APIs",
        "screened_skills": ["python", "fastapi"],
        "experience_years": 4.0,
        "skills": ["python", "fastapi"],
        "current_title": "Backend Engineer",
        "cover_letter": "I love APIs.",
        "cv_text": "Ali Ahmed, ali@example.com, +92 300 1234567. Built FastAPI services 2019 - 2023.",
    }
    values.update(overrides)
    return CandidateContext(**values)


VALID_ANALYSIS = {
    "technical_strength": 8,
    "experience_relevance": 7,
    "communication_indication": 6,
    "missing_skills": ["Docker"],
    "summary": "Relevant Python backend experience with FastAPI.",
    "recommendation": "SHORTLIST",
}


class FakeLLM:
    """Scripted LLM: returns the queued outcomes in order (or raises queued exceptions)."""

    provider = "fake"
    model = "fake-model"

    def __init__(self, *outcomes: LLMOutcome | Exception) -> None:
        self.outcomes = list(outcomes)
        self.prompts: list[str] = []

    async def generate(self, system: str, user: str, *, session_id: str | None) -> LLMOutcome:
        self.prompts.append(user)
        item = self.outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeReferenceRepo:
    def __init__(self) -> None:
        self.settings_values: dict[str, Any] = {
            "company.currency": "PKR",
            "company.timezone": "Asia/Karachi",
            "screening.ai_enabled": True,
            "screening.auto_reject_enabled": True,
        }
        self.bundle = ScoringBundle(
            application_id="00000000-0000-4000-8000-00000000aaaa",
            status="VALIDATED",
            config=PY_CONFIG,
            data=scoring_input(
                skills=["python", "fastapi", "postgresql", "docker", "git", "rest apis"],
                experience_years=4,
                cv_text="Worked at a fintech startup on AWS.",
            ),
        )

    async def positions(self) -> list[PositionInfo]:
        return POSITIONS

    async def open_positions_public(self) -> list[dict[str, Any]]:
        return [
            {
                "code": "PY_DEV",
                "title": "Python Developer",
                "department": "Engineering",
                "employment_type": "FULL_TIME",
                "min_experience_years": 1,
                "description": None,
            }
        ]

    async def skill_aliases(self) -> dict[str, str]:
        return ALIASES

    async def settings(self, keys: list[str]) -> dict[str, Any]:
        return {k: v for k, v in self.settings_values.items() if k in keys}

    async def scoring_bundle(self, application_id: str) -> ScoringBundle:
        return self.bundle

    async def candidate_context(self, application_id: str) -> tuple[CandidateContext, str]:
        return candidate_context(application_id=application_id), "NovaTech Solutions"


class MemoryStorage:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.texts: dict[str, str] = {}

    def save(self, key: str, data: bytes) -> None:
        self.files[key] = data

    def save_text(self, key: str, text: str) -> None:
        self.texts[key] = text

    def exists(self, key: str) -> bool:
        return key in self.files

    def read_text(self, key: str) -> str | None:
        return self.texts.get(key)


@pytest.fixture
def repo() -> FakeReferenceRepo:
    return FakeReferenceRepo()


@pytest.fixture
def storage() -> MemoryStorage:
    return MemoryStorage()


@pytest.fixture
def client(repo: FakeReferenceRepo, storage: MemoryStorage) -> Iterator[TestClient]:
    from app.main import create_app

    get_settings.cache_clear()
    app = create_app(get_settings())
    app.state.reference_repo = repo
    app.state.storage = storage
    app.state.analyzer = CandidateAnalyzer(FakeLLM(*[LLMOutcome(dict(VALID_ANALYSIS), None, "end_turn")] * 5))
    app.state.db = None
    # No `with` block: the lifespan (which opens the DB pool) does not run in API unit tests.
    yield TestClient(app, raise_server_exceptions=False)
