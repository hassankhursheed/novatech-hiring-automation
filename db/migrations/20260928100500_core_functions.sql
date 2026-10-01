-- migrate:up

-- =============================================================================================
-- Execution context
-- Every api.* write takes p_ctx jsonb:
--   { "actor_type": "SYSTEM|STAFF|CANDIDATE", "actor_id": "...", "correlation_id": "...",
--     "workflow_name": "WF-03", "workflow_version": "1.0.0", "execution_id": "123", "retry_count": 0 }
-- It is validated once and copied into history, audit log and errors so any action can be traced.
-- =============================================================================================
CREATE FUNCTION ops.require_ctx(p_ctx jsonb)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
  v_type text;
  v_id   text;
BEGIN
  IF p_ctx IS NULL OR jsonb_typeof(p_ctx) <> 'object' THEN
    PERFORM ops.fail('NT400', 'INVALID_CONTEXT', 'p_ctx must be a JSON object with actor_type and actor_id');
  END IF;

  v_type := upper(btrim(coalesce(p_ctx->>'actor_type', '')));
  v_id   := btrim(coalesce(p_ctx->>'actor_id', ''));

  IF v_type = 'AI' THEN
    PERFORM ops.fail('NT403', 'AI_ACTOR_FORBIDDEN',
      'AI output is advisory only; AI can never be the actor of a business action');
  END IF;
  IF v_type NOT IN ('SYSTEM', 'STAFF', 'CANDIDATE') THEN
    PERFORM ops.fail('NT400', 'INVALID_CONTEXT', 'actor_type must be SYSTEM, STAFF or CANDIDATE');
  END IF;
  IF v_id = '' THEN
    PERFORM ops.fail('NT400', 'INVALID_CONTEXT', 'actor_id is required');
  END IF;
  IF v_type = 'STAFF' AND NOT EXISTS (
       SELECT 1 FROM hiring.staff_members s
        WHERE v_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
          AND s.id = v_id::uuid AND s.is_active) THEN
    PERFORM ops.fail('NT403', 'UNKNOWN_STAFF_ACTOR', 'actor_id must be the id of an active staff member');
  END IF;

  RETURN p_ctx || jsonb_build_object('actor_type', v_type, 'actor_id', v_id);
END;
$$;

CREATE FUNCTION ops.ctx_retry_count(p_ctx jsonb)
RETURNS integer
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT CASE WHEN coalesce(p_ctx->>'retry_count', '') ~ '^[0-9]{1,6}$' THEN (p_ctx->>'retry_count')::integer ELSE 0 END
$$;

-- =============================================================================================
-- Audit log writer
-- =============================================================================================
CREATE FUNCTION ops.write_log(
  p_ctx           jsonb,
  p_entity_type   text,
  p_entity_id     text,
  p_action        text,
  p_outcome       text DEFAULT 'SUCCESS',
  p_details       jsonb DEFAULT '{}'::jsonb,
  p_from_status   text DEFAULT NULL,
  p_to_status     text DEFAULT NULL,
  p_error_code    text DEFAULT NULL,
  p_error_message text DEFAULT NULL)
RETURNS bigint
LANGUAGE sql
AS $$
  INSERT INTO ops.automation_logs (
    correlation_id, workflow_name, workflow_version, execution_id, entity_type, entity_id, action, outcome,
    from_status, to_status, actor_type, actor_id, retry_count, error_code, error_message, details)
  VALUES (
    nullif(p_ctx->>'correlation_id', ''), nullif(p_ctx->>'workflow_name', ''), nullif(p_ctx->>'workflow_version', ''),
    nullif(p_ctx->>'execution_id', ''), p_entity_type, p_entity_id, p_action, p_outcome,
    p_from_status, p_to_status,
    coalesce(nullif(upper(p_ctx->>'actor_type'), ''), 'SYSTEM'), coalesce(nullif(p_ctx->>'actor_id', ''), 'system'),
    ops.ctx_retry_count(p_ctx), p_error_code, left(p_error_message, 4000), coalesce(p_details, '{}'::jsonb))
  RETURNING id
$$;

-- =============================================================================================
-- Outbox / timer enqueue (idempotent by dedupe_key)
-- =============================================================================================
CREATE FUNCTION ops.enqueue_action(
  p_action_type    text,
  p_entity_type    text,
  p_entity_id      uuid,
  p_application_id uuid,
  p_run_at         timestamptz,
  p_dedupe_key     text,
  p_payload        jsonb,
  p_correlation_id text,
  p_max_attempts   smallint DEFAULT 5)
RETURNS uuid
LANGUAGE plpgsql
AS $$
DECLARE
  v_id uuid;
BEGIN
  INSERT INTO ops.scheduled_actions (action_type, entity_type, entity_id, application_id, correlation_id,
                                     run_at, dedupe_key, payload, max_attempts)
  VALUES (p_action_type, p_entity_type, p_entity_id, p_application_id, p_correlation_id,
          p_run_at, p_dedupe_key, coalesce(p_payload, '{}'::jsonb), p_max_attempts)
  ON CONFLICT (dedupe_key) DO NOTHING
  RETURNING id INTO v_id;

  IF v_id IS NULL THEN
    SELECT sa.id INTO v_id FROM ops.scheduled_actions sa WHERE sa.dedupe_key = p_dedupe_key;
  END IF;
  RETURN v_id;
END;
$$;

-- =============================================================================================
-- State machine
-- =============================================================================================
CREATE FUNCTION hiring.record_status_history(
  p_application_id uuid, p_candidate_id uuid, p_from text, p_to text, p_reason text, p_ctx jsonb, p_metadata jsonb)
