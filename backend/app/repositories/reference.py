"""Read-only queries. Nothing is cached: configuration changes (rules, thresholds, settings) apply to
the very next request without a redeploy (brief scenario 6)."""

from dataclasses import dataclass
from typing import Any

from app.ai.analyzer import CandidateContext
from app.core.db import Database
from app.core.errors import NotFoundError, UnprocessableError
from app.domain.scoring import ScoringConfig, ScoringInput, ScoringRule
from app.domain.validation import PositionInfo


@dataclass(frozen=True)
class ScoringBundle:
    application_id: str
    status: str
    config: ScoringConfig
    data: ScoringInput


class ReferenceRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def positions(self) -> list[PositionInfo]:
        rows = await self._db.fetch_all(
            "SELECT code, title, currency, is_open, salary_min, salary_max FROM hiring.job_positions ORDER BY title"
        )
        return [
            PositionInfo(
                code=r["code"],
                title=r["title"],
                currency=r["currency"].strip(),
                is_open=r["is_open"],
                salary_min=r["salary_min"],
                salary_max=r["salary_max"],
            )
            for r in rows
        ]

    async def open_positions_public(self) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in await self._db.fetch_all(
                """SELECT code, title, department, employment_type, min_experience_years, description
                 FROM hiring.job_positions WHERE is_open ORDER BY title"""
            )
        ]

    async def skill_aliases(self) -> dict[str, str]:
        rows = await self._db.fetch_all("SELECT alias, canonical FROM hiring.skill_aliases")
        return {r["alias"]: r["canonical"] for r in rows}

    async def settings(self, keys: list[str]) -> dict[str, Any]:
        rows = await self._db.fetch_all("SELECT key, value FROM hiring.settings WHERE key = ANY(%s)", (keys,))
        return {r["key"]: r["value"] for r in rows}

    async def scoring_bundle(self, application_id: str) -> ScoringBundle:
        # One snapshot so rules, thresholds and version are always mutually consistent.
        async with self._db.snapshot() as conn:
            cur = await conn.execute(
                """SELECT a.id::text AS application_id, a.status, a.skills, a.experience_years, a.cv_text,
                          a.cover_letter, a.current_title, a.current_company,
                          p.id AS position_id, p.code AS position_code,
                          sc.version, sc.shortlist_min_score, sc.review_min_score
                     FROM hiring.applications a
                     JOIN hiring.job_positions p ON p.id = a.job_position_id
                     LEFT JOIN hiring.scoring_configs sc ON sc.job_position_id = p.id
                    WHERE a.id = %s""",
                (application_id,),
            )
            app = await cur.fetchone()
            if app is None:
                raise NotFoundError(f"application {application_id} does not exist", code="APPLICATION_NOT_FOUND")
            if app["version"] is None:
                raise UnprocessableError(
                    f"no scoring configuration for position {app['position_code']}", code="NO_SCORING_CONFIG"
                )
            cur = await conn.execute(
                """SELECT rule_key, label, rule_type, match_terms, min_value, points
                     FROM hiring.scoring_rules WHERE job_position_id = %s AND is_active
                    ORDER BY sort_order, rule_key""",
                (app["position_id"],),
            )
            rules = await cur.fetchall()
            cur = await conn.execute("SELECT alias, canonical FROM hiring.skill_aliases")
            aliases = {r["alias"]: r["canonical"] for r in await cur.fetchall()}

        config = ScoringConfig(
            position_code=app["position_code"],
            version=app["version"],
            shortlist_min_score=float(app["shortlist_min_score"]),
            review_min_score=float(app["review_min_score"]),
            rules=[
                ScoringRule(
                    rule_key=r["rule_key"],
                    label=r["label"],
                    rule_type=r["rule_type"],
                    match_terms=list(r["match_terms"]),
                    min_value=float(r["min_value"]) if r["min_value"] is not None else None,
                    points=r["points"],
                )
                for r in rules
            ],
        )
        data = ScoringInput(
            skills=list(app["skills"] or []),
            experience_years=float(app["experience_years"]) if app["experience_years"] is not None else None,
            cv_text=app["cv_text"],
            cover_letter=app["cover_letter"],
            current_title=app["current_title"],
            current_company=app["current_company"],
            skill_aliases=aliases,
        )
        return ScoringBundle(application_id=app["application_id"], status=app["status"], config=config, data=data)

    async def candidate_context(self, application_id: str) -> tuple[CandidateContext, str]:
        row = await self._db.fetch_one(
            """SELECT a.id::text AS application_id, c.full_name, p.title, p.department, p.min_experience_years,
                      p.description, a.experience_years, a.skills, a.current_title, a.cover_letter, a.cv_text,
                      coalesce((SELECT array_agg(DISTINCT t ORDER BY t)
                                  FROM hiring.scoring_rules r, unnest(r.match_terms) t
                                 WHERE r.job_position_id = p.id AND r.is_active AND r.rule_type LIKE 'SKILL%%'),
                               '{}') AS screened_skills,
                      (SELECT value #>> '{}' FROM hiring.settings WHERE key = 'company.name') AS company
                 FROM hiring.applications a
                 JOIN hiring.candidates c ON c.id = a.candidate_id
                 JOIN hiring.job_positions p ON p.id = a.job_position_id
                WHERE a.id = %s""",
            (application_id,),
        )
        if row is None:
            raise NotFoundError(f"application {application_id} does not exist", code="APPLICATION_NOT_FOUND")
        ctx = CandidateContext(
            application_id=row["application_id"],
            candidate_name=row["full_name"],
            position_title=row["title"],
            department=row["department"],
            min_experience_years=float(row["min_experience_years"]),
            position_description=row["description"],
            screened_skills=list(row["screened_skills"]),
            experience_years=float(row["experience_years"]) if row["experience_years"] is not None else None,
            skills=list(row["skills"] or []),
            current_title=row["current_title"],
            cover_letter=row["cover_letter"],
            cv_text=row["cv_text"],
        )
        return ctx, row["company"] or "the company"
