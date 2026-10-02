# NovaTech backend (FastAPI)

Two jobs:

1. **Computation for the n8n workflows** (`X-API-Key`): validation and normalisation, rule-based scoring, advisory AI
   analysis, interview evaluation, offer letter PDFs, signed links and the daily-report text. These endpoints never
   write business data; n8n persists the results through the database `api.*` functions.
2. **Actions taken by people**: candidates, interviewers and approvers through signed email links (`/v1/portal/*`),
   staff through `/v1/staff/*`. Each action is one `api.*` call with that person as the actor, so the database
   enforces ownership, roles and the state machine; then the dispatcher is kicked.

| Group | Endpoints |
|---|---|
| Public | `GET /v1/public/positions`, `POST /v1/public/cv` |
| n8n (service key) | `POST /v1/intake/validate`, `/v1/screening/{score,ai-analysis,decide}`, `/v1/interviews/evaluate`, `/v1/offers/document`, `/v1/links`, `/v1/reports/daily-summary` |
| Signed link (`Authorization: Bearer`) | `GET/POST /v1/portal/interview[/confirm\|/cancel]`, `GET /v1/portal/offer[/document]`, `POST /v1/portal/offer/respond`, `GET/POST /v1/portal/feedback`, `GET/POST /v1/portal/approval` |
| Staff (service key + `X-Staff-Id`) | `POST /v1/staff/applications/{id}/{transition,withdraw,offers}`, `/v1/staff/offers/{id}/{approval,close-negotiation}`, `/v1/staff/interviews/{id}/{feedback,cancel,no-show}`, `/v1/staff/onboarding-tasks/{id}/complete`, `/v1/staff/errors/{id}/replay`, `GET /v1/staff/overview`, `GET /v1/staff/queues/{name}` |
| Ops | `GET /health/live`, `GET /health/ready` |

```
app/core/          settings, logging, errors (problem+json), DB pool, auth, signed links, fault injection, middleware
app/domain/        contracts (Pydantic), normalisation, validation, scoring engine, screening policy,
                   interview evaluation, report numeric guardrail
app/ai/            schemas, prompts, PII redaction, provider-agnostic LangChain client, analyzer
app/repositories/  reference (read-only SQL), hiring (api.* calls on behalf of people)
app/services/      storage (claim check), CV extraction, offer letter PDF, n8n webhooks
app/api/routes/    health, intake, screening, workflow_support, portal, staff
tests/             unit, api, integration (DB)
```

Development runs in Docker (see the root README). Dependencies are locked with `uv` (`uv.lock`); to change them,
edit `pyproject.toml` and run `uv lock` (or run it in the `python:3.12` image as the project does).