RETURNS bigint
LANGUAGE sql
AS $$
  INSERT INTO hiring.candidate_status_history (
    application_id, candidate_id, from_status, to_status, reason, actor_type, actor_id,
    workflow_name, workflow_version, execution_id, correlation_id, metadata)
  VALUES (
    p_application_id, p_candidate_id, p_from, p_to, p_reason, p_ctx->>'actor_type', p_ctx->>'actor_id',
    nullif(p_ctx->>'workflow_name', ''), nullif(p_ctx->>'workflow_version', ''), nullif(p_ctx->>'execution_id', ''),
    p_ctx->>'correlation_id', coalesce(p_metadata, '{}'::jsonb))
  RETURNING id
$$;

-- The single place where an application's status changes.
--   * idempotent: moving to the current status is a no-op (changed = false)
--   * optimistic concurrency: p_expected_from rejects stale callers (e.g. a reminder racing a confirmation)
--   * validates the transition and the actor type against hiring.status_transitions
--   * managed transitions can only be made by their owning api function
--   * writes history + audit log, enqueues the on-enter action, cancels timers when the application closes
CREATE FUNCTION hiring.transition_application(
  p_application_id uuid,
  p_to_status      text,
  p_reason         text,
  p_ctx            jsonb,
  p_expected_from  text DEFAULT NULL,
  p_managed_by     text DEFAULT NULL,
  p_metadata       jsonb DEFAULT '{}'::jsonb)
RETURNS TABLE (application_id uuid, from_status text, to_status text, changed boolean, history_id bigint)
LANGUAGE plpgsql
AS $$
#variable_conflict use_column
DECLARE
  v_ctx        jsonb := ops.require_ctx(p_ctx);
  v_app        hiring.applications%ROWTYPE;
  v_rule       hiring.status_transitions%ROWTYPE;
  v_target     hiring.application_statuses%ROWTYPE;
  v_history_id bigint;
  v_corr       text;
BEGIN
  SELECT * INTO v_app FROM hiring.applications a WHERE a.id = p_application_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'APPLICATION_NOT_FOUND', format('application %s does not exist', p_application_id));
  END IF;

  v_corr := coalesce(nullif(v_ctx->>'correlation_id', ''), v_app.correlation_id);
  v_ctx  := v_ctx || jsonb_build_object('correlation_id', v_corr);

  IF v_app.status = p_to_status THEN
    RETURN QUERY SELECT v_app.id, v_app.status, v_app.status, false, NULL::bigint;
    RETURN;
  END IF;

  IF p_expected_from IS NOT NULL AND v_app.status <> p_expected_from THEN
    PERFORM ops.fail('NT409', 'STALE_STATE',
      format('expected status %s but application %s is %s', p_expected_from, v_app.application_code, v_app.status));
  END IF;

  SELECT * INTO v_rule FROM hiring.status_transitions t
   WHERE t.from_status = v_app.status AND t.to_status = p_to_status;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT409', 'INVALID_TRANSITION',
      format('%s -> %s is not an allowed transition (application %s)', v_app.status, p_to_status, v_app.application_code));
  END IF;

  IF NOT ((v_ctx->>'actor_type') = ANY (v_rule.allowed_actor_types)) THEN
    PERFORM ops.fail('NT403', 'ACTOR_NOT_ALLOWED',
      format('%s may not perform %s -> %s', v_ctx->>'actor_type', v_app.status, p_to_status));
  END IF;

  IF v_rule.managed_by IS NOT NULL AND v_rule.managed_by IS DISTINCT FROM p_managed_by THEN
    PERFORM ops.fail('NT409', 'TRANSITION_REQUIRES_FUNCTION',
      format('%s -> %s must be performed by api.%s', v_app.status, p_to_status, v_rule.managed_by));
  END IF;

  SELECT * INTO v_target FROM hiring.application_statuses s WHERE s.code = p_to_status;

  PERFORM set_config('hiring.allow_status_write', 'on', true);
  UPDATE hiring.applications a
     SET status            = p_to_status,
         status_changed_at = now(),
         review_reason     = CASE WHEN v_target.awaits_human THEN p_reason END,
         closed_at         = CASE WHEN v_target.is_terminal THEN now() END,
         version           = a.version + 1
   WHERE a.id = v_app.id;
  PERFORM set_config('hiring.allow_status_write', 'off', true);

  v_history_id := hiring.record_status_history(
    v_app.id, v_app.candidate_id, v_app.status, p_to_status, p_reason, v_ctx, p_metadata);

  PERFORM ops.write_log(v_ctx, 'APPLICATION', v_app.id::text, 'STATUS_CHANGED', 'SUCCESS',
    jsonb_build_object('reason', p_reason, 'history_id', v_history_id, 'application_code', v_app.application_code)
      || coalesce(p_metadata, '{}'::jsonb),
    v_app.status, p_to_status);

  IF v_target.on_enter_action IS NOT NULL THEN
    PERFORM ops.enqueue_action(
      v_target.on_enter_action, 'APPLICATION', v_app.id, v_app.id, now(),
      v_target.on_enter_action || ':' || v_app.id::text || ':' || v_history_id::text,
      jsonb_build_object('from_status', v_app.status, 'to_status', p_to_status,
                         'history_id', v_history_id, 'reason', p_reason),
      v_corr);
  END IF;

  IF v_target.is_terminal THEN
    UPDATE ops.scheduled_actions sa
       SET status = 'CANCELLED', cancelled_at = now(), cancel_reason = 'application closed as ' || p_to_status
     WHERE sa.application_id = v_app.id
       AND sa.status = 'PENDING'
       AND sa.action_type IS DISTINCT FROM v_target.on_enter_action;
  END IF;

  RETURN QUERY SELECT v_app.id, v_app.status, p_to_status, true, v_history_id;
