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
| Orchestration | **n8n 2.40.7** (+ external task runners) | Workflows WF-00…WF-08, integrations, timers, retries, error handling |
| Source of truth | **PostgreSQL 17** | Tables, constraints, state machine, idempotency, audit log, reporting views |
| Business logic | **FastAPI** (Python 3.12, Pydantic v2) | Validation/normalisation, scoring engine, AI analysis, CV extraction |
| AI | **LangChain** + Claude (`claude-opus-5`, provider-configurable), Langfuse tracing | Advisory, structured, validated candidate analysis |
| Portal | **React + TypeScript** (Vite, TanStack Query, Tailwind; nginx in Docker) | Careers form, candidate and staff link pages, HR portal and operations dashboard |

## Quick start (Windows, Docker Desktop)

```powershell
# 1. Generate .env with strong random secrets
powershell -ExecutionPolicy Bypass -File scripts\new-env.ps1

# 2. (optional) add an LLM key to .env, e.g. ANTHROPIC_API_KEY=...  (without a key, AI analyses fall back to manual review)

# 3. Build and start everything (migrations and seed data run automatically)
docker compose up -d --build

# 4. Check status
docker compose ps

# 5. Open http://localhost:5678, create the n8n owner account and the three credentials (docs/n8n-setup.md),
#    then deploy all workflows:
powershell -ExecutionPolicy Bypass -File scripts\n8n-deploy.ps1 --exported
```

Linux/macOS: `sh scripts/new-env.sh` then the same `docker compose` commands.

| Service | URL | Notes |
|---|---|---|
| n8n editor | http://localhost:5678 | create the owner account on first visit, see [docs/n8n-setup.md](docs/n8n-setup.md) |
| Backend API docs | http://localhost:8000/docs | Swagger UI (development only) |
| Backend health | http://localhost:8000/health/ready | `database: true` when ready |
| Portal | http://localhost:5173 | **Careers** (job seekers apply) and **Staff portal** (sign in with **Use demo login**: the HR/recruiter account and the password from `STAFF_DEMO_PASSWORD` in `.env`) |
| Mailpit (dev inbox) | http://localhost:8025 | every email sent in development lands here |
| Business database | `localhost:5433` | db `novatech`; connect with any SQL client |

Useful commands:

```powershell
docker compose logs -f backend n8n                       # follow logs
docker compose --profile test run --rm backend-tests     # lint + unit + API + DB integration tests
docker compose --profile test run --rm backend-tests python -m tests.scenarios.run   # 23 scenarios end to end
docker compose run --rm migrate                          # apply new migrations
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
n8n/src/            workflow-as-code for WF-00 and WF-04..WF-08 (node n8n/src/build.mjs)
scripts/            .env generator, n8n deploy / export
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
| [docs/testing.md](docs/testing.md) | Test layers, scenario runner, map of the 23 scenarios |
| [docs/scenario-report.md](docs/scenario-report.md) | Latest end-to-end run: 23 scenarios + 42 fictional applications, with evidence |

## Testing

`docker compose --profile test run --rm backend-tests` runs `ruff` and 127 tests:

* **Unit:**
  * normalisation (PK phone numbers, lakh salaries, day-first dates, skill aliases)
  * validation outcomes and scoring rules/thresholds
  * the screening policy ("AI can only add review")
  * AI retry/fallback/refusal paths, PII redaction, error mapping
* **API:**
  * API-key auth and key rotation, problem+json errors
  * correlation-id propagation, attempt-aware fault injection
  * CV upload type and size checks
* **Database integration** (each test rolled back):
  * event replay and idempotency-key reuse, duplicate detection, phone-match flagging
  * forbidden transitions (invalid pair, AI actor, wrong actor, managed bypass, stale state)
  * timers cancelled on close, scheduler backoff and exhaustion into the error queue
  * notification dedupe, error dedupe and replay
  * privileges, append-only history, typed settings, rule-version bumps

## Project status

| Step | Scope | Status |
|---|---|---|
| 0 | Checkpoint: architecture, ERD, workflow responsibilities | done, in `docs/` (submit for review) |
| 1 | Foundation: Docker stack, n8n 2.x local setup, database schema + state machine + idempotency + scheduling + reporting, backend intake/scoring/AI | **done** |
| 2 | Interview + offer + onboarding database functions; backend: interview evaluation, offer PDF, signed links, candidate portal API, staff API, report summary | **done** (175 unit/API + 41 DB integration tests) |
| 3 | n8n workflows WF-00…WF-08 + sub-workflows (exported to `n8n/workflows`, WF-00/04–08 also as code in `n8n/src`) | **done**, verified end to end: application → interview → approved offer → accepted → onboarded employee |
| 4 | React + TypeScript careers form, candidate pages, HR portal, ops dashboard; passwordless staff sign-in | **done** (`frontend/`, served on http://localhost:5173) |
| 5 | 42 fictional applications, scenario runner for the 23 mandatory scenarios, test report | **done**: [docs/scenario-report.md](docs/scenario-report.md) (23/23, 42/42); demo walkthrough next |

## Production notes

* Single-tenant: one installation per company keeps candidate data isolated.
* Put a TLS reverse proxy in front. Expose only `/webhook/*` and the portal; keep the n8n editor behind VPN or SSO.
* Use managed Postgres with point-in-time recovery, and back up `N8N_ENCRYPTION_KEY`.
* Use a transactional email provider on the company domain (SPF/DKIM) instead of Mailpit.
* Recruitment AI is regulated as high-risk in some jurisdictions (e.g. the EU AI Act; NYC Local Law 144 requires
  bias audits). This system keeps AI advisory, minimises PII, logs model/prompt versions, and always leaves the
  decision to a human.
* n8n is under the Sustainable Use License: fine for a company's internal use. Hosting it for many paying
  companies as a SaaS needs a commercial agreement with n8n.
