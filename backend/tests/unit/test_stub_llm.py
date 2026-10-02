from datetime import date

import pytest

from app.ai.analyzer import CandidateAnalyzer, build_messages
from app.ai.report_writer import DailySummaryWire, ReportWriter
from app.ai.schemas import AnalysisStatus, CandidateAnalysis, CandidateAnalysisWire
from app.ai.stub import StubStructuredLLM, candidate_analysis
from app.core.config import Settings
from app.core.faults import Fault
from tests.conftest import candidate_context


def test_stub_output_is_valid_and_tracks_the_evidence() -> None:
    strong = candidate_context(skills=["python", "fastapi"], experience_years=4.0)
    weak = candidate_context(skills=["excel"], experience_years=0.5, cv_text="Retail cashier.", cover_letter=None)
    strong_out = candidate_analysis(build_messages(strong, "NovaTech")[1])
    weak_out = candidate_analysis(build_messages(weak, "NovaTech")[1])
    CandidateAnalysis.model_validate(strong_out)
    CandidateAnalysis.model_validate(weak_out)
    assert strong_out["recommendation"] == "SHORTLIST" and strong_out["missing_skills"] == []
    assert weak_out["recommendation"] == "REJECT" and "python" in weak_out["missing_skills"]


def test_skill_matching_uses_word_boundaries() -> None:
    ctx = candidate_context(
        skills=[], screened_skills=["git", "go"], cv_text="Digital marketing; good at GitHub-free work."
    )
    out = candidate_analysis(build_messages(ctx, "NovaTech")[1])
    assert set(out["missing_skills"]) == {"git", "go"}


async def test_malformed_fault_retries_once_then_falls_back_with_the_stub() -> None:
    analyzer = CandidateAnalyzer(StubStructuredLLM(CandidateAnalysisWire))
    ok = await analyzer.analyze(candidate_context(), company="NovaTech", correlation_id=None)
    bad = await analyzer.analyze(
        candidate_context(), company="NovaTech", correlation_id=None, fault=Fault("ai", "malformed", None)
    )
    assert ok.status == AnalysisStatus.COMPLETED and ok.provider == "stub" and ok.attempts == 1
    assert bad.status == AnalysisStatus.FALLBACK and bad.attempts == 2 and "MALFORMED" in (bad.fallback_reason or "")


async def test_stub_report_summary_passes_the_numeric_guardrail() -> None:
    metrics = {
        "applications_received": 12,
        "shortlisted": 5,
        "offers_sent": 2,
        "offers_accepted": 1,
        "employees_onboarded": 1,
        "manual_intervention_required": 3,
    }
    result = await ReportWriter(StubStructuredLLM(DailySummaryWire)).write(
        metrics, date(2026, 10, 1), company="NovaTech", correlation_id=None
    )
    assert result.source == "AI" and "12 applications" in result.summary


def test_stub_is_refused_in_production() -> None:
    with pytest.raises(ValueError, match="stub"):
        Settings(app_env="production", llm_provider="stub", internal_api_keys="k" * 30, link_signing_secret="s" * 40)