END;
$$;

-- Generic transition for human decisions (review queues, closing an application).
-- Managed transitions are refused here and must use their dedicated api function.
CREATE FUNCTION api.transition_application_status(
  p_application_id uuid,
  p_to_status      text,
  p_reason         text,
  p_ctx            jsonb,
  p_expected_from  text DEFAULT NULL)
RETURNS TABLE (application_id uuid, from_status text, to_status text, changed boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
BEGIN
  IF upper(coalesce(p_ctx->>'actor_type', '')) = 'STAFF' AND nullif(btrim(p_reason), '') IS NULL THEN
    PERFORM ops.fail('NT400', 'REASON_REQUIRED', 'a reason is required for human decisions');
  END IF;
  RETURN QUERY
    SELECT t.application_id, t.from_status, t.to_status, t.changed
      FROM hiring.transition_application(p_application_id, upper(btrim(p_to_status)), p_reason, p_ctx,
                                         p_expected_from, NULL, '{}'::jsonb) AS t;
END;
$$;

-- =============================================================================================
-- Audit log (for n8n actions such as EMAIL_SENT, CALENDAR_EVENT_CREATED, RETRY_ATTEMPT, RETRY_RECOVERED)
-- =============================================================================================
CREATE FUNCTION api.log_action(
  p_ctx           jsonb,
  p_entity_type   text,
  p_entity_id     text,
  p_action        text,
  p_outcome       text DEFAULT 'SUCCESS',
  p_details       jsonb DEFAULT '{}'::jsonb,
  p_error_code    text DEFAULT NULL,
  p_error_message text DEFAULT NULL)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  RETURN ops.write_log(ops.require_ctx(p_ctx), upper(p_entity_type), p_entity_id, upper(p_action), upper(p_outcome),
                       p_details, NULL, NULL, p_error_code, p_error_message);
END;
$$;

-- =============================================================================================
-- Idempotent event intake
-- =============================================================================================
-- Registers an inbound event BEFORE processing. Returns is_replay = true when the same
-- (source, idempotency_key) was seen before. Callers must:
--   * is_replay AND status = 'COMPLETED'  -> return the stored result, do nothing else
--   * otherwise                           -> (re)process; downstream functions are idempotent too
-- Re-using a key with a DIFFERENT payload is rejected (IDEMPOTENCY_KEY_REUSED).
CREATE FUNCTION api.register_event(
  p_source          text,
  p_idempotency_key text,
  p_event_type      text,
  p_payload         jsonb,
  p_ctx             jsonb)
RETURNS TABLE (event_id uuid, correlation_id text, is_replay boolean, status text, outcome text,
               result jsonb, receive_count integer)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx    jsonb := ops.require_ctx(p_ctx);
  v_source text  := upper(btrim(coalesce(p_source, '')));
  v_key    text  := btrim(coalesce(p_idempotency_key, ''));
  v_type   text  := upper(btrim(coalesce(p_event_type, '')));
  v_hash   text;
  v_row    ops.processed_events%ROWTYPE;
BEGIN
  IF v_source !~ '^[A-Z][A-Z0-9_]*$' THEN
    PERFORM ops.fail('NT400', 'INVALID_EVENT', 'source must be an UPPER_SNAKE identifier');
  END IF;
  IF char_length(v_key) NOT BETWEEN 8 AND 200 THEN
    PERFORM ops.fail('NT400', 'INVALID_IDEMPOTENCY_KEY', 'idempotency key must be 8-200 characters');
  END IF;
  IF p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object' THEN
    PERFORM ops.fail('NT400', 'INVALID_EVENT', 'payload must be a JSON object');
  END IF;

  v_hash := encode(sha256(convert_to(p_payload::text, 'UTF8')), 'hex');

  SELECT * INTO v_row FROM ops.processed_events e
   WHERE e.source = v_source AND e.idempotency_key = v_key
   FOR UPDATE;

  IF NOT FOUND THEN
    INSERT INTO ops.processed_events (source, idempotency_key, event_type, correlation_id, payload, payload_hash)
    VALUES (v_source, v_key, v_type,
            ops.next_code('COR', to_char(now() AT TIME ZONE hiring.company_timezone(), 'YYYYMMDD'), 4),
            p_payload, v_hash)
    ON CONFLICT ON CONSTRAINT processed_events_idempotency_uq DO NOTHING
    RETURNING * INTO v_row;

    IF FOUND THEN
      PERFORM ops.write_log(v_ctx || jsonb_build_object('correlation_id', v_row.correlation_id),
        'EVENT', v_row.id::text, 'EVENT_RECEIVED', 'SUCCESS',
        jsonb_build_object('source', v_source, 'event_type', v_type));
      RETURN QUERY SELECT v_row.id, v_row.correlation_id, false, v_row.status, v_row.outcome, v_row.result, v_row.receive_count;
      RETURN;
    END IF;

    -- A concurrent request inserted the same key first; treat this one as the replay.
    SELECT * INTO v_row FROM ops.processed_events e
     WHERE e.source = v_source AND e.idempotency_key = v_key
     FOR UPDATE;
  END IF;

  IF v_row.payload_hash <> v_hash THEN
    PERFORM ops.fail('NT409', 'IDEMPOTENCY_KEY_REUSED',
      'this idempotency key was already used with a different payload');
  END IF;

  UPDATE ops.processed_events e
     SET receive_count = e.receive_count + 1, last_received_at = now()
   WHERE e.id = v_row.id
  RETURNING * INTO v_row;

  PERFORM ops.write_log(v_ctx || jsonb_build_object('correlation_id', v_row.correlation_id),
    'EVENT', v_row.id::text, 'EVENT_REPLAY_DETECTED', 'SKIPPED',
    jsonb_build_object('receive_count', v_row.receive_count, 'status', v_row.status, 'outcome', v_row.outcome));

  RETURN QUERY SELECT v_row.id, v_row.correlation_id, true, v_row.status, v_row.outcome, v_row.result, v_row.receive_count;
END;
$$;

-- Marks an event FAILED (infrastructure failure before a business outcome). A later replay re-processes it.
CREATE FUNCTION api.fail_event(p_event_id uuid, p_reason text, p_ctx jsonb)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_ctx    jsonb := ops.require_ctx(p_ctx);
  v_status text;
  v_corr   text;
BEGIN
  UPDATE ops.processed_events e
     SET status = 'FAILED', outcome_reason = left(p_reason, 2000)
   WHERE e.id = p_event_id AND e.status <> 'COMPLETED'
  RETURNING e.status, e.correlation_id INTO v_status, v_corr;

  IF NOT FOUND THEN
    SELECT e.status INTO v_status FROM ops.processed_events e WHERE e.id = p_event_id;
    IF NOT FOUND THEN
      PERFORM ops.fail('NT404', 'EVENT_NOT_FOUND', format('event %s does not exist', p_event_id));
    END IF;
    RETURN v_status;  -- already completed: nothing to fail
  END IF;

  PERFORM ops.write_log(v_ctx || jsonb_build_object('correlation_id', v_corr), 'EVENT', p_event_id::text,
    'EVENT_PROCESSING_FAILED', 'FAILURE', jsonb_build_object('reason', p_reason));
  RETURN v_status;
END;
$$;

-- =============================================================================================
-- Workflow execution tracking
-- =============================================================================================
CREATE FUNCTION api.start_workflow_execution(
  p_ctx          jsonb,
  p_trigger_type text,
  p_entity_type  text DEFAULT NULL,
  p_entity_id    text DEFAULT NULL)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_ctx jsonb := ops.require_ctx(p_ctx);
  v_id  bigint;
BEGIN
  IF nullif(v_ctx->>'workflow_name', '') IS NULL OR nullif(v_ctx->>'execution_id', '') IS NULL
     OR nullif(v_ctx->>'workflow_version', '') IS NULL THEN
    PERFORM ops.fail('NT400', 'INVALID_CONTEXT', 'workflow_name, workflow_version and execution_id are required');
  END IF;

  INSERT INTO ops.workflow_executions (execution_id, workflow_name, workflow_version, trigger_type,
                                       correlation_id, entity_type, entity_id)
  VALUES (v_ctx->>'execution_id', v_ctx->>'workflow_name', v_ctx->>'workflow_version', upper(p_trigger_type),
          nullif(v_ctx->>'correlation_id', ''), upper(p_entity_type), p_entity_id)
  ON CONFLICT ON CONSTRAINT workflow_executions_run_uq DO UPDATE
    SET correlation_id = coalesce(ops.workflow_executions.correlation_id, EXCLUDED.correlation_id),
        entity_type    = coalesce(ops.workflow_executions.entity_type, EXCLUDED.entity_type),
        entity_id      = coalesce(ops.workflow_executions.entity_id, EXCLUDED.entity_id)
  RETURNING id INTO v_id;
  RETURN v_id;
END;
$$;

CREATE FUNCTION api.finish_workflow_execution(p_ctx jsonb, p_status text, p_error_id uuid DEFAULT NULL)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_ctx      jsonb := ops.require_ctx(p_ctx);
  v_status   text  := upper(p_status);
  v_duration integer;
BEGIN
  IF v_status NOT IN ('SUCCEEDED', 'FAILED', 'SKIPPED') THEN
    PERFORM ops.fail('NT400', 'INVALID_STATUS', 'status must be SUCCEEDED, FAILED or SKIPPED');
  END IF;

  UPDATE ops.workflow_executions w
     SET status = v_status, finished_at = now(), error_id = coalesce(p_error_id, w.error_id),
         correlation_id = coalesce(w.correlation_id, nullif(v_ctx->>'correlation_id', ''))
   WHERE w.workflow_name = v_ctx->>'workflow_name' AND w.execution_id = v_ctx->>'execution_id'
     AND w.status = 'RUNNING'
  RETURNING w.duration_ms INTO v_duration;

  IF NOT FOUND THEN
    -- Finish without a recorded start (e.g. the workflow crashed before logging): record it anyway.
    INSERT INTO ops.workflow_executions (execution_id, workflow_name, workflow_version, trigger_type,
                                         correlation_id, status, finished_at, error_id)
    VALUES (v_ctx->>'execution_id', v_ctx->>'workflow_name', coalesce(v_ctx->>'workflow_version', 'unknown'),
            'MANUAL', nullif(v_ctx->>'correlation_id', ''), v_status, now(), p_error_id)
    ON CONFLICT ON CONSTRAINT workflow_executions_run_uq DO NOTHING
    RETURNING duration_ms INTO v_duration;
  END IF;
  RETURN v_duration;
END;
$$;

-- =============================================================================================
-- Error / dead-letter queue
-- =============================================================================================
CREATE FUNCTION ops.record_error(p_error jsonb, p_ctx jsonb)
RETURNS uuid
LANGUAGE plpgsql
AS $$
DECLARE
  v_workflow text := coalesce(nullif(p_error->>'workflow_name', ''), nullif(p_ctx->>'workflow_name', ''), 'UNKNOWN');
  v_corr     text := coalesce(nullif(p_error->>'correlation_id', ''), nullif(p_ctx->>'correlation_id', ''));
  v_code     text := upper(coalesce(nullif(p_error->>'error_code', ''), 'UNKNOWN_ERROR'));
  v_class    text := upper(coalesce(nullif(p_error->>'error_class', ''), 'UNKNOWN'));
  v_message  text := coalesce(nullif(p_error->>'error_message', ''), 'no error message provided');
  v_http     smallint;
  v_id       uuid;
BEGIN
  IF v_class NOT IN ('RETRYABLE', 'NON_RETRYABLE', 'UNKNOWN') THEN
    v_class := 'UNKNOWN';
  END IF;
  IF coalesce(p_error->>'http_status', '') ~ '^[1-5][0-9]{2}$' THEN
    v_http := (p_error->>'http_status')::smallint;
  END IF;

  INSERT INTO ops.automation_errors AS e (
    fingerprint, correlation_id, workflow_name, workflow_version, execution_id, node_name, entity_type, entity_id,
    error_class, error_code, error_message, http_status, retry_count, payload, replay_workflow)
  VALUES (
    md5(concat_ws('|', v_workflow, p_error->>'node_name', v_corr, p_error->>'entity_id', v_code)),
    v_corr, v_workflow,
    coalesce(nullif(p_error->>'workflow_version', ''), nullif(p_ctx->>'workflow_version', '')),
    coalesce(nullif(p_error->>'execution_id', ''), nullif(p_ctx->>'execution_id', '')),
    nullif(p_error->>'node_name', ''), upper(nullif(p_error->>'entity_type', '')), nullif(p_error->>'entity_id', ''),
    v_class, v_code, left(v_message, 4000), v_http,
    greatest(ops.ctx_retry_count(p_error), ops.ctx_retry_count(p_ctx)),
    coalesce(p_error->'payload', '{}'::jsonb), nullif(p_error->>'replay_workflow', ''))
  ON CONFLICT (fingerprint) WHERE status IN ('OPEN', 'REPLAYING') DO UPDATE
    SET occurrence_count = e.occurrence_count + 1,
        status           = 'OPEN',
        error_message    = EXCLUDED.error_message,
        http_status      = coalesce(EXCLUDED.http_status, e.http_status),
        retry_count      = greatest(e.retry_count, EXCLUDED.retry_count),
        execution_id     = coalesce(EXCLUDED.execution_id, e.execution_id),
        payload          = CASE WHEN EXCLUDED.payload = '{}'::jsonb THEN e.payload ELSE EXCLUDED.payload END
  RETURNING e.id INTO v_id;

  UPDATE ops.workflow_executions w SET error_id = v_id
   WHERE w.workflow_name = v_workflow
     AND w.execution_id = coalesce(nullif(p_error->>'execution_id', ''), nullif(p_ctx->>'execution_id', ''));

  PERFORM ops.write_log(p_ctx || jsonb_build_object('correlation_id', v_corr), 'ERROR', v_id::text,
    'ERROR_RECORDED', 'FAILURE',
    jsonb_build_object('workflow', v_workflow, 'node', p_error->>'node_name', 'error_class', v_class),
    NULL, NULL, v_code, v_message);
  RETURN v_id;
END;
$$;

CREATE FUNCTION api.record_error(p_error jsonb, p_ctx jsonb)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  IF p_error IS NULL OR jsonb_typeof(p_error) <> 'object' THEN
    PERFORM ops.fail('NT400', 'INVALID_ERROR', 'p_error must be a JSON object');
  END IF;
  RETURN ops.record_error(p_error, ops.require_ctx(p_ctx));
END;
$$;

-- Moves an error to REPLAYING and returns what is needed to re-run it (original payload + target workflow).
-- The replayed workflow is idempotent, so replaying cannot create duplicate business records.
CREATE FUNCTION api.begin_error_replay(p_error_id uuid, p_ctx jsonb)
RETURNS TABLE (error_id uuid, correlation_id text, replay_workflow text, payload jsonb, replay_count integer)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx jsonb := ops.require_ctx(p_ctx);
  v_err ops.automation_errors%ROWTYPE;
BEGIN
  SELECT * INTO v_err FROM ops.automation_errors e WHERE e.id = p_error_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'ERROR_NOT_FOUND', format('error %s does not exist', p_error_id));
  END IF;
  IF NOT (v_err.status = 'OPEN'
          OR (v_err.status = 'REPLAYING' AND v_err.last_replayed_at < now() - interval '10 minutes')) THEN
    PERFORM ops.fail('NT409', 'ERROR_NOT_REPLAYABLE', format('error is %s', v_err.status));
  END IF;
  IF v_err.replay_workflow IS NULL THEN
    PERFORM ops.fail('NT422', 'NO_REPLAY_TARGET', 'this error has no replay workflow; resolve it manually');
  END IF;

  UPDATE ops.automation_errors e
     SET status = 'REPLAYING', replay_count = e.replay_count + 1,
         last_replayed_at = now(), last_replayed_by = v_ctx->>'actor_id'
   WHERE e.id = v_err.id
  RETURNING * INTO v_err;

  PERFORM ops.write_log(v_ctx || jsonb_build_object('correlation_id', v_err.correlation_id), 'ERROR', v_err.id::text,
    'ERROR_REPLAY_STARTED', 'SUCCESS',
    jsonb_build_object('replay_workflow', v_err.replay_workflow, 'replay_count', v_err.replay_count));

  RETURN QUERY SELECT v_err.id, v_err.correlation_id, v_err.replay_workflow, v_err.payload, v_err.replay_count;
