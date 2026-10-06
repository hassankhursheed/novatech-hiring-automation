# Testing

Six layers, from fast and isolated to the whole running system, plus a separate quality evaluation of the AI.

| Layer | What it proves | Where | How to run |
|---|---|---|---|
| Unit (white-box) | Normalisation, validation, scoring engine, screening policy, interview evaluation incl. the AI rules, AI validation/retry/fallback for screening and interviews, the Mistral client (pacing, error classification), n8n client, storage, CV extraction, signed links, report numeric guardrail, offer letter | `backend/tests/unit` | test image (below) |
| API | HTTP contracts, auth (service key, signed links, staff sessions, demo login), problem+json errors, fault injection | `backend/tests/api` (in-memory fakes for the database) | test image |
| Database integration | The rule enforcer itself on a **real PostgreSQL started by testcontainers**: state machine, actor rules, idempotency, timers, candidate status updates, AI assessment storage, error queue, approvals, exactly-once employee, privileges of the runtime roles, every repository query as `backend_app` | `backend/tests/integration` | test image (starts a throwaway `postgres:17-alpine`) |
| Journey (end to end) | One applicant through all 7 hiring steps on the running stack, with every email they receive | `backend/tests/scenarios/journey.py` | `python -m tests.scenarios.journey` |
| Scenarios (end to end) | The 23 scenarios of the brief and 42 fictional applications | `backend/tests/scenarios/run.py` | `python -m tests.scenarios.run` |
| CI | All of the above that needs no running stack, on every push (GitHub Actions) | `.github/workflows/ci.yml` | automatic |
| AI evaluation | Quality and safety of the Mistral answers on 36 recruiter-labelled cases (DeepEval + Langfuse) | `backend/tests/ai_eval` | see [ai-evaluation.md](ai-evaluation.md) |

```bash
docker compose --profile test run --rm backend-tests                 # ruff + unit + API + DB integration
docker compose --profile test run --rm backend-tests pytest --cov=app --cov-report=term   # with coverage
docker compose --profile test run --rm backend-tests python -m tests.scenarios.journey     # the 7 steps, live
docker compose --profile test run --rm backend-tests python -m tests.scenarios.run         # scenarios + 42 apps
docker compose --profile test run --rm backend-tests deepeval test run tests/ai_eval -m ai_eval   # AI evaluation
```

The test container mounts the working tree (`backend/app`, `backend/tests`), so it always tests the current code;
rebuild it only after changing dependencies: `docker compose --profile test build backend-tests`.

### Test database: testcontainers

`tests/integration/conftest.py` starts a fresh PostgreSQL 17 container per test session, creates the three roles as
production does, applies every migration in order (the `-- migrate:up` sections, like dbmate) and the NovaTech
configuration seed. Each test runs in a transaction that is rolled back; the container is removed at the end. No
test ever touches your working data. In Docker the test container reaches the host's Docker engine through the
mounted socket; in CI it runs directly on the runner. Set `DATABASE_URL_TEST` and `DATABASE_URL_OWNER_TEST` to use
an existing database instead.

### Journey check

`python -m tests.scenarios.journey` applies as one candidate (on the reserved test domain `example.com`) and walks
the real interfaces: careers intake, the emailed slot link, HR shortlist/scorecard/decision in the staff API, the
emailed approval and offer links, and onboarding tasks. It prints one line per step and the candidate's inbox:

```
[PASS] 1 Apply: APP-2026-00186 stored; confirmation email sent
[PASS] 2 Screening: rule score 80.00, AI ... -> SCREENING_REVIEW
[PASS] 3 Interview booked: invitation email -> slot booked -> confirmation email
[PASS] 4 Scorecard + evaluation: interview score 84, AI assessment ..., final 82.80 -> INTERVIEW_REVIEW
[PASS] 5 Decision: selected; candidate told the offer is being prepared
[PASS] 6 Offer: approved -> offer email with letter -> accepted
[PASS] 7 Onboarding: employee NT-2026-009, welcome email, 10 tasks completed -> ONBOARDED, closing email
```

## Scenario runner

The runner behaves like the outside world:

* **Inputs only through public interfaces**: the intake webhook (with `Idempotency-Key`, optional `X-Fault-Inject`),
  CV upload, the signed links in the emails (`/v1/portal/*`, exactly as a candidate, interviewer or approver would
  use them), the staff API (`/v1/staff/*`) and the authenticated ops webhooks (`kick`, `onboarding-sweep`).
