# NovaTech Hiring Automation

End-to-end recruitment and onboarding automation: application intake, validation, duplicate protection,
rule-based scoring, advisory AI analysis, interviews, two-level offer approval, onboarding, monitoring and a daily
management report.

Built as a production-style system:
* explicit state machine
* idempotent everywhere
* retries with a dead-letter queue and safe replay
* end-to-end traceability by correlation id
* AI that is validated and advisory, never the decision-maker

| Layer | Technology | Role |
|---|---|---|
| Orchestration | **n8n 2.41.7** (+ external task runners) | Workflows WF-00…WF-09, integrations, timers, retries, error handling |
| Source of truth | **PostgreSQL 17** | Tables, constraints, state machine, idempotency, audit log, reporting views |
| Business logic | **FastAPI** (Python 3.12, Pydantic v2) | Validation/normalisation, scoring engine, AI analysis, CV extraction |
| AI | **Mistral** via LangChain (`ministral-14b-latest`; n8n: `ministral-8b-latest`), **Langfuse** tracing, **DeepEval** evaluation | Advisory, structured, validated screening analysis and interview assessment; AI-drafted interview questions |
| Portal | **React + TypeScript** (Vite, TanStack Query, Tailwind; nginx in Docker) | Careers form, candidate and staff link pages, HR portal and operations dashboard |

## Quick start (Windows, Docker Desktop)

```powershell
# 1. Generate .env with strong random secrets
powershell -ExecutionPolicy Bypass -File scripts\new-env.ps1

# 2. Paste your Mistral API key into .env: MISTRAL_API_KEY=...   (without a key the AI is off and cases go to a person)

# 3. Build and start everything (migrations and seed data run automatically)
docker compose up -d --build

# 4. Check status
docker compose ps

# 5. Open http://localhost:5678, create the n8n owner account and the credentials (docs/n8n-setup.md),
#    then deploy all workflows and give n8n its Mistral key:
powershell -ExecutionPolicy Bypass -File scripts\n8n-deploy.ps1
powershell -ExecutionPolicy Bypass -File scripts\n8n-mistral-key.ps1

# 6. (optional) AI tracing and evaluation datasets: self-hosted Langfuse on http://localhost:3000
docker compose --profile observability up -d
```

**Manual setup that stays with you** (details in [docs/n8n-setup.md](docs/n8n-setup.md)):
* **Real email:** out of the box every email goes to the Mailpit test inbox. To reach candidates, edit the n8n
  credential *NovaTech SMTP (outgoing email)* (e.g. Gmail: `smtp.gmail.com`, port 465, SSL, an App password) and set
  `MAIL_FROM_ADDRESS` in `.env`.
* **Mistral key in n8n:** n8n has its own key: set `N8N_MISTRAL_API_KEY` in `.env` and run `scripts\n8n-mistral-key.ps1`
  (or paste it into the credential *Mistral AI (n8n)*). The backend key is never used by n8n.

Linux/macOS: `sh scripts/new-env.sh` then the same `docker compose` commands.

| Service | URL | Notes |
|---|---|---|
| n8n editor | http://localhost:5678 | create the owner account on first visit, see [docs/n8n-setup.md](docs/n8n-setup.md) |
| Backend API docs | http://localhost:8000/docs | Swagger UI (development only) |
| Backend health | http://localhost:8000/health/ready | `database: true` when ready |
| Portal | http://localhost:5173 | **Careers** (job seekers apply) and **Staff portal** (sign in with **Use demo login**: the HR/recruiter account and the password from `STAFF_DEMO_PASSWORD` in `.env`) |
| Mailpit (dev inbox) | http://localhost:8025 | every email lands here until a real SMTP account is configured |
| Langfuse (optional) | http://localhost:3000 | AI traces, datasets, experiments; sign in with `LANGFUSE_INIT_USER_EMAIL` / `LANGFUSE_INIT_USER_PASSWORD` from `.env` |
| Business database | `localhost:5433` | db `novatech`; connect with any SQL client |

Useful commands:

```powershell
docker compose logs -f backend n8n                       # follow logs
docker compose --profile test run --rm backend-tests     # lint + unit + API + DB integration (testcontainers)
docker compose --profile test run --rm backend-tests python -m tests.scenarios.journey   # the 7 hiring steps, live
docker compose --profile test run --rm backend-tests deepeval test run tests/ai_eval -m ai_eval   # AI evaluation
docker compose run --rm migrate                          # apply new migrations
sh scripts/purge-applications.sh --dry-run               # see which test applications (example.com etc.) would be removed
sh scripts/purge-applications.sh                         # remove them (backup first, asks to confirm); --all = clean go-live
docker compose down                                      # stop (data is kept in volumes)
docker compose down -v                                   # stop AND delete all data (fresh start)
```

## Repository layout