END;
$$;

-- RESOLVED (fixed/replayed OK), IGNORED (won't fix; STAFF only, note required), OPEN (replay failed).
CREATE FUNCTION api.resolve_error(p_error_id uuid, p_status text, p_note text, p_ctx jsonb)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_ctx    jsonb := ops.require_ctx(p_ctx);
  v_status text  := upper(p_status);
  v_corr   text;
BEGIN
  IF v_status NOT IN ('RESOLVED', 'IGNORED', 'OPEN') THEN
    PERFORM ops.fail('NT400', 'INVALID_STATUS', 'status must be RESOLVED, IGNORED or OPEN');
  END IF;
  IF v_status = 'IGNORED' AND (v_ctx->>'actor_type' <> 'STAFF' OR nullif(btrim(p_note), '') IS NULL) THEN
    PERFORM ops.fail('NT403', 'IGNORE_REQUIRES_STAFF', 'only staff can ignore an error, and a note is required');
  END IF;

  UPDATE ops.automation_errors e
     SET status          = v_status,
         resolution_note = coalesce(p_note, e.resolution_note),
         resolved_at     = CASE WHEN v_status = 'OPEN' THEN NULL ELSE now() END,
         resolved_by     = CASE WHEN v_status = 'OPEN' THEN NULL ELSE v_ctx->>'actor_id' END
   WHERE e.id = p_error_id AND e.status IN ('OPEN', 'REPLAYING')
  RETURNING e.correlation_id INTO v_corr;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT409', 'ERROR_NOT_OPEN', 'only OPEN or REPLAYING errors can be resolved');
  END IF;

  PERFORM ops.write_log(v_ctx || jsonb_build_object('correlation_id', v_corr), 'ERROR', p_error_id::text,
    'ERROR_' || v_status, 'SUCCESS', jsonb_build_object('note', p_note));
  RETURN v_status;
END;
$$;

-- =============================================================================================
-- Scheduled actions (timers + outbox)
-- =============================================================================================
CREATE FUNCTION api.schedule_action(
  p_action_type    text,
  p_entity_type    text,
  p_entity_id      uuid,
  p_application_id uuid,
  p_run_at         timestamptz,
  p_dedupe_key     text,
  p_payload        jsonb,
  p_ctx            jsonb)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_ctx jsonb := ops.require_ctx(p_ctx);
  v_id  uuid;
BEGIN
  IF nullif(btrim(p_dedupe_key), '') IS NULL THEN
    PERFORM ops.fail('NT400', 'DEDUPE_KEY_REQUIRED', 'every scheduled action needs a dedupe key');
  END IF;
  v_id := ops.enqueue_action(upper(p_action_type), upper(p_entity_type), p_entity_id, p_application_id,
                             coalesce(p_run_at, now()), p_dedupe_key, p_payload, nullif(v_ctx->>'correlation_id', ''));
  PERFORM ops.write_log(v_ctx, 'SCHEDULED_ACTION', v_id::text, 'ACTION_SCHEDULED', 'SUCCESS',
    jsonb_build_object('action_type', upper(p_action_type), 'run_at', coalesce(p_run_at, now())));
  RETURN v_id;
END;
$$;

CREATE FUNCTION api.cancel_scheduled_actions(
  p_entity_type  text,
  p_entity_id    uuid,
  p_action_types text[],
  p_reason       text,
  p_ctx          jsonb)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_ctx   jsonb := ops.require_ctx(p_ctx);
  v_count integer;
BEGIN
  UPDATE ops.scheduled_actions sa
     SET status = 'CANCELLED', cancelled_at = now(), cancel_reason = coalesce(p_reason, 'cancelled')
   WHERE sa.entity_type = upper(p_entity_type) AND sa.entity_id = p_entity_id
     AND sa.status = 'PENDING'
     AND (p_action_types IS NULL OR sa.action_type = ANY (p_action_types));
  GET DIAGNOSTICS v_count = ROW_COUNT;

  IF v_count > 0 THEN
    PERFORM ops.write_log(v_ctx, upper(p_entity_type), p_entity_id::text, 'SCHEDULED_ACTIONS_CANCELLED', 'SUCCESS',
      jsonb_build_object('count', v_count, 'action_types', p_action_types, 'reason', p_reason));
  END IF;
  RETURN v_count;
END;
$$;

-- Claims due actions for one dispatcher run. SKIP LOCKED lets overlapping runs share the queue safely;
-- the lease lets a crashed run's actions be picked up again after it expires.
CREATE FUNCTION api.claim_scheduled_actions(p_worker text, p_limit integer DEFAULT 25, p_lease_seconds integer DEFAULT 300)
RETURNS SETOF ops.scheduled_actions
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_stale ops.scheduled_actions%ROWTYPE;
BEGIN
  IF p_limit NOT BETWEEN 1 AND 500 OR p_lease_seconds NOT BETWEEN 10 AND 3600 THEN
    PERFORM ops.fail('NT400', 'INVALID_ARGUMENT', 'limit must be 1-500 and lease 10-3600 seconds');
  END IF;

  -- Leases that expired after the last allowed attempt: give up and surface in the error queue.
  FOR v_stale IN
    UPDATE ops.scheduled_actions sa
       SET status = 'FAILED', locked_until = NULL, locked_by = NULL,
           last_error = coalesce(sa.last_error, 'lease expired after final attempt')
     WHERE sa.status = 'RUNNING' AND sa.locked_until < now() AND sa.attempts >= sa.max_attempts
    RETURNING sa.*
  LOOP
    PERFORM ops.record_error(jsonb_build_object(
      'workflow_name', 'WF-00', 'node_name', v_stale.action_type, 'correlation_id', v_stale.correlation_id,
      'entity_type', 'SCHEDULED_ACTION', 'entity_id', v_stale.id::text, 'error_class', 'RETRYABLE',
      'error_code', 'ACTION_ATTEMPTS_EXHAUSTED', 'error_message', v_stale.last_error,
      'retry_count', v_stale.attempts, 'replay_workflow', 'WF-00',
      'payload', jsonb_build_object('scheduled_action_id', v_stale.id, 'action_type', v_stale.action_type)),
      jsonb_build_object('actor_type', 'SYSTEM', 'actor_id', coalesce(p_worker, 'dispatcher'),
                         'workflow_name', 'WF-00', 'correlation_id', v_stale.correlation_id));
  END LOOP;

  RETURN QUERY
  WITH due AS (
    SELECT sa.id
      FROM ops.scheduled_actions sa
     WHERE (sa.status = 'PENDING' AND sa.run_at <= now())
        OR (sa.status = 'RUNNING' AND sa.locked_until < now())
     ORDER BY sa.run_at
     LIMIT p_limit
     FOR UPDATE SKIP LOCKED
  )
  UPDATE ops.scheduled_actions sa
     SET status = 'RUNNING', attempts = sa.attempts + 1,
         locked_until = now() + make_interval(secs => p_lease_seconds), locked_by = p_worker
    FROM due
   WHERE sa.id = due.id
  RETURNING sa.*;
END;
$$;

-- DONE / SKIPPED (precondition no longer true, e.g. the candidate already answered) / FAILED.
-- FAILED retries with exponential backoff until max_attempts, then lands in the error queue.
CREATE FUNCTION api.complete_scheduled_action(
  p_action_id uuid,
  p_outcome   text,
  p_result    jsonb,
  p_error     text,
  p_ctx       jsonb)
RETURNS TABLE (action_id uuid, status text, attempts smallint, next_run_at timestamptz)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx     jsonb := ops.require_ctx(p_ctx);
  v_outcome text  := upper(p_outcome);
  v_row     ops.scheduled_actions%ROWTYPE;
BEGIN
  IF v_outcome NOT IN ('DONE', 'SKIPPED', 'FAILED') THEN
    PERFORM ops.fail('NT400', 'INVALID_OUTCOME', 'outcome must be DONE, SKIPPED or FAILED');
  END IF;

  SELECT * INTO v_row FROM ops.scheduled_actions sa WHERE sa.id = p_action_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'ACTION_NOT_FOUND', format('scheduled action %s does not exist', p_action_id));
  END IF;
  v_ctx := v_ctx || jsonb_build_object('correlation_id', coalesce(nullif(v_ctx->>'correlation_id', ''), v_row.correlation_id));

  IF v_row.status <> 'RUNNING' THEN
    -- Already finished or cancelled meanwhile: idempotent, report the current state.
    RETURN QUERY SELECT v_row.id, v_row.status, v_row.attempts, NULL::timestamptz;
    RETURN;
  END IF;

  IF v_outcome IN ('DONE', 'SKIPPED') THEN
    UPDATE ops.scheduled_actions sa
       SET status = 'DONE', completed_at = now(), locked_until = NULL, locked_by = NULL,
           result = coalesce(p_result, '{}'::jsonb) || jsonb_build_object('outcome', v_outcome)
     WHERE sa.id = v_row.id
    RETURNING * INTO v_row;
    PERFORM ops.write_log(v_ctx, 'SCHEDULED_ACTION', v_row.id::text, 'ACTION_' || v_outcome, 'SUCCESS',
      jsonb_build_object('action_type', v_row.action_type, 'attempts', v_row.attempts) || coalesce(p_result, '{}'::jsonb));
    RETURN QUERY SELECT v_row.id, v_row.status, v_row.attempts, NULL::timestamptz;
    RETURN;
  END IF;

  IF v_row.attempts < v_row.max_attempts THEN
    UPDATE ops.scheduled_actions sa
       SET status = 'PENDING', locked_until = NULL, locked_by = NULL, last_error = left(p_error, 4000),
           run_at = now() + least(make_interval(secs => 30 * power(2, v_row.attempts - 1)), interval '1 hour')
     WHERE sa.id = v_row.id
    RETURNING * INTO v_row;
    PERFORM ops.write_log(v_ctx, 'SCHEDULED_ACTION', v_row.id::text, 'ACTION_RETRY_SCHEDULED', 'FAILURE',
      jsonb_build_object('action_type', v_row.action_type, 'attempts', v_row.attempts, 'next_run_at', v_row.run_at),
      NULL, NULL, 'ACTION_FAILED', p_error);
    RETURN QUERY SELECT v_row.id, v_row.status, v_row.attempts, v_row.run_at;
    RETURN;
  END IF;

  UPDATE ops.scheduled_actions sa
     SET status = 'FAILED', locked_until = NULL, locked_by = NULL, last_error = left(p_error, 4000)
   WHERE sa.id = v_row.id
  RETURNING * INTO v_row;
  PERFORM ops.record_error(jsonb_build_object(
    'workflow_name', coalesce(v_ctx->>'workflow_name', 'WF-00'), 'node_name', v_row.action_type,
    'entity_type', 'SCHEDULED_ACTION', 'entity_id', v_row.id::text, 'error_class', 'RETRYABLE',
    'error_code', 'ACTION_ATTEMPTS_EXHAUSTED', 'error_message', coalesce(p_error, 'action failed'),
    'retry_count', v_row.attempts, 'replay_workflow', 'WF-00',
    'payload', jsonb_build_object('scheduled_action_id', v_row.id, 'action_type', v_row.action_type)), v_ctx);
  RETURN QUERY SELECT v_row.id, v_row.status, v_row.attempts, NULL::timestamptz;
