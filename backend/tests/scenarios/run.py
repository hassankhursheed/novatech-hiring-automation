"""Scenario runner: proves the brief's 23 scenarios and screens 42 fictional applications on the running stack.

    docker compose --profile test run --rm backend-tests python -m tests.scenarios.run
    ... python -m tests.scenarios.run --only 1,9,11 --skip-bulk

Writes reports/scenario-report.md and reports/scenario-report.json. Exit code 1 if anything failed.
Requires a development stack: FAULT_INJECTION_ENABLED=true for the backend and LLM_PROVIDER=stub (or a real model).
"""

import argparse
import json
import os
import sys
import time
import traceback
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.scenarios.fixtures import bulk_fixtures
from tests.scenarios.harness import BACKEND, MAILPIT, N8N, Harness
from tests.scenarios.scenarios import SCENARIOS, Proof

REPORT_DIR = Path(os.environ.get("SCENARIO_REPORT_DIR", "reports"))


def preflight(h: Harness) -> dict[str, Any]:
    health = h.http.get(f"{BACKEND}/health/ready").json()
    if not health.get("database"):
        raise SystemExit("backend is not ready")
    h.http.get(f"{N8N}/healthz").raise_for_status()
    h.http.get(f"{MAILPIT}/api/v1/info").raise_for_status()
    # Lets the intake carry X-Fault-Inject to later workflow steps (dev only; see docs/reliability.md section 7).
    h.q(
        "UPDATE hiring.settings SET value = 'true', updated_by = 'scenario-runner' "
        "WHERE key = 'dev.fault_injection_enabled'"
    )
    slots = h.one(
        """SELECT count(*) AS n FROM hiring.interview_slots s
             JOIN hiring.job_positions p ON p.default_interviewer_id = s.interviewer_id
            WHERE p.code = 'PY_DEV' AND s.status = 'OPEN' AND s.starts_at > now() + interval '2 hours'"""
    )["n"]
    if slots < 25:
        print(f"warning: only {slots} open interview slots for PY_DEV; re-run the seed to add more", file=sys.stderr)
    return {"ai_provider": health.get("ai_provider"), "backend_version": health.get("version"), "open_py_slots": slots}


def run_bulk(h: Harness) -> list[dict[str, Any]]:
    submitted = []
    for fixture in bulk_fixtures():
        fields = dict(fixture["fields"])
        if fixture.get("cv"):
            fields["cv_ref"] = h.upload_cv(fixture["cv"])
        payload = h.make(fixture["kind"], fixture["first"], fixture["last"], fixture["position"], **fields)
        if fixture.get("phone_format"):
            payload["phone"] = fixture["phone_format"].format(n=payload["phone"][-7:])
        submitted.append((fixture, payload, h.submit(payload)))
    for _, _, c in submitted:
        h.wait_intake(c, timeout=300)
    h.settle(timeout=900)

    rows = []
    for fixture, payload, c in submitted:
        event = h.event(c)
        if event.get("outcome") == "INVALID" or not c.application_id:
            actual, snap, score, ai = (
                "INVALID" if event.get("outcome") == "INVALID" else event.get("outcome"),
                {},
                {},
                {},
            )
        else:
            snap = h.snapshot(c)
            actual = snap.get("status")
            score = h.one(
                "SELECT score, route FROM hiring.application_scores WHERE application_id = %s", c.application_id
            )
            ai = h.one(
                "SELECT status, recommendation FROM hiring.ai_analyses WHERE application_id = %s "
                "ORDER BY created_at DESC LIMIT 1",
                c.application_id,
            )
        # A shortlisted candidate immediately receives an invitation, so it may already be past SHORTLISTED.
        reached = actual
        if fixture["expect"] == "SHORTLISTED" and actual in {"SHORTLISTED", "INTERVIEW_SCHEDULED"}:
            reached = "SHORTLISTED"
        rows.append(
            {
                "kind": fixture["kind"],
                "name": payload["full_name"],
                "position": fixture["position"],
                "inputs": {
                    k: payload.get(k) for k in ("phone", "expected_salary", "experience_years", "available_from")
                },
                "expected": fixture["expect"],
                "actual": actual,
                "score": float(score["score"]) if score.get("score") is not None else None,
                "route": score.get("route"),
                "ai": ai.get("recommendation") or ai.get("status"),
                "reason": (snap.get("review_reason") or event.get("outcome_reason") or "")[:160],
                "application_code": snap.get("application_code"),
                "ok": reached == fixture["expect"],
            }
        )
    return rows