```
backend/            FastAPI service (app/, tests/, Dockerfile, pyproject.toml, uv.lock)
frontend/           React + TypeScript portal (Vite; Dockerfile serves it from unprivileged nginx)
db/migrations/      SQL migrations (dbmate): schema, state machine, api.* functions, reporting, privileges
db/seed/            NovaTech configuration: staff, positions, scoring rules, skills, onboarding, interview slots
db/bootstrap/       role bootstrap for managed Postgres (Supabase / Cloud SQL / RDS)
infra/              container init scripts (DB roles, seeding)
n8n/workflows/      exported n8n workflows (version-controlled, no credentials)
n8n/src/            workflow-as-code (WF-00, WF-02, WF-04..WF-09; notes for all) and the build (node n8n/src/build.mjs)
scripts/            .env generator, n8n deploy / export, n8n Mistral key, test-data cleanup
db/maintenance/     purge_applications.sql (used by scripts/purge-applications)
.github/workflows/  CI (lint, tests with testcontainers + coverage, portal and workflow builds) and the AI evaluation
docs/               architecture, database/ERD, state machine, workflows, reliability, configuration, n8n setup
```

## Documentation

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Tool responsibilities, system diagram, application flow, security and deployment model |
| [docs/database.md](docs/database.md) | ERD, schemas, constraints that enforce business rules, write API reference |
| [docs/state-machine.md](docs/state-machine.md) | Statuses, allowed transitions, actors, on-enter actions, deviations from the brief |
| [docs/workflows.md](docs/workflows.md) | WF-00…WF-08 and sub-workflows: triggers, steps, owned transitions, scenario coverage |
| [docs/reliability.md](docs/reliability.md) | Idempotency strategy, error classification, retry policy, dead queue, replay, timers, fault injection |
| [docs/configuration.md](docs/configuration.md) | Environment variables, business settings, scoring rules |
| [docs/n8n-setup.md](docs/n8n-setup.md) | First login, credentials, conventions, deploy/export, hardening |
| [docs/testing.md](docs/testing.md) | Test layers (unit, API, testcontainers, journey, scenarios, CI), map of the 23 scenarios |
| [docs/ai-evaluation.md](docs/ai-evaluation.md) | Mistral models and limits, Langfuse tracing, DeepEval + Langfuse evaluation, results |
| [docs/scenario-report.md](docs/scenario-report.md) | Latest end-to-end run: 23 scenarios + 42 fictional applications, with evidence |

## Testing

| Suite | Result |
|---|---|
| Backend: unit, API and database integration (white-box; PostgreSQL via testcontainers) | **243 passed** (locally and in GitHub Actions), 90 % line coverage, ruff clean |
| Journey: one applicant through all 7 steps on the running stack | **7/7 steps** with the AI off (human review paths) and with live Mistral (AI shortlist, AI interview assessment, AI interview questions); the candidate is emailed at every step, 0 errors |
| Scenarios: the 23 brief scenarios + 42 applications | 23/23, 42/42 ([docs/scenario-report.md](docs/scenario-report.md)) |
| AI evaluation (DeepEval, live Mistral `ministral-14b-latest`, 36 labelled cases) | 100 % valid output, 100 % acceptable recommendations, 100 % safety, 0 personal-data leaks ([docs/ai-evaluation.md](docs/ai-evaluation.md)) |

Details in [docs/testing.md](docs/testing.md).

## Project status

| Step | Scope | Status |
|---|---|---|
| 0 | Checkpoint: architecture, ERD, workflow responsibilities | done, in `docs/` (submit for review) |
| 1 | Foundation: Docker stack, n8n 2.x local setup, database schema + state machine + idempotency + scheduling + reporting, backend intake/scoring/AI | **done** |
| 2 | Interview + offer + onboarding database functions; backend: interview evaluation, offer PDF, signed links, candidate portal API, staff API, report summary | **done** (175 unit/API + 41 DB integration tests) |
| 3 | n8n workflows WF-00…WF-08 + sub-workflows (exported to `n8n/workflows`, WF-00/04–08 also as code in `n8n/src`) | **done**, verified end to end: application → interview → approved offer → accepted → onboarded employee |
| 4 | React + TypeScript careers form, candidate pages, HR portal, ops dashboard; passwordless staff sign-in | **done** (`frontend/`, served on http://localhost:5173) |
| 5 | 42 fictional applications, scenario runner for the 23 mandatory scenarios, test report | **done**: [docs/scenario-report.md](docs/scenario-report.md) (23/23, 42/42) |
| 6 | Mistral AI (screening, interview assessment, n8n interview questions), candidate email at every step, HR scorecard entry, testcontainers, Langfuse, DeepEval, CI, n8n 2.41.7 | **done** ([docs/ai-evaluation.md](docs/ai-evaluation.md)) |

## Production notes

* Single-tenant: one installation per company keeps candidate data isolated.
* Put a TLS reverse proxy in front. Expose only `/webhook/*` and the portal; keep the n8n editor behind VPN or SSO.
* Use managed Postgres with point-in-time recovery, and back up `N8N_ENCRYPTION_KEY`.
* Use a transactional email provider on the company domain (SPF/DKIM) instead of Mailpit (one credential in n8n).
* Recruitment AI is regulated as high-risk in some jurisdictions (e.g. the EU AI Act; NYC Local Law 144 requires
  bias audits). This system keeps AI advisory, minimises PII, logs model/prompt versions, and always leaves the
  decision to a human.
* n8n is under the Sustainable Use License: fine for a company's internal use. Hosting it for many paying
  companies as a SaaS needs a commercial agreement with n8n.
