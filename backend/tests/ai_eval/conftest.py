"""Session fixtures for the AI evaluation: the model runs over every case once; the report is written at the end.

    docker compose --profile test run --rm backend-tests deepeval test run tests/ai_eval -m ai_eval
    (or: pytest -m ai_eval tests/ai_eval)

Skipped without MISTRAL_API_KEY. AI_EVAL_PROVIDER=stub checks the pipeline with the deterministic test model.
"""

import os
from collections.abc import Iterator

import pytest

from tests.ai_eval.evaluation import Outcome, ai_available, eval_settings, run_all, write_report

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")


@pytest.fixture(scope="session")
def grounded_scores() -> dict[str, float]:
    return {}


@pytest.fixture(scope="session")
def ai_results(grounded_scores: dict[str, float]) -> Iterator[dict[str, list[Outcome]]]:
    available, model = ai_available()
    if not available:
        pytest.skip(model)
    results = run_all()
    yield results
    report = write_report(results, model, grounded_scores)
    for name, summary in report["summary"].items():
        print(f"\nAI evaluation ({name}, {model}): {summary}")


@pytest.fixture(scope="session")
def judge() -> object | None:
    """Mistral as the groundedness judge, when the live model is used (AI_EVAL_JUDGE=off disables it)."""
    if eval_settings().llm_provider != "mistral" or os.environ.get("AI_EVAL_JUDGE") == "off":
        return None
    from tests.ai_eval.metrics import MistralJudge

    return MistralJudge()


@pytest.fixture(scope="session")
def live_model() -> bool:
    return eval_settings().llm_provider == "mistral"