def run_scenarios(h: Harness, only: set[int] | None) -> list[dict[str, Any]]:
    results = []
    for sc in sorted(SCENARIOS, key=lambda s: s.number):
        if only and sc.number not in only:
            continue
        proof = Proof(h)
        started = time.monotonic()
        error = None
        try:
            sc.run(proof)
        except Exception as exc:  # report every failure, keep going
            error = f"{type(exc).__name__}: {exc}"
            if not isinstance(exc, AssertionError):
                proof.note(traceback.format_exc(limit=3))
        seconds = round(time.monotonic() - started, 1)
        print(
            f"[{'PASS' if error is None else 'FAIL'}] {sc.number:>2} {sc.title} ({seconds}s)"
            + (f"  -> {error}" if error else "")
        )
        results.append(
            {
                "number": sc.number,
                "title": sc.title,
                "passed": error is None,
                "error": error,
                "seconds": seconds,
                "evidence": proof.evidence,
            }
        )
    return results


def write_report(meta: dict[str, Any], bulk: list[dict[str, Any]], scenarios: list[dict[str, Any]]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "scenario-report.json").write_text(
        json.dumps({"meta": meta, "bulk": bulk, "scenarios": scenarios}, indent=2, default=str), encoding="utf-8"
    )
    passed = sum(s["passed"] for s in scenarios)
    bulk_ok = sum(r["ok"] for r in bulk)
    lines = [
        "# Scenario test report",
        "",
        f"Run `{meta['run_id']}` on {meta['finished_at']} · AI provider `{meta['ai_provider']}` · "
        f"duration {meta['duration_min']} min",
        "",
        f"**Scenarios: {passed}/{len(scenarios)} passed** · **Bulk screening: {bulk_ok}/{len(bulk)} as expected**",
        "",
        "Everything was driven through public interfaces (intake webhook, CV upload, signed-link portal API, "
        "staff API, "
        "ops webhooks). Evidence comes from PostgreSQL (status history, logs, error queue, notifications) and Mailpit. "
        "Waiting days was replaced by moving timers' `run_at` to now (time travel touches scheduling data only).",
        "",
        "| # | Scenario | Result | Time |",
        "|---|---|---|---|",
    ]
    for s in scenarios:
        lines.append(f"| {s['number']} | {s['title']} | {'pass' if s['passed'] else '**FAIL**'} | {s['seconds']} s |")
    if bulk:
        counts = Counter((r["position"], r["actual"]) for r in bulk)
        lines += [
            "",
            f"## Bulk screening of {len(bulk)} fictional applications",
            "",
            "| Position | Outcome | Count |",
            "|---|---|---|",
        ]
        lines += [f"| {pos} | {status} | {n} |" for (pos, status), n in sorted(counts.items(), key=str)]
        lines += [
            "",
            "| Application | Profile | Inputs | Expected | Actual | Rule score | AI (advisory) | OK |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in bulk:
            inputs = ", ".join(f"{v}" for v in r["inputs"].values() if v not in (None, ""))
            lines.append(
                f"| {r['application_code'] or '-'} {r['name']} | {r['kind']} | {inputs} | {r['expected']} | "
                f"{r['actual']} | {'' if r['score'] is None else r['score']} {r['route'] or ''} | {r['ai'] or '-'} | "
                f"{'yes' if r['ok'] else '**no**'} |"
            )
    lines += ["", "## Evidence per scenario"]
    for s in scenarios:
        lines += ["", f"### {s['number']}. {s['title']} - {'pass' if s['passed'] else 'FAIL'}", ""]
        lines += [
            f"- {e}" if not e.startswith(("PASS ", "FAIL ")) else f"- {'✅' if e.startswith('PASS') else '❌'} {e[5:]}"
            for e in s["evidence"]
        ]
        if s["error"]:
            lines.append(f"- **Error:** {s['error']}")
    path = REPORT_DIR / "scenario-report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="comma-separated scenario numbers")
    parser.add_argument("--skip-bulk", action="store_true", help="skip the 42-application screening run")
    args = parser.parse_args()
    only = {int(x) for x in args.only.split(",")} if args.only else None

    h = Harness()
    started = time.monotonic()
    meta = {"run_id": h.run, **preflight(h)}
    print(f"run {h.run}: AI provider {meta['ai_provider']}, {meta['open_py_slots']} open PY_DEV slots")
    bulk = [] if args.skip_bulk or only else run_bulk(h)
    if bulk:
        print(f"bulk: {sum(r['ok'] for r in bulk)}/{len(bulk)} applications reached the expected outcome")
        for r in bulk:
            if not r["ok"]:
                print(
                    f"  unexpected: {r['kind']} {r['name']}: expected {r['expected']}, "
                    f"got {r['actual']} ({r['reason']})"
                )
    scenarios = run_scenarios(h, only)
    meta["finished_at"] = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    meta["duration_min"] = round((time.monotonic() - started) / 60, 1)
    path = write_report(meta, bulk, scenarios)
    print(f"report: {path}")
    return 0 if all(s["passed"] for s in scenarios) and all(r["ok"] for r in bulk) else 1


if __name__ == "__main__":
    sys.exit(main())