END;
$$;

-- Operator replay for a FAILED action (after fixing the cause).
CREATE FUNCTION api.requeue_scheduled_action(p_action_id uuid, p_ctx jsonb)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_ctx  jsonb := ops.require_ctx(p_ctx);
  v_corr text;
BEGIN
  UPDATE ops.scheduled_actions sa
     SET status = 'PENDING', attempts = 0, run_at = now(), last_error = NULL
   WHERE sa.id = p_action_id AND sa.status = 'FAILED'
  RETURNING sa.correlation_id INTO v_corr;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT409', 'ACTION_NOT_FAILED', 'only FAILED actions can be requeued');
  END IF;
  PERFORM ops.write_log(v_ctx || jsonb_build_object('correlation_id', v_corr), 'SCHEDULED_ACTION', p_action_id::text,
    'ACTION_REQUEUED', 'SUCCESS');
  RETURN 'PENDING';
END;
$$;

-- =============================================================================================
-- Notifications (at-most-once intent per dedupe key)
-- =============================================================================================
CREATE FUNCTION api.begin_notification(
  p_dedupe_key     text,
  p_channel        text,
  p_template_key   text,
  p_recipient      text,
  p_application_id uuid,
  p_entity_type    text,
  p_entity_id      text,
  p_ctx            jsonb)
