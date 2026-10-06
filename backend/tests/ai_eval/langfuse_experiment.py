"""Langfuse datasets and experiment runs for the hiring AI.

    docker compose --profile test run --rm backend-tests python -m tests.ai_eval.langfuse_experiment

1. Uploads the labelled cases (datasets/*.jsonl) as two Langfuse datasets, `novatech-screening` and
   `novatech-interview`. Item ids are the case ids, so re-running updates instead of duplicating.
2. Runs the production AI (same prompt, schema, validation and model as the backend) over every item as one
   experiment run per dataset, named after the model and the time. Every item links to its trace.
3. Scores each item with the same checks as the DeepEval suite (acceptable recommendation, safe outcome, no
   personal data, notes-vs-ratings detection) and the run with its averages, so runs can be compared in the
   Langfuse UI (Datasets -> <dataset> -> Runs) after a prompt or model change.

Needs MISTRAL_API_KEY and the LANGFUSE_* keys (self-hosted: docker compose --profile observability up -d).
"""

import os
import sys
from datetime import UTC, datetime
from typing import Any

from langfuse import Evaluation, get_client

from app.ai.analyzer import CandidateAnalyzer
from app.ai.interview_assessor import InterviewAssessor
from app.ai.prompts import INTERVIEW_PROMPT_VERSION, PROMPT_VERSION
from app.ai.schemas import CandidateAnalysisWire, InterviewAssessmentWire
from app.main import _build_llm
from tests.ai_eval.cases import INTERVIEW, SCREENING, Case, candidate_context, interview_context
from tests.ai_eval.evaluation import COMPANY, Outcome, _checks, ai_available, eval_settings

PROMPTS = {"novatech-screening": PROMPT_VERSION, "novatech-interview": INTERVIEW_PROMPT_VERSION}
DATASETS = {
    "novatech-screening": (SCREENING, "Advisory screening analysis of job applications (recruiter-labelled)."),
    "novatech-interview": (INTERVIEW, "Advisory assessment of interview scorecards (recruiter-labelled)."),
}


def upload(client: Any) -> None:
    for name, (cases, description) in DATASETS.items():
        client.create_dataset(name=name, description=description, metadata={"owner": "NovaTech hiring"})
        for case in cases:
            client.create_dataset_item(
                dataset_name=name,
                id=f"{name}-{case.id}",
                input={"case_id": case.id, "position": case.position, "text": case.input_text},
                expected_output={
                    "recommendation": case.expected,
                    "acceptable": list(case.acceptable),
                    "must_not": list(case.must_not),
                    "evidence_alignment": case.expected_alignment,
                },
                metadata={"kind": case.kind},
            )
        print(f"dataset {name}: {len(cases)} items")


OUTCOMES: dict[str, Outcome] = {}  # case id -> scored outcome of the current run (the trace gets plain JSON)


def item_evaluator(cases: dict[str, Case]) -> Any:
    def evaluate(*, input: Any, output: Any, expected_output: Any, metadata: Any, **_: Any) -> list[Evaluation]:
        o = OUTCOMES[input["case_id"]]
        checks = _checks(o)
        evaluations = [
            Evaluation(name=name, value=1.0 if ok else 0.0, comment=None if ok else f"got {o.recommendation}")
            for name, ok in checks.items()
        ]
        evaluations.append(Evaluation(name="attempts", value=float(o.attempts)))
        return evaluations

    return evaluate


def run_evaluator(*, item_results: list[Any], **_: Any) -> list[Evaluation]:
    names = {e.name for r in item_results for e in r.evaluations if e.name != "attempts"}
    averages = []
    for name in sorted(names):
        values = [float(e.value) for r in item_results for e in r.evaluations if e.name == name]
        averages.append(Evaluation(name=f"avg_{name}", value=round(sum(values) / len(values), 3)))
    return averages


def task_for(dataset: str, cases: dict[str, Case]) -> Any:
    settings = eval_settings()
    analyzer = CandidateAnalyzer(_build_llm(settings, CandidateAnalysisWire, "candidate_analysis"))
    assessor = InterviewAssessor(_build_llm(settings, InterviewAssessmentWire, "interview_assessment"))

    async def task(*, item: Any, **_: Any) -> dict[str, Any]:
        case = cases[item.input["case_id"]]
        if dataset == "novatech-screening":
            result = await analyzer.analyze(candidate_context(case), company=COMPANY, correlation_id=f"EXP-{case.id}")
            payload = result.analysis
            alignment = None
        else:
            r = await assessor.assess(interview_context(case), company=COMPANY, correlation_id=f"EXP-{case.id}")
            result, payload = r, r.assessment  # type: ignore[assignment]
            alignment = r.assessment.evidence_alignment.value if r.assessment else None
        outcome = Outcome(
            case=case,
            status=result.status.value,
            attempts=result.attempts,
            latency_ms=result.latency_ms,
            recommendation=payload.recommendation.value if payload else None,
            summary=payload.summary if payload else "",
            output=payload.model_dump(mode="json") if payload else {},
            trace_id=result.trace_id,
            fallback_reason=result.fallback_reason,
            alignment=alignment,
        )
        OUTCOMES[case.id] = outcome
        return {
            "status": outcome.status,
            "attempts": outcome.attempts,
            **outcome.output,
            "fallback_reason": outcome.fallback_reason,
        }

    return task


def main() -> int:
    available, model = ai_available()
    if not available:
        print(f"skipped: {model}")
        return 1
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        print("skipped: LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are not set")
        return 1
    client = get_client()
    upload(client)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    for name, (cases, _) in DATASETS.items():
        by_id = {c.id: c for c in cases}
        result = client.get_dataset(name).run_experiment(
            name=f"{eval_settings().llm_model} {stamp}",
            description=f"{model}; prompt {PROMPTS[name]}",
            task=task_for(name, by_id),
            evaluators=[item_evaluator(by_id)],
            run_evaluators=[run_evaluator],
            max_concurrency=1,  # the free tier allows about one request per second
            metadata={"model": eval_settings().llm_model},
        )
        print(f"\n{name}: " + ", ".join(f"{e.name}={e.value}" for e in result.run_evaluations))
        if result.dataset_run_url:
            print(f"  run: {result.dataset_run_url}")
    client.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
