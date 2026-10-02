# n8n setup (local)

n8n **2.40.7** runs in Docker with its own PostgreSQL database and an external task runner (the production
setup recommended for n8n 2.x). Everything starts with `docker compose up -d`.

## 1. First login (owner account)

1. Open **http://localhost:5678**.
2. n8n asks you to create the **owner account** (email, name, password). This account lives only in your local
   n8n database. Use a strong password and store it in your password manager.
3. Skip the personalisation survey (disabled via `N8N_PERSONALIZATION_ENABLED=false`).
4. Optional: *Settings → Users* to invite teammates later.

> Back up `N8N_ENCRYPTION_KEY` from `.env`. It encrypts every credential you save in n8n. Without it,
> a restored database cannot decrypt them.

## 2. Credentials to create

Create these in *Credentials → Add credential*. Names matter: the exported workflows reference credentials by name.

| Credential name | Type | Values |
|---|---|---|
| `NovaTech DB (n8n_app)` | Postgres | Host `db` · Port `5432` · Database `novatech` · User `n8n_app` · Password = `NOVATECH_N8N_DB_PASSWORD` from `.env` · SSL `disable` (local only) |
| `NovaTech Backend API key` | Header Auth | Name `X-API-Key` · Value = the first key in `INTERNAL_API_KEYS` from `.env` |
| `Mailpit SMTP (dev)` | SMTP | Host `mailpit` · Port `1025` · User/password: anything · SSL/TLS off |
| `NovaTech Telegram bot` (optional) | Telegram API | Bot token from @BotFather: HR/ops alerts only |

The same `NovaTech Backend API key` credential also protects the operations webhooks n8n exposes to the backend
(`/webhook/ops/kick`, `/webhook/ops/replay`, `/webhook/ops/notify`, `/webhook/ops/daily-report`, `/webhook/ops/onboarding-sweep`): callers must send the same `X-API-Key`.

**Calendar.** Interview and orientation emails carry an "Add to calendar" link and WF-04 records a stable event id
(`api.record_calendar_event`), so no calendar account is needed. To create events in Google Calendar or Outlook
instead, replace the *Record Calendar Event* node in WF-04 with the provider node and pass its event id to
`api.record_calendar_event`.

Notes:
* The `n8n_app` database role can **read** tables and **execute `api.*` functions only**. A workflow that tries
  `UPDATE`/`INSERT` directly gets `permission denied`. That is intentional: all writes go through the audited,
  idempotent functions.
* From inside Docker the backend is **http://backend:8000**, not `localhost`.
* All mail goes to Mailpit (http://localhost:8025) in development. For production, replace the SMTP credential
  with the company's transactional provider. Only SWF-02 uses it.

## 3. Test the connections

1. New workflow → Manual Trigger → **Postgres** node (`NovaTech DB (n8n_app)`), *Execute Query*:
   `SELECT * FROM reporting.v_ops_overview;`. It should return one row.
2. Add an **HTTP Request** node: `GET http://backend:8000/health/ready`. It should return `"database": true`.
3. Add an **HTTP Request** node: `POST http://backend:8000/v1/intake/validate`.
   * Authentication: Generic → Header Auth → `NovaTech Backend API key`.
   * Body JSON: `{"full_name":"Test User","email":"test@example.com","position":"PY_DEV"}`.
   * It should return `"outcome": "NEEDS_REVIEW"` with a list of issues.

## 4. Workflow conventions

Follow [workflows.md → Conventions](workflows.md#conventions-every-workflow-follows). In short:
* The first node builds `ctx`: `workflow_name`, `workflow_version`, `execution_id = {{$execution.id}}`, `correlation_id`.
* Postgres nodes use **query parameters** (`$1`, `$2` and the *Query Parameters* option). Never string-concatenate
  candidate data into SQL.
* Call the backend only through SWF-01 (retries, headers, fault passthrough).
* Send messages only through SWF-02 (dedupe key = no duplicate emails on replay).
* *Workflow settings → Error workflow* = **WF-07 Error & Recovery**.
* *Workflow settings → Timezone* = `Asia/Karachi` (already the instance default via `GENERIC_TIMEZONE`).
* In n8n 2.x, saving a workflow does not activate it. Use **Publish** to activate triggers.

## 5. Deploying workflows and version control

Workflows are versioned in two forms. Credentials are never exported.

| Path | What | Source of truth for |
|---|---|---|
| `n8n/src/*.mjs` | Workflow-as-code: WF-00 and WF-04 to WF-08, built with small helpers (`n8n/src/lib.mjs`) | the code-defined workflows |
| `n8n/workflows/*.json` | Exports of **every** workflow from the running n8n (one file each) | WF-01 to WF-03 and SWF-01 to SWF-03 (edited in the UI), and what is deployed |

```bash
sh scripts/n8n-deploy.sh                # build n8n/src -> import -> publish -> restart n8n
sh scripts/n8n-deploy.sh WF-04          # only one workflow
sh scripts/n8n-deploy.sh --exported     # fresh install / restore: deploy every file in n8n/workflows
sh scripts/n8n-export.sh                # after any change: export, then commit
```

On Windows, `scripts\n8n-deploy.ps1` and `scripts\n8n-export.ps1` take the same arguments. The deploy imports into the
owner's personal project (create the owner account and the credentials first), publishes WF-07 before the others
(it is their error workflow), and restarts n8n so schedules and webhooks are registered.

Workflow ids are fixed (for example `ntWf04Interview1`), so a redeploy updates a workflow in place and calls between
workflows keep working. Node ids are derived from the workflow id and node name. `node n8n/src/build.mjs` rejects
an expression that contains a stray `}}`, which would otherwise end the expression early.

The export script fails if it finds something that looks like a secret inside a workflow file.

## 6. Hardening already applied (docker-compose.yml)

| Setting | Why |
|---|---|
| Separate Postgres for n8n (`n8n-db`) | n8n's internal state never mixes with business data |
| `N8N_RUNNERS_MODE=external` + `n8nio/runners` | Code nodes run isolated from the main process (n8n 2.x recommendation) |
| `N8N_RUNNERS_TASK_TIMEOUT=60` | Code nodes must stay small; heavy logic belongs in the backend |
| `N8N_BLOCK_ENV_ACCESS_IN_NODE=true` | Workflows cannot read server environment variables (secrets) |
| `N8N_UNVERIFIED_PACKAGES_ENABLED=false` | No unverified community nodes |
| `EXECUTIONS_DATA_PRUNE=true`, 14 days | Execution history kept for evidence, then pruned |
| Ports bound to `127.0.0.1` | Nothing is reachable from your network by default |
| Pinned image `2.40.7` | Upgrades are deliberate (`N8N_VERSION` in `.env`), not surprises |

For production, also set `N8N_SECURE_COOKIE=true` behind HTTPS and restrict the editor to VPN or SSO.
Expose only `/webhook/*` publicly, and use a reverse proxy with rate limiting on the intake webhook.