RETURNS TABLE (notification_id uuid, should_send boolean, status text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx jsonb := ops.require_ctx(p_ctx);
  v_row ops.notifications%ROWTYPE;
BEGIN
  IF nullif(btrim(p_dedupe_key), '') IS NULL OR nullif(btrim(p_recipient), '') IS NULL THEN
    PERFORM ops.fail('NT400', 'INVALID_NOTIFICATION', 'dedupe_key and recipient are required');
  END IF;

  INSERT INTO ops.notifications (dedupe_key, channel, template_key, recipient, application_id,
                                 entity_type, entity_id, correlation_id)
  VALUES (p_dedupe_key, upper(p_channel), lower(p_template_key), p_recipient, p_application_id,
          upper(p_entity_type), p_entity_id, nullif(v_ctx->>'correlation_id', ''))
  ON CONFLICT ON CONSTRAINT notifications_dedupe_uq DO NOTHING
  RETURNING * INTO v_row;

  IF FOUND THEN
    RETURN QUERY SELECT v_row.id, true, v_row.status;
    RETURN;
  END IF;

  SELECT * INTO v_row FROM ops.notifications n WHERE n.dedupe_key = p_dedupe_key FOR UPDATE;
  IF v_row.status IN ('SENT', 'SKIPPED') THEN
    PERFORM ops.write_log(v_ctx, 'NOTIFICATION', v_row.id::text, 'DUPLICATE_NOTIFICATION_SUPPRESSED', 'SKIPPED',
      jsonb_build_object('dedupe_key', p_dedupe_key, 'template', v_row.template_key));
    RETURN QUERY SELECT v_row.id, false, v_row.status;
    RETURN;
  END IF;

  UPDATE ops.notifications n SET status = 'PENDING', attempts = n.attempts + 1
   WHERE n.id = v_row.id
  RETURNING * INTO v_row;
  RETURN QUERY SELECT v_row.id, true, v_row.status;
END;
$$;

CREATE FUNCTION api.finish_notification(
  p_notification_id     uuid,
  p_status              text,
  p_provider_message_id text,
  p_error               text,
  p_ctx                 jsonb)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_ctx    jsonb := ops.require_ctx(p_ctx);
  v_status text  := upper(p_status);
  v_row    ops.notifications%ROWTYPE;
BEGIN
  IF v_status NOT IN ('SENT', 'FAILED', 'SKIPPED') THEN
    PERFORM ops.fail('NT400', 'INVALID_STATUS', 'status must be SENT, FAILED or SKIPPED');
  END IF;
  UPDATE ops.notifications n
     SET status = v_status,
         provider_message_id = coalesce(p_provider_message_id, n.provider_message_id),
         last_error = CASE WHEN v_status = 'FAILED' THEN left(p_error, 4000) END,
         sent_at = CASE WHEN v_status = 'SENT' THEN now() END
   WHERE n.id = p_notification_id AND n.status = 'PENDING'
  RETURNING * INTO v_row;
  IF NOT FOUND THEN
    SELECT n.status INTO v_status FROM ops.notifications n WHERE n.id = p_notification_id;
    IF NOT FOUND THEN
      PERFORM ops.fail('NT404', 'NOTIFICATION_NOT_FOUND', format('notification %s does not exist', p_notification_id));
    END IF;
    RETURN v_status;
  END IF;

  PERFORM ops.write_log(v_ctx || jsonb_build_object('correlation_id', coalesce(v_row.correlation_id, v_ctx->>'correlation_id')),
    'NOTIFICATION', v_row.id::text,
    CASE v_status WHEN 'SENT' THEN 'NOTIFICATION_SENT' WHEN 'FAILED' THEN 'NOTIFICATION_FAILED' ELSE 'NOTIFICATION_SKIPPED' END,
    CASE v_status WHEN 'FAILED' THEN 'FAILURE' WHEN 'SKIPPED' THEN 'SKIPPED' ELSE 'SUCCESS' END,
    jsonb_build_object('channel', v_row.channel, 'template', v_row.template_key),
    NULL, NULL, CASE WHEN v_status = 'FAILED' THEN 'NOTIFICATION_FAILED' END, p_error);
  RETURN v_status;
END;
$$;

-- migrate:down
DROP FUNCTION IF EXISTS api.finish_notification, api.begin_notification, api.requeue_scheduled_action,
  api.complete_scheduled_action, api.claim_scheduled_actions, api.cancel_scheduled_actions, api.schedule_action,
  api.resolve_error, api.begin_error_replay, api.record_error, ops.record_error, api.finish_workflow_execution,
  api.start_workflow_execution, api.fail_event, api.register_event, api.log_action, api.transition_application_status,
  hiring.transition_application, hiring.record_status_history, ops.enqueue_action, ops.write_log,
  ops.ctx_retry_count, ops.require_ctx;
