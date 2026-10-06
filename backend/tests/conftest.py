import importlib.util
import os

# The AI evaluation (tests/ai_eval) needs the optional "eval" dependency group (DeepEval). Without it, pytest does not
# even collect that folder, so the regular suite runs with the dev tools only (as in CI's backend job).
collect_ignore_glob = [] if importlib.util.find_spec("deepeval") else ["ai_eval/*"]

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
from app.ai.interview_assessor import InterviewAssessor, InterviewContext
from app.ai.llm import LLMOutcome
from app.ai.schemas import InterviewAssessmentWire
from app.ai.stub import StubStructuredLLM
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.domain.scoring import ScoringConfig, ScoringInput, ScoringRule
from app.domain.validation import PositionInfo, ValidationContext
from app.repositories.reference import ScoringBundle

API_KEY = "test-key-primary"
Row = dict[str, Any]

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


def interview_context(**overrides: Any) -> InterviewContext:
    values: dict[str, Any] = {
        "application_id": "00000000-0000-4000-8000-00000000aaaa",
        "interview_id": "00000000-0000-4000-8000-00000000bbbb",
        "candidate_name": "Ayesha Khan",
        "interviewer_name": "Bilal Ahmed",
        "position_title": "Python Developer",
        "department": "Engineering",
        "position_description": "Backend engineer building APIs with Python, FastAPI and PostgreSQL.",
        "screening_score": 82.0,
        "screening_summary": "Shows Python, FastAPI and PostgreSQL with 4 years of experience.",
        "missing_skills": ["kubernetes"],
        "technical_skills": 4,
        "communication": 4,
        "problem_solving": 4,
        "experience": 4,
        "team_fit": 5,
        "interview_score": 84.0,
        "interviewer_recommendation": "HIRE",
        "comments": "Designed a clean REST API for the take-home task and explained indexing trade-offs clearly.",
    }
    values.update(overrides)
    return InterviewContext(**values)


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

    def read(self, key: str) -> bytes | None:
        return self.files.get(key)

    def read_text(self, key: str) -> str | None:
        return self.texts.get(key)


CANDIDATE_ID = "00000000-0000-4000-8000-0000000000c1"
INTERVIEWER_ID = "00000000-0000-4000-8000-000000000004"
APPROVER_ID = "00000000-0000-4000-8000-000000000003"
HR_ID = "00000000-0000-4000-8000-000000000001"
INTERVIEW_ID = "00000000-0000-4000-8000-0000000000e1"
OFFER_ID = "00000000-0000-4000-8000-0000000000f1"
SLOT_ID = "00000000-0000-4000-8000-0000000000a1"


def offer_snapshot(status: str = "PENDING_APPROVAL", **overrides: Any) -> dict[str, Any]:
    offer: dict[str, Any] = {
        "offer_id": OFFER_ID,
        "offer_code": "OFF-2026-0001",
        "revision": 1,
        "status": status,
        "monthly_salary": 220000,
        "currency": "PKR",
        "joining_date": "2026-11-02",
        "probation_months": 3,
        "department": "Engineering",
        "required_approval_levels": 1,
        "approval_threshold_applied": 250000,
        "sent_at": None,
        "expires_at": None,
        "candidate_response": None,
        "candidate_message": None,
        "document_storage_key": None,
        "created_by": "n8n",
        "reporting_manager": {"id": APPROVER_ID, "full_name": "Ayesha Khan", "email": "a@x", "job_title": "CTO"},
        "approvals": [],
        "next_level": 1,
        "next_approvers": [{"id": APPROVER_ID, "full_name": "Ayesha Khan", "email": "a@x"}],
        "issued_on": date(2026, 10, 1),
        "application": {
            "application_id": "00000000-0000-4000-8000-00000000aaaa",
            "application_code": "APP-2026-00001",
            "expected_salary": 220000,
            "application_score": 90,
            "interview_score": 84,
            "final_score": 85.8,
            "candidate": {"candidate_id": CANDIDATE_ID, "candidate_code": "CAN-2026-00001", "full_name": "Hira Saleem"},
            "position": {"title": "Python Developer"},
            "company": {"name": "NovaTech Solutions"},
        },
    }
    offer.update(overrides)
    return offer


