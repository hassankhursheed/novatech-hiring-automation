# Configuration

Configuration is split by who changes it and how often:

| Kind | Where | Changed by | Needs restart |
|---|---|---|---|
| Secrets and infrastructure | `.env` (from `.env.example`) | operator | yes |
| n8n integration credentials | n8n *Credentials* (encrypted) | operator | no |
| Business rules | `hiring.settings`, `hiring.scoring_*`, `hiring.job_positions`, `hiring.onboarding_task_templates` | HR admin (SQL now, portal later) | **no** |

## Environment variables (no secrets shown)

| Variable | Used by | Purpose |
|---|---|---|
| `APP_ENV` | backend | `development` / `staging` / `production` (production forces fault injection off and hides API docs) |
| `APP_VERSION`, `LOG_LEVEL` | backend | build tag, log level |
| `COMPANY_TIMEZONE` | n8n, backend | IANA time zone (default `Asia/Karachi`) |
| `BUSINESS_DB_PORT` | compose | host port of the business database (localhost only) |
| `POSTGRES_SUPERUSER_PASSWORD` | db | bootstrap superuser (not used at runtime) |
| `NOVATECH_OWNER_PASSWORD` | migrate, seed | owner role for migrations |
| `NOVATECH_N8N_DB_PASSWORD` | n8n credential | runtime role `n8n_app` |
| `NOVATECH_BACKEND_DB_PASSWORD` | backend | runtime role `backend_app` |
| `SEED_DEMO_DATA` | seed | load the NovaTech demo configuration |
| `DEMO_FAST_TIMERS` | seed | compress reminder/expiry delays to minutes (demo only) |
| `N8N_VERSION` | compose | pinned n8n / runners image version |
| `N8N_DB_PASSWORD` | n8n, n8n-db | n8n's own database |
| `N8N_ENCRYPTION_KEY` | n8n | encrypts stored credentials; **back it up** |
| `N8N_RUNNERS_AUTH_TOKEN` | n8n, n8n-runners | task runner authentication |
| `N8N_HOST`, `N8N_PROTOCOL`, `N8N_WEBHOOK_URL`, `N8N_EDITOR_BASE_URL`, `N8N_SECURE_COOKIE` | n8n | public URLs and cookie security |
| `INTERNAL_API_KEYS` | backend, n8n credential | comma-separated service keys (two keys allow rotation) |
| `CORS_ALLOWED_ORIGINS` | backend | origins allowed to call public endpoints (the portal) |
| `EXPOSE_API_DOCS` | backend | Swagger UI at `/docs` (never in production) |
| `MAX_UPLOAD_MB` | backend | CV size limit |
| `DEFAULT_PHONE_REGION` | backend | region for parsing local phone numbers (`PK`) |
| `FAULT_INJECTION_ENABLED` | backend | honour `X-Fault-Inject` (dev/test only) |
| `LINK_SIGNING_SECRET` | backend | signs the links in emails (slot choice, offer response, scorecard, approval); required in production, at least 32 characters. Rotating it invalidates links already sent |
| `LINK_MAX_TTL_DAYS` | backend | upper bound for any link's validity (default 30); links normally expire at the business deadline |
| `STAFF_DEMO_PASSWORD` | backend | demo/testing only: every active staff account can also sign in to the HR portal with this shared password (5 failures lock an address for 10 minutes). Empty = disabled; refused when `APP_ENV=production` |
| `STAFF_LOGIN_LINK_MINUTES`, `STAFF_SESSION_HOURS` | backend | staff sign-in link lifetime (default 15, single use) and portal session length (default 8) |
| `PORTAL_DEV_MAILBOX_URL` | portal | development hint "emails go to Mailpit" on the sign-in page; set it empty for production builds |
| `PORTAL_PORT`, `PORTAL_API_URL`, `PORTAL_INTAKE_URL`, `PORTAL_CONNECT_SRC` | portal | published port; API and intake URLs baked into the bundle; origins allowed by the portal's Content-Security-Policy |
| `LLM_PROVIDER`, `LLM_MODEL` | backend | `anthropic` / `openai` / `mistral` / `google` / `none`; model id (default `claude-opus-5`) |
| `LLM_TIMEOUT_SECONDS`, `LLM_MAX_TOKENS` | backend | AI call limits |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `MISTRAL_API_KEY` / `GOOGLE_API_KEY` | backend | key for the selected provider only; missing key → AI disabled, candidates go to review |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | backend | optional AI tracing |