* **Evidence from the systems of record**: status history, automation logs, the error queue and notifications in
  PostgreSQL, and the actual emails in Mailpit.
* **Time travel instead of waiting**: reminders and expiries are rows in `ops.scheduled_actions`; the runner moves a
  timer's `run_at` to now (and, for offer expiry, the offer's deadline into the past). It never edits a business
  record's status: state still changes only through the workflows and `api.*` functions. Scenario 22 also
  re-delivers an already processed action to prove at-least-once delivery is harmless.
* **Configuration changes like an HR admin**: scenario 6 changes a scoring rule with the SQL documented in
  [configuration.md](configuration.md) and restores it afterwards.

Requirements: a development stack (`FAULT_INJECTION_ENABLED=true`, `LLM_PROVIDER=stub` for reproducible AI answers)
with the n8n workflows deployed. The runner creates its applicants on `example.com`; run it on a test installation,
not on the one HR uses. The runner switches on `dev.fault_injection_enabled` (lets the intake carry an
injected fault to later steps) and warns when fewer than 25 interview slots are open (re-run the seed to add more).

Output: `reports/scenario-report.md` (summary, bulk screening table, evidence per scenario) and
`reports/scenario-report.json`. The exit code is 1 if any scenario or application did not behave as expected.
The latest report is kept in [scenario-report.md](scenario-report.md).

### Scenario map

| # | Scenario | Proven by |
|---|---|---|
| 1 | Strong candidate becomes an employee | intake → screening → slot booked via link → scorecard via link → evaluation → offer drafted → L1 approval via link → PDF + offer email → accept → employee `NT-YYYY-NNN`, tasks, welcome → all tasks done → `ONBOARDED` |
| 2 | Incomplete application → manual review with reason | validator `NEEDS_REVIEW`, review reason, no automatic score, recruiters alerted once |
| 3 | Duplicate submission detected | same person and position, new key → `DUPLICATE`, still one application, candidate told |
| 4 | Same webhook replayed | same key and body → 200 with the original correlation id, `receive_count` 2, one email; same key with another body → 409; no key → 400 |
| 5 | Weak candidate rejected by rules | rule route `REJECT` → `REJECTED`, one rejection notice |
| 6 | Rule changed in the database | scoring version bumps automatically; the same profile moves from SHORTLIST to REVIEW with no workflow change |
| 7 | Malformed AI output | `ai:malformed` → one corrective retry → `FALLBACK` → manual review; rule score unaffected |
| 8 | Retryable failure recovers | `score:http_503@2` → two `RETRY_ATTEMPT`, one `RETRY_RECOVERED`, nothing queued |
| 9 | Non-retryable failure → error queue | `score:http_400` → one `NON_RETRYABLE` error with the original action, no retries |
| 10 | Retries exhausted → dead queue | `score:http_503` on every attempt → dispatcher backoff × 5 → `ACTION_ATTEMPTS_EXHAUSTED` |
| 11 | Corrected item replayed without duplicates | fault cleared → HR replays both errors via the staff API → `RESOLVED` (attributed), one score row, one email |
| 12 | Interview reminder (and expiry) | reminder once; expiry → `SCREENING_REVIEW`, recruiters alerted |
| 13 | Interview cancellation | candidate cancels via link → slot reopened, feedback timers cancelled, round 2 invitation |
| 14 | Feedback reminder | interviewer reminded, then HR escalation |
| 15 | Candidate fails the interview | low scorecard → final score below threshold → `REJECTED`, notice sent |
| 16 | Standard approval | one level, letter generated, offer emailed once |
| 17 | Two-level approval | salary above threshold → two levels; the L1 approver is refused at L2; sent after L2 |
| 18 | Rejected approval | reason recorded, nothing sent to the candidate, HR asked to revise, revision 2 drafted |
| 19 | Decline | `DECLINED`, offer timers cancelled, HR informed, candidate thanked |
| 20 | Negotiate | `NEGOTIATION`, HR notified + candidate acknowledged, revision 2 approved and sent, revision 1 superseded |
| 21 | Offer expiry | reminder, deadline passes → `OFFER_EXPIRED`, late acceptance refused |
| 22 | Onboarding exactly once | `START_ONBOARDING` delivered twice → one employee, one welcome email (second suppressed) |
| 23 | Overdue onboarding task | task visible in `v_overdue_onboarding_tasks`, owner reminded once by the sweep, counted in daily metrics |
