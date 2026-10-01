-- migrate:up

-- Runtime roles must exist first (created by infra/postgres/init or db/bootstrap/roles.sql).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'n8n_app')
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backend_app') THEN
    RAISE EXCEPTION 'Roles n8n_app and backend_app must exist before migrating. See db/bootstrap/roles.sql.';
  END IF;
END $$;

-- btree_gist lets an exclusion constraint prevent overlapping interview slots per interviewer.
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Schema layout:
--   hiring    : business domain tables (source of truth). Runtime roles may read, never write directly.
--   ops       : automation infrastructure (idempotency, executions, logs, errors, scheduling, notifications).
--   api       : SECURITY DEFINER functions - the ONLY write path for n8n and the backend.
--   reporting : read-only views/functions for the operations dashboard and the daily report.
CREATE SCHEMA hiring;
CREATE SCHEMA ops;
CREATE SCHEMA api;
CREATE SCHEMA reporting;

COMMENT ON SCHEMA hiring    IS 'Recruitment and onboarding domain tables (source of truth).';
COMMENT ON SCHEMA ops       IS 'Automation infrastructure: idempotency, executions, audit log, error queue, scheduling.';
COMMENT ON SCHEMA api       IS 'Write API. Every state change goes through a function in this schema.';
COMMENT ON SCHEMA reporting IS 'Read-only operational and management reporting.';

REVOKE ALL ON SCHEMA hiring, ops, api, reporting FROM PUBLIC;

-- Functions are EXECUTE-able by PUBLIC by default; grant explicitly instead (see the privileges migration).
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- ---------------------------------------------------------------------------------------------
-- Error convention
--   SQLSTATE class "NT" (implementation-defined) marks business errors. Message = '<CODE>: <text>'.
--   NT400 invalid input | NT403 forbidden | NT404 not found | NT409 conflict | NT410 expired | NT422 rule violation
--   Callers treat NTxxx as NON-retryable; connection/deadlock/serialization errors are retryable.
-- ---------------------------------------------------------------------------------------------
CREATE FUNCTION ops.fail(p_sqlstate text, p_code text, p_message text, p_detail text DEFAULT NULL)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
  IF p_detail IS NULL THEN
    RAISE EXCEPTION USING ERRCODE = p_sqlstate, MESSAGE = p_code || ': ' || p_message;
  END IF;
  RAISE EXCEPTION USING ERRCODE = p_sqlstate, MESSAGE = p_code || ': ' || p_message, DETAIL = p_detail;
END;
$$;

CREATE FUNCTION ops.touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

-- Append-only guard for audit tables (history, logs). Evidence must not be editable.
CREATE FUNCTION ops.forbid_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION USING ERRCODE = 'NT403',
    MESSAGE = 'APPEND_ONLY: ' || TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME || ' is append-only';
END;
$$;

-- ---------------------------------------------------------------------------------------------
-- Human-readable business codes (CAN-2026-0001, NT-2026-001, COR-20260928-0001).
-- A counter row per scope; the row lock serialises concurrent callers and a rollback releases the number,
-- so codes are gap-free under normal operation. Internal primary keys stay UUIDs.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE ops.code_counters (
  scope       text PRIMARY KEY,
  last_value  bigint NOT NULL CHECK (last_value > 0),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION ops.next_code(p_prefix text, p_period text, p_width integer)
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
  v_scope text := p_prefix || '-' || p_period;
  v_next  bigint;
BEGIN
  INSERT INTO ops.code_counters AS c (scope, last_value)
  VALUES (v_scope, 1)
  ON CONFLICT (scope) DO UPDATE SET last_value = c.last_value + 1, updated_at = now()
  RETURNING c.last_value INTO v_next;

  -- lpad() truncates longer strings, so only pad when needed.
  RETURN v_scope || '-' || CASE WHEN length(v_next::text) >= p_width THEN v_next::text
                                ELSE lpad(v_next::text, p_width, '0') END;
END;
$$;

-- migrate:down
DROP SCHEMA IF EXISTS reporting CASCADE;
DROP SCHEMA IF EXISTS api CASCADE;
DROP SCHEMA IF EXISTS ops CASCADE;
DROP SCHEMA IF EXISTS hiring CASCADE;
