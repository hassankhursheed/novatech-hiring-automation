-- migrate:up

-- =============================================================================================
-- ProcessedEvents / IdempotencyKeys: the durable inbox.
-- Every inbound event is stored (raw payload included) BEFORE any processing, so nothing is silently
-- dropped and a replayed event (same source + idempotency key) is recognised instead of re-processed.
-- =============================================================================================
CREATE TABLE ops.processed_events (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source             text NOT NULL CHECK (source ~ '^[A-Z][A-Z0-9_]*$'),
  idempotency_key    text NOT NULL CHECK (char_length(idempotency_key) BETWEEN 8 AND 200),
  event_type         text NOT NULL CHECK (event_type ~ '^[A-Z][A-Z0-9_]*$'),
  correlation_id     text NOT NULL UNIQUE,
  payload            jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
  payload_hash       text NOT NULL,
  status             text NOT NULL DEFAULT 'RECEIVED' CHECK (status IN ('RECEIVED','COMPLETED','FAILED')),
  outcome            text CHECK (outcome IN ('ACCEPTED','NEEDS_REVIEW','INVALID','DUPLICATE')),
  outcome_reason     text,
  result             jsonb NOT NULL DEFAULT '{}'::jsonb,
  receive_count      integer NOT NULL DEFAULT 1 CHECK (receive_count >= 1),
  first_received_at  timestamptz NOT NULL DEFAULT now(),
  last_received_at   timestamptz NOT NULL DEFAULT now(),
  completed_at       timestamptz,
  CONSTRAINT processed_events_idempotency_uq UNIQUE (source, idempotency_key),
  CHECK ((status = 'COMPLETED') = (outcome IS NOT NULL AND completed_at IS NOT NULL))
);
CREATE INDEX processed_events_received_idx ON ops.processed_events (first_received_at);
CREATE INDEX processed_events_open_idx ON ops.processed_events (status) WHERE status <> 'COMPLETED';

-- =============================================================================================
-- AutomationErrors: dead-letter / manual-intervention queue.
-- Holds the original payload and the workflow that can safely replay it.
-- =============================================================================================
CREATE TABLE ops.automation_errors (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  fingerprint       text NOT NULL,
  correlation_id    text,
  workflow_name     text NOT NULL,
  workflow_version  text,
  execution_id      text,
  node_name         text,
  entity_type       text,
  entity_id         text,
  error_class       text NOT NULL CHECK (error_class IN ('RETRYABLE','NON_RETRYABLE','UNKNOWN')),
  error_code        text NOT NULL,
  error_message     text NOT NULL,
  http_status       smallint CHECK (http_status BETWEEN 100 AND 599),
  retry_count       integer NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
  occurrence_count  integer NOT NULL DEFAULT 1 CHECK (occurrence_count >= 1),
  payload           jsonb NOT NULL DEFAULT '{}'::jsonb,
  replay_workflow   text,
  status            text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','REPLAYING','RESOLVED','IGNORED')),
  replay_count      integer NOT NULL DEFAULT 0 CHECK (replay_count >= 0),
  last_replayed_at  timestamptz,
  last_replayed_by  text,
  resolution_note   text,
  resolved_at       timestamptz,
  resolved_by       text,
  alerted_at        timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CHECK (status NOT IN ('RESOLVED','IGNORED') OR resolved_at IS NOT NULL)
);
-- The same failure reported twice (explicit handler + Error Trigger) collapses into one open item.
CREATE UNIQUE INDEX automation_errors_open_fingerprint_uq
  ON ops.automation_errors (fingerprint) WHERE status IN ('OPEN','REPLAYING');
CREATE INDEX automation_errors_status_idx ON ops.automation_errors (status, created_at);
CREATE INDEX automation_errors_correlation_idx ON ops.automation_errors (correlation_id);
CREATE TRIGGER automation_errors_touch BEFORE UPDATE ON ops.automation_errors
  FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();

