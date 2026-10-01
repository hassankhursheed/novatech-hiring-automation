-- migrate:up

-- =============================================================================================
-- scheduled_actions: durable timers AND transactional outbox in one table.
--   * Timers: reminders/expiries are rows with a future run_at (not multi-day paused n8n executions).
--   * Outbox: status changes enqueue their follow-up action (run_at = now()) in the same DB transaction,
--     so a crash between "commit" and "tell n8n" can never lose work.
-- The n8n dispatcher (WF-00) claims due rows with FOR UPDATE SKIP LOCKED and routes them by action_type.
-- Cancelling a reminder = marking its row CANCELLED, visible and auditable.
-- =============================================================================================
CREATE TABLE ops.scheduled_actions (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  action_type     text NOT NULL CHECK (action_type ~ '^[A-Z][A-Z0-9_]*$'),
  entity_type     text NOT NULL,
  entity_id       uuid NOT NULL,
  application_id  uuid REFERENCES hiring.applications(id),
  correlation_id  text,
  run_at          timestamptz NOT NULL,
  status          text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','RUNNING','DONE','CANCELLED','FAILED')),
  dedupe_key      text NOT NULL,
  payload         jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(payload) = 'object'),
  attempts        smallint NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts    smallint NOT NULL DEFAULT 5 CHECK (max_attempts BETWEEN 1 AND 20),
  locked_until    timestamptz,
  locked_by       text,
  last_error      text,
  result          jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  completed_at    timestamptz,
  cancelled_at    timestamptz,
  cancel_reason   text,
  -- Scheduling the same thing twice is a no-op.
  CONSTRAINT scheduled_actions_dedupe_uq UNIQUE (dedupe_key),
  CHECK (status <> 'RUNNING' OR locked_until IS NOT NULL),
  CHECK (status <> 'CANCELLED' OR cancelled_at IS NOT NULL)
);
CREATE INDEX scheduled_actions_due_idx ON ops.scheduled_actions (run_at) WHERE status = 'PENDING';
CREATE INDEX scheduled_actions_running_idx ON ops.scheduled_actions (locked_until) WHERE status = 'RUNNING';
CREATE INDEX scheduled_actions_application_idx ON ops.scheduled_actions (application_id) WHERE status IN ('PENDING','RUNNING');
CREATE INDEX scheduled_actions_entity_idx ON ops.scheduled_actions (entity_type, entity_id);
CREATE TRIGGER scheduled_actions_touch BEFORE UPDATE ON ops.scheduled_actions
  FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();

-- =============================================================================================
-- notifications: every outbound message is claimed here first with a dedupe key.
-- A replayed workflow finds the SENT row and skips, so candidates never get duplicate emails.
-- =============================================================================================
CREATE TABLE ops.notifications (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dedupe_key           text NOT NULL,
  channel              text NOT NULL CHECK (channel IN ('EMAIL','TELEGRAM','WHATSAPP','SLACK')),
  template_key         text NOT NULL CHECK (template_key ~ '^[a-z][a-z0-9_.]*$'),
  recipient            text NOT NULL,
  application_id       uuid REFERENCES hiring.applications(id),
  entity_type          text,
  entity_id            text,
  correlation_id       text,
  status               text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','SENT','FAILED','SKIPPED')),
  attempts             smallint NOT NULL DEFAULT 1 CHECK (attempts >= 1),
  provider_message_id  text,
  last_error           text,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  sent_at              timestamptz,
  CONSTRAINT notifications_dedupe_uq UNIQUE (dedupe_key),
  CHECK ((status = 'SENT') = (sent_at IS NOT NULL))
);
CREATE INDEX notifications_application_idx ON ops.notifications (application_id);
CREATE INDEX notifications_status_idx ON ops.notifications (status, created_at);
CREATE TRIGGER notifications_touch BEFORE UPDATE ON ops.notifications
  FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();

-- migrate:down
DROP TABLE IF EXISTS ops.notifications;
DROP TABLE IF EXISTS ops.scheduled_actions;
