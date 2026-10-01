# Architecture

NovaTech Hiring Automation takes a job application from submission to an onboarded employee. It is built so
that a failure at any point is visible, recoverable and never creates duplicate business records.

## 1. Guiding rule: each tool does the job it is best at

| Question | Owner | Why |
|---|---|---|
| Must this rule never break, even when a workflow has a bug? | **PostgreSQL** | Constraints, the state machine and idempotency live in the database, so no caller can bypass them. |
| Does it talk to an outside app, involve time, or coordinate steps? | **n8n** | Webhooks, email/calendar/chat, schedules, retries, human hand-offs, orchestration. |
| Does it need types, unit tests or libraries (parsing, AI, PDF)? | **FastAPI** | Pydantic contracts, pytest, LangChain, document parsing. Endpoints are pure computations. |
| Does a person look at it or click it? | **React + TypeScript** (next phase) | Careers form, candidate links, HR portal, operations dashboard. |

Two consequences:

* **n8n workflows are short-lived.** State lives in the database, not in paused executions. Multi-day waits are
  rows in `ops.scheduled_actions`, picked up by a dispatcher (durable timers).
* **Backend compute endpoints never write.** n8n calls them, then persists the result through `api.*`
  database functions. This keeps the orchestration visible in n8n, and it makes the endpoints safe to retry.

## 2. System context

```mermaid
flowchart LR
    subgraph People
        C[Candidate]
        HR[HR / Recruiter / Managers]
    end

    subgraph Frontend["React + TypeScript (phase 3)"]
        F1[Careers form]
        F2[Candidate links<br/>slot / offer response]
        F3[HR portal + ops dashboard]
    end

    subgraph N8N["n8n 2.40 (orchestration)"]
        WF0[WF-00 Dispatcher]
        WF1[WF-01 Intake]
        WF2[WF-02 Candidate processing]
        WF3[WF-03 Scoring & AI]
        WF4[WF-04 Interviews]
        WF5[WF-05 Offers]
        WF6[WF-06 Onboarding]
        WF7[WF-07 Error & recovery]
        WF8[WF-08 Monitoring & report]
    end

    subgraph API["FastAPI backend"]
        V[/validate/]
        S[/score/]
        AI[/ai-analysis/]
        D[/decide/]
        CV[/public/cv/]
    end

    subgraph DB["PostgreSQL 17 - source of truth"]
        T[(hiring.* tables)]
        O[(ops.* idempotency, logs,<br/>errors, scheduled_actions)]
        FN[[api.* functions<br/>state machine + idempotency]]
        R[[reporting.* views]]
    end

    LLM[(LLM provider<br/>Claude via LangChain)]
    LF[(Langfuse tracing)]
    EXT[(Gmail / Calendar /<br/>Telegram)]

    C --> F1 & F2
    HR --> F3
    F1 -- CV upload --> CV
    F1 -- application + Idempotency-Key --> WF1
    F2 & F3 --> API
    WF1 & WF3 -- HTTP + X-API-Key --> API
    AI --> LLM
    AI -.-> LF
    N8N -- SQL: api.* only --> FN
    API -- read + api.* --> FN
    FN --> T & O
    R --> T & O
    N8N --> EXT
    WF0 -- claims due actions --> O
```

## 3. How one application flows

```mermaid
sequenceDiagram
    autonumber
    participant Form as Careers form
    participant WF1 as n8n WF-01 Intake
    participant BE as FastAPI
    participant DB as PostgreSQL
    participant WF0 as n8n WF-00 Dispatcher
    participant WF3 as n8n WF-03 Screening
    participant LLM as LLM

    Form->>BE: POST /v1/public/cv (PDF/DOCX)
    BE-->>Form: cv_ref (claim check)
    Form->>WF1: POST /webhook/applications (+ Idempotency-Key)
    WF1->>DB: api.register_event() - raw payload stored first
    DB-->>WF1: correlation_id (or "replay")
    WF1-->>Form: 202 Accepted + correlation_id
    WF1->>BE: POST /v1/intake/validate
    BE-->>WF1: normalized contract + issues
    WF1->>DB: api.submit_application() (WF-02)
    Note over DB: candidate upsert, duplicate check,<br/>NEW -> VALIDATING -> VALIDATED,<br/>enqueue SCREEN_APPLICATION (outbox)
    WF0->>DB: api.claim_scheduled_actions()
    WF0->>WF3: SCREEN_APPLICATION
    WF3->>BE: POST /v1/screening/score (rules from DB)
    WF3->>DB: api.record_application_score() -> SCORED
    WF3->>BE: POST /v1/screening/ai-analysis
    BE->>LLM: structured output (PII removed)
    WF3->>DB: api.record_ai_analysis() (advisory)
    WF3->>BE: POST /v1/screening/decide
    WF3->>DB: api.apply_screening_decision() -> SHORTLISTED / REVIEW / REJECTED
    Note over DB: SHORTLISTED enqueues INVITE_TO_INTERVIEW -> WF-04
```