-- =============================================================================================
-- WorkflowExecutions: one row per n8n workflow run (start/finish/duration/outcome).
-- =============================================================================================
CREATE TABLE ops.workflow_executions (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  execution_id      text NOT NULL,
  workflow_name     text NOT NULL,
  workflow_version  text NOT NULL,
  trigger_type      text NOT NULL CHECK (trigger_type IN ('WEBHOOK','SCHEDULE','SUB_WORKFLOW','REPLAY','MANUAL','ERROR')),
  correlation_id    text,
  entity_type       text,
  entity_id         text,
  status            text NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING','SUCCEEDED','FAILED','SKIPPED')),
  started_at        timestamptz NOT NULL DEFAULT now(),
  finished_at       timestamptz,
  duration_ms       integer GENERATED ALWAYS AS (
                      CASE WHEN finished_at IS NULL THEN NULL
                           ELSE (extract(epoch FROM (finished_at - started_at)) * 1000)::integer END) STORED,
  error_id          uuid REFERENCES ops.automation_errors(id),
  CONSTRAINT workflow_executions_run_uq UNIQUE (workflow_name, execution_id),
  CHECK ((status = 'RUNNING') = (finished_at IS NULL))
);
CREATE INDEX workflow_executions_started_idx ON ops.workflow_executions (started_at);
CREATE INDEX workflow_executions_correlation_idx ON ops.workflow_executions (correlation_id);

-- =============================================================================================
-- AutomationLogs: append-only audit trail of every important business action.
-- =============================================================================================
CREATE TABLE ops.automation_logs (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  occurred_at       timestamptz NOT NULL DEFAULT clock_timestamp(),
  correlation_id    text,
  workflow_name     text,
  workflow_version  text,
  execution_id      text,
  entity_type       text NOT NULL CHECK (entity_type IN (
                      'CANDIDATE','APPLICATION','INTERVIEW','OFFER','EMPLOYEE','ONBOARDING_TASK',
                      'EVENT','NOTIFICATION','SCHEDULED_ACTION','ERROR','REPORT','CONFIG','SYSTEM')),
  entity_id         text,
  action            text NOT NULL CHECK (action ~ '^[A-Z][A-Z0-9_]*$'),
  outcome           text NOT NULL CHECK (outcome IN ('SUCCESS','FAILURE','SKIPPED')),
  from_status       text,
  to_status         text,
  actor_type        text NOT NULL CHECK (actor_type IN ('SYSTEM','STAFF','CANDIDATE')),
  actor_id          text NOT NULL,
  retry_count       integer NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
  error_code        text,
  error_message     text,
  details           jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX automation_logs_correlation_idx ON ops.automation_logs (correlation_id);
CREATE INDEX automation_logs_entity_idx ON ops.automation_logs (entity_type, entity_id);
CREATE INDEX automation_logs_occurred_idx ON ops.automation_logs (occurred_at);
CREATE INDEX automation_logs_action_idx ON ops.automation_logs (action, occurred_at);
CREATE TRIGGER automation_logs_append_only BEFORE UPDATE OR DELETE ON ops.automation_logs
  FOR EACH ROW EXECUTE FUNCTION ops.forbid_mutation();

-- =============================================================================================
-- Daily management reports (one per business date; the primary key makes sending idempotent).
-- Metrics are a snapshot of reporting.daily_metrics(); the summary is optional AI prose.
-- =============================================================================================
CREATE TABLE ops.daily_reports (
  report_date     date PRIMARY KEY,
  metrics         jsonb NOT NULL CHECK (jsonb_typeof(metrics) = 'object'),
  summary         text,
  summary_source  text CHECK (summary_source IN ('AI','TEMPLATE')),
  correlation_id  text,
  generated_at    timestamptz NOT NULL DEFAULT now(),
  delivered_at    timestamptz
);

-- migrate:down
DROP TABLE IF EXISTS ops.daily_reports;
DROP TABLE IF EXISTS ops.automation_logs;
DROP TABLE IF EXISTS ops.workflow_executions;
DROP TABLE IF EXISTS ops.automation_errors;
DROP TABLE IF EXISTS ops.processed_events;