Generate strong values with `scripts/new-env.ps1` (Windows) or `scripts/new-env.sh`.

## Business settings (`hiring.settings`)

Typed and validated on write. For example, an invalid interval is rejected with `INVALID_SETTING`. Change a
value with SQL as the owner role, or later from the HR portal:

```sql
UPDATE hiring.settings SET value = '300000', updated_by = 'sana.malik' WHERE key = 'offer.second_approval_threshold';
```

| Key | Default | Meaning |
|---|---|---|
| `company.name` / `company.timezone` / `company.currency` | NovaTech Solutions / Asia/Karachi / PKR | company identity |
| `company.email_domain`, `company.employee_code_prefix` | novatech.example / NT | simulated account creation, employee IDs |
| `company.portal_url` | http://localhost:5173 | base URL of the candidate / staff portal used in email links |
| `company.careers_email` | careers@novatech.example | contact address printed in candidate emails and the offer letter |
| `ops.alert_email` | ops-alerts@novatech.example | recipient of the error digest (WF-07) and fallback for reports |
| `screening.auto_reject_enabled` | true | if false, low scores go to review instead of automatic rejection |
| `screening.ai_enabled` | true | if false, screening runs on rules only |
| `interview.invite_reminder_after` / `invite_expires_after` | 2 days / 4 days | unconfirmed invitation handling |
| `interview.feedback_reminder_after` / `feedback_escalate_after` | 1 day / 2 days | missing interviewer feedback |
| `interview.slot_min_notice` | 2 hours | earliest slot offered or bookable, relative to now |
| `evaluation.application_weight` / `interview_weight` | 0.30 / 0.70 | final score formula |
| `evaluation.select_min_score` / `review_min_score` | 75 / 60 | interview decision thresholds |
| `offer.second_approval_threshold` | 250000 | monthly salary above which L2 approval is required |
| `offer.validity`, `offer.first_reminder_after`, `offer.final_reminder_after` | 5 d, 2 d, 4 d | offer timers |
| `offer.default_probation_months` | 3 | offer default |
| `offer.joining_lead_days` | 14 | default minimum days between the offer and the joining date |
| `onboarding.overdue_reminder_every` | 1 day | reminder spacing for overdue tasks |
| `privacy.rejected_retention` | 180 days | anonymise rejected candidates' personal data after this |

## Scoring rules

Per position: thresholds in `hiring.scoring_configs`, rules in `hiring.scoring_rules`. Any change increments the
position's scoring version automatically, and every stored score records the version it used. Example (scenario 6):

```sql
-- Docker experience becomes more important for Python Developers; takes effect on the next application.
UPDATE hiring.scoring_rules SET points = 15
 WHERE rule_key = 'docker'
   AND job_position_id = (SELECT id FROM hiring.job_positions WHERE code = 'PY_DEV');
```

Rule types: `SKILL_ANY` (any listed skill, declared or found in the CV), `SKILL_ALL`, `MIN_EXPERIENCE_YEARS`,
`KEYWORD_ANY` (terms in CV / cover letter / current role). Free-text skills are mapped to canonical names through
`hiring.skill_aliases` (e.g. `postgres` → `postgresql`). The score is points awarded ÷ points possible × 100.
It routes to `SHORTLIST` / `REVIEW` / `REJECT` by the position's thresholds, and then the screening policy
applies the advisory AI input.
