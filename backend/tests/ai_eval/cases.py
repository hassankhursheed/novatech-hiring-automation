"""Labelled evaluation cases (datasets/*.jsonl) and the contexts the production code builds from them.

The labels are a recruiter's judgement for each case: `expected` is the best answer, `acceptable` the answers a
reasonable recruiter would not object to, `must_not` answers that would be a real error (for example shortlisting a
prompt-injection attempt). Positions mirror db/seed/010_novatech_config.sql.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.ai.analyzer import CandidateContext
from app.ai.interview_assessor import InterviewContext

DATASETS = Path(__file__).parent / "datasets"

POSITIONS: dict[str, dict[str, Any]] = {
    "PY_DEV": {
        "title": "Python Developer",
        "department": "Engineering",
        "min_experience_years": 1.0,
        "description": "Backend engineer building APIs and data services with Python, FastAPI/Django and PostgreSQL.",
        "screened_skills": [
            "aws",
            "azure",
            "django",
            "docker",
            "fastapi",
            "gcp",
            "git",
            "mysql",
            "postgresql",
            "python",
            "rest apis",
            "sql",
            "sql server",
        ],
    },
    "QA_ENG": {
        "title": "QA Engineer",
        "department": "Engineering",
        "min_experience_years": 1.0,
        "description": "Owns manual and automated testing of web applications and APIs.",
        "screened_skills": [
            "api testing",
            "ci/cd",
            "cypress",
            "github actions",
            "jenkins",
            "jira",
            "manual testing",
            "mysql",
            "playwright",
            "postgresql",
            "postman",
            "selenium",
            "sql",
            "test cases",
            "test planning",
        ],
    },
    "BDE": {
        "title": "Business Development Executive",
        "department": "Sales",
        "min_experience_years": 1.0,
        "description": (
            "Generates and qualifies B2B leads for NovaTech software services and manages the sales pipeline."
        ),
        "screened_skills": [
            "b2b sales",
            "cold calling",
            "communication",
            "crm",
            "hubspot",
            "lead generation",
            "market research",
            "negotiation",
            "presentation",
            "prospecting",
            "sales",
            "salesforce",
            "zoho crm",
        ],
    },
}
CRITERIA = ("technical_skills", "communication", "problem_solving", "experience", "team_fit")


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    position: str
    expected: str
    acceptable: tuple[str, ...]
    must_not: tuple[str, ...] = ()
    max_technical: int | None = None
    expected_alignment: str | None = None
    forbidden_in_summary: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def input_text(self) -> str:
        """Everything the model was given, for reports and the groundedness judge (the summary must stick to it)."""
        r, p = self.raw, POSITIONS[self.position]
        role = (
            f"Position: {p['title']} ({p['department']}). Description: {p['description']} "
            f"Minimum experience: {p['min_experience_years']:g} years. "
            f"Skills screened for (alternatives): {', '.join(p['screened_skills'])}."
        )
        if "ratings" in r:
            ratings = ", ".join(f"{c.replace('_', ' ')} {v}/5" for c, v in zip(CRITERIA, r["ratings"], strict=True))
            interview_score = round(sum(r["ratings"]) / 25 * 100, 2)
            return (
                f"{role} Screening score {r['screening_score']}/100: {r['screening_summary']} "
                f"Skills not evidenced at screening: {', '.join(r['missing_skills']) or 'none'}. "
                f"Interview ratings: {ratings}. Interview score {interview_score:g}/100. "
                f"Interviewer recommends {r['interviewer_recommendation']}. Interviewer comments: {r['comments']}"
            )
        return (
            f"{role} Stated experience: {r['experience_years']} years. "
            f"Declared skills: {', '.join(r['skills']) or 'none'}. Current title: {r['current_title'] or 'none'}. "
            f"Cover letter: {r['cover_letter'] or 'none'}. CV: {r['cv_text'] or 'none'}"
        )


def _load(name: str) -> list[Case]:
    cases = []
    for line in (DATASETS / name).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        cases.append(
            Case(
                id=r["id"],
                kind=r["kind"],
                position=r["position"],
                expected=r["expected"],
                acceptable=tuple(r["acceptable"]),
                must_not=tuple(r.get("must_not", ())),
                max_technical=r.get("max_technical"),
                expected_alignment=r.get("expected_alignment"),
                forbidden_in_summary=tuple(r.get("forbidden_in_summary", ())),
                raw=r,
            )
        )
    return cases


SCREENING = _load("screening.jsonl")
INTERVIEW = _load("interview.jsonl")


def candidate_context(case: Case) -> CandidateContext:
    r, p = case.raw, POSITIONS[case.position]
    names = {"py-pii": "Sara Ahmed", "bde-pii": "Bilal Khan"}
    return CandidateContext(
        application_id=f"eval-{case.id}",
        candidate_name=names.get(case.id),
        position_title=p["title"],
        department=p["department"],
        min_experience_years=p["min_experience_years"],
        position_description=p["description"],
        screened_skills=p["screened_skills"],
        experience_years=r["experience_years"],
        skills=r["skills"],
        current_title=r["current_title"] or None,
        cover_letter=r["cover_letter"] or None,
        cv_text=r["cv_text"] or None,
    )


def interview_context(case: Case) -> InterviewContext:
    r, p = case.raw, POSITIONS[case.position]
    ratings = dict(zip(CRITERIA, r["ratings"], strict=True))
    return InterviewContext(
        application_id=f"eval-{case.id}",
        interview_id=f"eval-{case.id}",
        candidate_name="Hamza Khan" if case.id == "iv-pii" else None,
        interviewer_name="Usman Tariq",
        position_title=p["title"],
        department=p["department"],
        position_description=p["description"],
        screening_score=float(r["screening_score"]),
        screening_summary=r["screening_summary"],
        missing_skills=r["missing_skills"],
        interview_score=round(sum(r["ratings"]) / 25 * 100, 2),
        interviewer_recommendation=r["interviewer_recommendation"],
        comments=r["comments"],
        **ratings,
    )