## 4. Reliability building blocks

| Concern | Mechanism | Where |
|---|---|---|
| Never drop a submission | Raw payload persisted before processing (`ops.processed_events`) | `api.register_event` |
| Replayed webhook | Unique `(source, idempotency_key)`; completed events return the stored result | `api.register_event`, `api.submit_application` |
| Duplicate candidate/application | Unique normalized email; one *active* application per candidate and position | partial unique indexes |
| Duplicate offer / employee / booking | One open offer per application; one employee per offer; one booking per slot | partial unique indexes |
| Duplicate emails on replay | `ops.notifications` with a unique dedupe key | `api.begin_notification` |
| Invalid status jumps | Transitions table + actor types + managed transitions; status column guarded by a trigger | `hiring.transition_application` |
| Lost hand-offs between steps | Transactional outbox: status changes enqueue follow-up actions in the same transaction | `on_enter_action` + `ops.scheduled_actions` |
| Reminders that fire after the fact | Timers are rows; replying cancels them; handlers re-check state; stale callers get `STALE_STATE` | `api.cancel_scheduled_actions`, `p_expected_from` |
| Transient failures | Classified errors (`retryable` flag), bounded exponential backoff | backend problem+json, n8n `SWF-01`, `api.complete_scheduled_action` |
| Permanent failures | Dead/error queue with original payload and replay target | `ops.automation_errors`, WF-07 |
| Traceability | Correlation id on every event, history row, log, error, AI trace | `reporting.trace(correlation_id)` |

Details: [reliability.md](reliability.md) · Data model: [database.md](database.md) ·
State machine: [state-machine.md](state-machine.md) · Workflows: [workflows.md](workflows.md)

## 5. AI: narrow and advisory

* One structured call (LangChain `with_structured_output`, native JSON schema). **No agent loop, no tools, no RAG.**
  The applicant controls the input, so a tool-using agent would be a prompt-injection risk. There is also no
  retrieval problem in screening.
* The input is PII-minimised: name, email, phone, national ID, links and personal details are removed.
  The prompt fences application content as untrusted data.
* The output is validated in our own code: shape, 0-10 ranges, enum values and lengths. Database CHECK
  constraints validate it again. One corrective retry, then `FALLBACK`, which routes the candidate to manual review.
* **AI never decides.** The configured rules decide the route. AI can only add a human review, never remove
  one. The database rejects `AI` as an actor for any transition.
* Refusals and provider outages are handled explicitly: a refusal becomes a fallback, and an outage returns a
  retryable 503.

## 6. Deployment model

Single-tenant: one installation per company, run with `docker compose`.

| Container | Image | Exposure |
|---|---|---|
| `db` | postgres:17-alpine (business data) | localhost:5433 |
| `migrate` / `seed` | dbmate 2.36 / postgres (run once, exit) | none |
| `backend` | built from `backend/Dockerfile` (non-root) | localhost:8000 |
| `n8n` | n8nio/n8n:2.40.7 | localhost:5678 |
| `n8n-runners` | n8nio/runners:2.40.7 (external task runners) | internal |
| `n8n-db` | postgres:17-alpine (n8n's own state) | internal |

For production, put a reverse proxy (Caddy/Traefik/nginx) with TLS in front. Expose only `/webhook/*`
and the portal publicly, and keep the n8n editor behind VPN or SSO. Set `N8N_SECURE_COOKIE=true` and
`APP_ENV=production`, and use a managed Postgres with point-in-time recovery.

**Licensing note.** n8n is distributed under the Sustainable Use License. A company running its own
instance for internal use is covered, and building or configuring it for them is a service. Hosting one
n8n for many paying companies as your own SaaS needs a commercial agreement with n8n.

## 7. Security model

* Three database roles:
  * `novatech_owner` owns the objects and is used only by migrations.
  * `n8n_app` and `backend_app` can `SELECT` and `EXECUTE api.*`, and nothing else.
* Every `api.*` function is `SECURITY DEFINER` with a pinned `search_path`. Audit tables are append-only (trigger).
* Service-to-service calls (n8n to the backend) use rotatable `X-API-Key` keys, compared in constant time.
* Secrets live only in `.env` / n8n credentials. The backend container receives only the variables it needs.
* All ports are bound to `127.0.0.1`. CV uploads are type-checked by magic bytes and size-limited, with a
  zip-bomb guard.
* Fault injection (for failure demos) is forced off when `APP_ENV=production`.
