# Database design

PostgreSQL 17 (Supabase-compatible, plain Postgres features only). Migrations live in [`db/migrations`](../db/migrations)
and are applied with [dbmate](https://github.com/amacneil/dbmate). Configuration seeds are in [`db/seed`](../db/seed).

## Schemas

| Schema | Contents | Runtime access (`n8n_app`, `backend_app`) |
|---|---|---|
| `hiring` | Business domain: candidates, applications, interviews, offers, employees, configuration | `SELECT` only |
| `ops` | Automation infrastructure: processed events, executions, audit log, error queue, scheduled actions, notifications, reports | `SELECT` only |
| `api` | `SECURITY DEFINER` write functions: the **only** way to change data | `EXECUTE` |
| `reporting` | Views and functions for the dashboard, daily report and tracing | `SELECT` / `EXECUTE` |

## Entity relationship diagram

```mermaid
erDiagram
    JOB_POSITIONS ||--|| SCORING_CONFIGS : "has thresholds"
    SCORING_CONFIGS ||--o{ SCORING_RULES : "has rules"
    STAFF_MEMBERS ||--o{ JOB_POSITIONS : "hiring manager"
    CANDIDATES ||--o{ APPLICATIONS : submits
    JOB_POSITIONS ||--o{ APPLICATIONS : "applied for"
    PROCESSED_EVENTS ||--o| APPLICATIONS : "source event"
    APPLICATIONS ||--o{ CANDIDATE_STATUS_HISTORY : "status changes"
    APPLICATION_STATUSES ||--o{ STATUS_TRANSITIONS : "from / to"
    APPLICATIONS ||--o{ APPLICATION_SCORES : scored
    APPLICATIONS ||--o{ AI_ANALYSES : "advisory analysis"
    APPLICATIONS ||--o{ INTERVIEWS : "interview rounds"
    STAFF_MEMBERS ||--o{ INTERVIEW_SLOTS : "availability"
    INTERVIEW_SLOTS ||--o| INTERVIEWS : "booked by"
    INTERVIEWS ||--o| INTERVIEW_FEEDBACK : "feedback"
    APPLICATIONS ||--o{ OFFERS : "offer revisions"
    OFFERS ||--o{ OFFER_APPROVALS : "L1 / L2"
    STAFF_MEMBERS ||--o{ OFFER_APPROVALS : approves
    OFFERS ||--o| EMPLOYEES : "creates exactly one"
    EMPLOYEES ||--o{ ONBOARDING_TASKS : has
    ONBOARDING_TASK_TEMPLATES ||--o{ ONBOARDING_TASKS : "instantiated from"
    APPLICATIONS ||--o{ SCHEDULED_ACTIONS : "timers / outbox"
    APPLICATIONS ||--o{ NOTIFICATIONS : "messages sent"

    CANDIDATES {
        uuid id PK
        text candidate_code UK "CAN-2026-0001"
        text email UK "normalised, partial unique"
        text phone_e164 "indexed; match flags review"
        timestamptz consent_at
    }
    APPLICATIONS {
        uuid id PK
        text application_code UK "APP-2026-00001"
        uuid candidate_id FK
        uuid job_position_id FK
        uuid source_event_id FK,UK
        text correlation_id UK
        text status FK "guarded by trigger"
        numeric application_score
        numeric final_score
        int version "optimistic concurrency"
    }
    CANDIDATE_STATUS_HISTORY {
        bigint id PK
        uuid application_id FK
        text from_status
        text to_status
        text actor_type "SYSTEM|STAFF|CANDIDATE"
        text actor_id
        text workflow_name
        text execution_id
        text correlation_id
    }
    STATUS_TRANSITIONS {
        text from_status PK,FK
        text to_status PK,FK
        text_array allowed_actor_types
        text managed_by
    }
    SCORING_RULES {
        uuid id PK
        uuid job_position_id FK
        text rule_key "UK per position"
        text rule_type
        text_array match_terms
        int points
    }
    APPLICATION_SCORES {
        uuid id PK
        uuid application_id FK
        int scoring_version "UK with app + input_hash"
        numeric score
        jsonb breakdown "per-rule explanation"
    }
    AI_ANALYSES {
        uuid id PK
        uuid application_id FK
        text status "COMPLETED|FALLBACK"
        smallint technical_strength "CHECK 0-10"
        text recommendation "advisory"
        text prompt_version
    }
    INTERVIEWS {
        uuid id PK
        uuid application_id FK "UK with round"
        uuid slot_id FK "one live booking per slot"
        text status
    }
    OFFERS {
        uuid id PK
        uuid application_id FK "one OPEN offer per application"
        smallint revision
        bigint monthly_salary
        smallint required_approval_levels "2 above threshold"
        text status
    }
    OFFER_APPROVALS {
        uuid id PK
        uuid offer_id FK "UK with level"
        smallint level
        uuid approver_id FK "UK with offer"
        text decision
    }
    EMPLOYEES {
        uuid id PK
        text employee_code UK "NT-2026-001"
        uuid offer_id FK,UK
        uuid candidate_id FK,UK
    }
    PROCESSED_EVENTS {
        uuid id PK
        text source "UK with idempotency_key"
        text idempotency_key
        text correlation_id UK
        jsonb payload "raw, stored first"
        text outcome
        int receive_count "replays seen"
    }
    SCHEDULED_ACTIONS {
        uuid id PK
        text action_type
        timestamptz run_at
        text status
        text dedupe_key UK
        smallint attempts
    }
    NOTIFICATIONS {
        uuid id PK
        text dedupe_key UK
        text channel
        text status
    }
```

Operational tables without foreign keys to the domain (by design, so they can log anything):
`ops.workflow_executions`, `ops.automation_logs` (append-only), `ops.automation_errors` (dead/error queue),
`ops.daily_reports`, `ops.code_counters`.

## Brief table mapping

| Brief | Table |
|---|---|
| Candidates | `hiring.candidates` |
| JobPositions | `hiring.job_positions` |
| Applications | `hiring.applications` |
| CandidateStatusHistory | `hiring.candidate_status_history` |
| ScoringRules | `hiring.scoring_rules` + `hiring.scoring_configs` (thresholds, version) |
| Interviews | `hiring.interviews` + `hiring.interview_slots` |
| InterviewFeedback | `hiring.interview_feedback` |
| Offers | `hiring.offers` |
| OfferApprovals | `hiring.offer_approvals` |
| Employees | `hiring.employees` |
| OnboardingTasks | `hiring.onboarding_tasks` + `hiring.onboarding_task_templates` |
| ProcessedEvents / IdempotencyKeys | `ops.processed_events` |
| WorkflowExecutions | `ops.workflow_executions` |
| AutomationLogs | `ops.automation_logs` |
| AutomationErrors | `ops.automation_errors` |
| (added) | `hiring.settings`, `hiring.skill_aliases`, `hiring.application_statuses`, `hiring.status_transitions`, `hiring.ai_analyses`, `hiring.application_scores`, `ops.scheduled_actions`, `ops.notifications`, `ops.daily_reports` |

## Constraints that carry business rules

| Rule | Enforcement |
|---|---|
| One active application per candidate and position | `applications_one_active_per_position_uq` (partial unique, excludes closed statuses) |
| An event is processed once | `processed_events_idempotency_uq (source, idempotency_key)` |
| One open offer per application | `offers_one_open_per_application_uq` (partial unique) |
| One employee per offer / candidate / application | `employees.offer_id`, `candidate_id`, `application_id` unique |
| One booking per interview slot; no overlapping slots | `interviews_one_booking_per_slot_uq`, `interview_slots_no_overlap` (GiST exclusion) |
| One interview per application round | `interviews_round_uq (application_id, round)` |
| One approval per level; an approver approves only once per offer | `offer_approvals_level_uq`, `offer_approvals_approver_uq` |
| A rejection needs a reason | `offer_approvals` CHECK |
| AI scores within 0-10 | `ai_analyses` CHECKs |
| Status changes only through the state machine | `applications_status_guard` trigger |
| History, audit log and approvals cannot be edited | `ops.forbid_mutation()` append-only triggers |
| Configuration values are well-typed | `settings_validate` trigger |
| Rule edits are versioned | `scoring_rules_bump_version`, `scoring_configs_bump_version` triggers |

## Identifiers

Internal primary keys are UUIDs. Human-readable business codes come from `ops.next_code()`, which uses a
per-scope counter row, so they are gap-free under normal operation and safe under concurrency:
`CAN-2026-0001`, `APP-2026-00001`, `NT-2026-001`, `COR-20260929-0001`. They are never computed in n8n.

## Write API (`api` schema)

Every function takes `p_ctx jsonb`: `{actor_type, actor_id, correlation_id, workflow_name, workflow_version, execution_id, retry_count}`.

| Function | Purpose | Idempotent |
|---|---|---|
| `register_event(source, key, type, payload, ctx)` | Store an inbound event first; detect replays | yes |
| `fail_event(event_id, reason, ctx)` | Mark an event FAILED (infrastructure error) | yes |
| `submit_application(event_id, validation, ctx)` | Candidate upsert, duplicate check, create application | yes (by event) |
| `record_application_score(app_id, score, ctx)` | Store the rule score, VALIDATED -> SCORED | yes (version + input hash) |
| `record_ai_analysis(app_id, analysis, ctx)` | Store advisory AI output; upgrade FALLBACK -> COMPLETED | yes |
| `apply_screening_decision(app_id, decision, ctx)` | SCORED -> SHORTLISTED / SCREENING_REVIEW / REJECTED | yes |
| `transition_application_status(app_id, to, reason, ctx, expected_from)` | Human decisions (review queues, closing) | yes |
| `application_snapshot(app_id)` | Current state + candidate + position, for templating and state re-checks | read |
| `schedule_action / cancel_scheduled_actions / claim_scheduled_actions / complete_scheduled_action / requeue_scheduled_action` | Durable timers + outbox | yes |
| `begin_notification / finish_notification` | Send each message at most once per dedupe key | yes |
| `start_workflow_execution / finish_workflow_execution` | Execution tracking | yes |
| `record_error / begin_error_replay / resolve_error` | Dead/error queue and safe replay | yes (fingerprint) |
| `log_action(ctx, entity, id, action, outcome, details)` | Audit log entries from workflows | append |
| `save_daily_report / mark_daily_report_delivered` | Daily report, sent once per date | yes |

Interview, offer and onboarding functions (`create_interview_invitation`, `confirm_interview_slot`,
`submit_interview_feedback`, `apply_interview_decision`, `create_offer`, `decide_offer_approval`,
`mark_offer_sent`, `respond_to_offer`, `expire_offer`, `create_employee_from_offer`,
`complete_onboarding_task`, `withdraw_application`) are part of build step 2. Their managed transitions are
already declared in `hiring.status_transitions`.

## Reporting

| Object | Use |
|---|---|
| `reporting.daily_metrics(date)` | Every number in the daily management report (flow + stock metrics) |
| `reporting.v_ops_overview` | One-row health summary for the dashboard |
| `reporting.v_pipeline_by_status` | Funnel by status |
| `reporting.v_manual_review_queue` | Screening / interview reviews waiting for a human |
| `reporting.v_pending_offer_approvals` | Offers waiting for L1/L2 |
| `reporting.v_pending_interview_feedback` | Interviews finished without feedback |
| `reporting.v_open_offers` | Sent / negotiating / expired offers |
| `reporting.v_overdue_onboarding_tasks` | Overdue onboarding tasks |
| `reporting.v_error_queue` | Open / replaying errors |
| `reporting.v_scheduled_actions` | Pending, running and failed timers |
| `reporting.v_workflow_health_today` | Runs, failures, average and p95 duration per workflow |
| `reporting.trace(correlation_id)` | End-to-end trace of one business transaction |