class FakeHiringRepo:
    """In-memory stand-in for HiringRepository: records calls, returns scripted rows."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.settings_values: dict[str, Any] = {
            "company.portal_url": "https://careers.novatech.example",
            "evaluation.application_weight": 0.3,
            "evaluation.interview_weight": 0.7,
            "evaluation.select_min_score": 75,
            "evaluation.review_min_score": 60,
        }
        self.offer = offer_snapshot()
        self.evaluation: dict[str, Any] = {
            "application_id": "00000000-0000-4000-8000-00000000aaaa",
            "status": "INTERVIEWED",
            "application_score": 90,
            "interview_score": 84,
            "recommendation": "HIRE",
            "interview_code": "INT-2026-0001",
            "interview_id": "00000000-0000-4000-8000-00000000bbbb",
            "ai_enabled": False,
            "ai_status": None,
            "ai_recommendation": None,
            "ai_evidence_alignment": None,
            "ai_fallback_reason": None,
        }
        self.active_staff = {INTERVIEWER_ID, APPROVER_ID, HR_ID}

    async def settings(self, keys: list[str]) -> dict[str, Any]:
        return {k: v for k, v in self.settings_values.items() if k in keys}

    async def interview_for_candidate(self, interview_id: str, candidate_id: str) -> dict[str, Any]:
        if candidate_id != CANDIDATE_ID or interview_id != INTERVIEW_ID:
            raise NotFoundError("interview not found", code="INTERVIEW_NOT_FOUND")
        return {"interview_id": interview_id, "status": "INVITED", "slots": [{"slot_id": SLOT_ID}]}

    async def interview_for_staff(self, interview_id: str) -> dict[str, Any]:
        return {"interview_id": interview_id, "interviewer_id": INTERVIEWER_ID, "candidate_name": "Hira Saleem"}

    async def confirm_slot(self, interview_id: str, slot_id: str, ctx: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("confirm_slot", (interview_id, slot_id, ctx)))
        return {"interview_id": interview_id, "status": "CONFIRMED", "changed": True}

    async def cancel_interview(self, interview_id: str, reason: str, ctx: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("cancel_interview", (interview_id, reason, ctx)))
        return {"interview_id": interview_id, "status": "CANCELLED", "changed": True}

    async def submit_feedback(self, interview_id: str, feedback: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("submit_feedback", (interview_id, feedback, ctx)))
        return {
            "feedback_id": "f1",
            "interview_score": 84,
            "recommendation": feedback["recommendation"],
            "replayed": False,
        }

    async def evaluation_inputs(self, application_id: str) -> dict[str, Any]:
        return self.evaluation

    async def interview_assessment_context(self, application_id: str) -> tuple[InterviewContext, str]:
        return interview_context(application_id=application_id), "NovaTech Solutions"

    async def offer_snapshot(self, offer_id: str) -> dict[str, Any]:
        return self.offer

    async def offer_for_candidate(self, offer_id: str, candidate_id: str) -> dict[str, Any]:
        if candidate_id != CANDIDATE_ID:
            raise NotFoundError("offer not found", code="OFFER_NOT_FOUND")
        return self.offer

    async def respond_to_offer(self, offer_id: str, response: str, message: str | None, ctx: dict[str, Any]) -> Row:
        self.calls.append(("respond_to_offer", (offer_id, response, message, ctx)))
        return {"offer_id": offer_id, "status": response, "application_status": "ACCEPTED", "changed": True}

    async def decide_approval(self, offer_id: str, level: int, decision: str, reason: str | None, ctx: Row) -> Row:
        self.calls.append(("decide_approval", (offer_id, level, decision, reason, ctx)))
        return {"offer_id": offer_id, "offer_status": "APPROVED", "level": level, "decision": decision, "changed": True}

    async def create_offer(self, application_id: str, terms: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("create_offer", (application_id, terms, ctx)))
        return {"offer_id": OFFER_ID, "revision": 2, "created": True}

    async def transition(self, application_id: str, to_status: str, reason: str, ctx: Row, expected: str | None) -> Row:
        self.calls.append(("transition", (application_id, to_status, reason, ctx, expected)))
        return {"application_id": application_id, "to_status": to_status, "changed": True}

    async def staff_exists(self, staff_id: str) -> bool:
        return staff_id in self.active_staff

    async def error_entry(self, error_id: str) -> dict[str, Any]:
        return {"error_id": error_id, "status": "OPEN", "replay_workflow": "WF-03"}

    async def queue(self, view: str, limit: int) -> list[dict[str, Any]]:
        self.calls.append(("queue", (view, limit)))
        return [{"view": view}]

    async def daily_report_context(self) -> tuple[str, str]:
        return "NovaTech Solutions", "careers@novatech.example"


class FakeN8n:
    def __init__(self) -> None:
        self.kicks: list[str] = []
        self.replays: list[tuple[str, str]] = []

    async def kick(self, reason: str) -> bool:
        self.kicks.append(reason)
        return True

    async def replay(self, error_id: str, staff_id: str) -> dict[str, Any]:
        self.replays.append((error_id, staff_id))
        return {"status": "RESOLVED", "error_id": error_id}

    async def close(self) -> None:
        return None


@pytest.fixture
def hiring_repo() -> FakeHiringRepo:
    return FakeHiringRepo()


@pytest.fixture
def n8n() -> FakeN8n:
    return FakeN8n()


@pytest.fixture
def repo() -> FakeReferenceRepo:
    return FakeReferenceRepo()


@pytest.fixture
def storage() -> MemoryStorage:
    return MemoryStorage()


@pytest.fixture
def client(
    repo: FakeReferenceRepo, storage: MemoryStorage, hiring_repo: FakeHiringRepo, n8n: FakeN8n
) -> Iterator[TestClient]:
    from app.ai.report_writer import ReportWriter
    from app.core.links import LinkSigner
    from app.main import create_app

    get_settings.cache_clear()
    app = create_app(get_settings())
    app.state.reference_repo = repo
    app.state.storage = storage
    app.state.analyzer = CandidateAnalyzer(FakeLLM(*[LLMOutcome(dict(VALID_ANALYSIS), None, "end_turn")] * 5))
    app.state.db = None
    app.state.hiring_repo = hiring_repo
    app.state.n8n = n8n
    app.state.link_signer = LinkSigner.from_settings(get_settings())
    app.state.report_writer = ReportWriter(None)
    app.state.interview_assessor = InterviewAssessor(StubStructuredLLM(InterviewAssessmentWire))
    # No `with` block: the lifespan (which opens the DB pool) does not run in API unit tests.
    yield TestClient(app, raise_server_exceptions=False)
