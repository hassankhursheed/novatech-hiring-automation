-- migrate:up

-- A closed application leaves nothing open behind it.
--
--   * Rejecting a shortlisted candidate used to leave the interview invitation open: the candidate's link still
--     offered slots, and booking then failed. Every terminal status (REJECTED, WITHDRAWN, DECLINED, OFFER_EXPIRED,
--     ONBOARDED) now cancels the application's open interviews (a booked slot is free again) and withdraws an open
--     offer, in the same transaction as the status change.
--   * The interviewer of a booked interview is told when it is cancelled, whoever cancels it (candidate, staff,
--     withdrawal, closure): NOTIFY_INTERVIEW_CANCELLED, sent by WF-04.

CREATE FUNCTION hiring.notify_interview_cancellation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  PERFORM ops.enqueue_action('NOTIFY_INTERVIEW_CANCELLED', 'INTERVIEW', NEW.id, NEW.application_id, now(),
    'NOTIFY_INTERVIEW_CANCELLED:' || NEW.id, jsonb_build_object('interview_id', NEW.id), NEW.correlation_id);
  RETURN NEW;
END;
$$;

CREATE TRIGGER interviews_notify_cancellation
  AFTER UPDATE OF status ON hiring.interviews
  FOR EACH ROW WHEN (OLD.status = 'CONFIRMED' AND NEW.status = 'CANCELLED')
  EXECUTE FUNCTION hiring.notify_interview_cancellation();

-- Cancelling an entity's timers never cancels the notice about the cancellation itself.
CREATE OR REPLACE FUNCTION ops.cancel_entity_actions(p_entity_type text, p_entity_id uuid, p_action_types text[], p_reason text)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
  v_count integer;
BEGIN
  UPDATE ops.scheduled_actions sa
     SET status = 'CANCELLED', cancelled_at = now(), cancel_reason = p_reason
   WHERE sa.entity_type = p_entity_type AND sa.entity_id = p_entity_id AND sa.status = 'PENDING'
     AND (p_action_types IS NULL AND sa.action_type <> 'NOTIFY_INTERVIEW_CANCELLED'
          OR sa.action_type = ANY (p_action_types));
  GET DIAGNOSTICS v_count = ROW_COUNT;
  RETURN v_count;
END;
$$;

CREATE OR REPLACE FUNCTION hiring.transition_application(
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

  -- The candidate's status update. The internal reason is deliberately not part of the payload.
  IF v_target.candidate_notice IS NOT NULL THEN
    PERFORM ops.enqueue_action(
      'NOTIFY_CANDIDATE', 'APPLICATION', v_app.id, v_app.id, now(),
      'NOTIFY_CANDIDATE:' || v_app.id::text || ':' || v_history_id::text,
      jsonb_build_object('template_key', v_target.candidate_notice, 'from_status', v_app.status,
                         'to_status', p_to_status, 'history_id', v_history_id),
      v_corr);
  END IF;

  IF v_target.is_terminal THEN
    UPDATE ops.scheduled_actions sa
       SET status = 'CANCELLED', cancelled_at = now(), cancel_reason = 'application closed as ' || p_to_status
     WHERE sa.application_id = v_app.id
       AND sa.status = 'PENDING'
       AND sa.action_type IS DISTINCT FROM v_target.on_enter_action
       AND sa.action_type IS DISTINCT FROM 'NOTIFY_INTERVIEW_CANCELLED'
       AND sa.dedupe_key IS DISTINCT FROM 'NOTIFY_CANDIDATE:' || v_app.id::text || ':' || v_history_id::text;

    -- Nothing stays open: the candidate's links stop offering slots or terms, a booked slot is free again, and the
    -- interviewer of a booked interview is told (trigger interviews_notify_cancellation).
    UPDATE hiring.interview_slots s SET status = 'OPEN'
     WHERE s.status = 'BOOKED'
       AND s.id IN (SELECT i.slot_id FROM hiring.interviews i WHERE i.application_id = v_app.id AND i.status = 'CONFIRMED');
    UPDATE hiring.interviews i
       SET status = 'CANCELLED', cancelled_at = now(), cancel_reason = 'application closed as ' || p_to_status
     WHERE i.application_id = v_app.id AND i.status IN ('INVITED', 'CONFIRMED');
    UPDATE hiring.offers o SET status = 'WITHDRAWN'
     WHERE o.application_id = v_app.id AND o.status IN ('PENDING_APPROVAL', 'APPROVED', 'SENT', 'NEGOTIATION');
  END IF;

  RETURN QUERY SELECT v_app.id, v_app.status, p_to_status, true, v_history_id;
END;
$$;

-- Applications closed before this change: close what they left open (no emails for old bookings).
ALTER TABLE hiring.interviews DISABLE TRIGGER interviews_notify_cancellation;
UPDATE hiring.interview_slots s SET status = 'OPEN'
 WHERE s.status = 'BOOKED'
   AND s.id IN (SELECT i.slot_id FROM hiring.interviews i JOIN hiring.applications a ON a.id = i.application_id
                 JOIN hiring.application_statuses st ON st.code = a.status
                WHERE st.is_terminal AND i.status = 'CONFIRMED');
UPDATE hiring.interviews i
   SET status = 'CANCELLED', cancelled_at = now(), cancel_reason = 'application closed as ' || a.status
  FROM hiring.applications a JOIN hiring.application_statuses st ON st.code = a.status
 WHERE a.id = i.application_id AND st.is_terminal AND i.status IN ('INVITED', 'CONFIRMED');
ALTER TABLE hiring.interviews ENABLE TRIGGER interviews_notify_cancellation;
UPDATE hiring.offers o SET status = 'WITHDRAWN'
  FROM hiring.applications a JOIN hiring.application_statuses st ON st.code = a.status
 WHERE a.id = o.application_id AND st.is_terminal
   AND o.status IN ('PENDING_APPROVAL', 'APPROVED', 'SENT', 'NEGOTIATION');

-- migrate:down
CREATE OR REPLACE FUNCTION hiring.transition_application(
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

  -- The candidate's status update. The internal reason is deliberately not part of the payload.
  IF v_target.candidate_notice IS NOT NULL THEN
    PERFORM ops.enqueue_action(
      'NOTIFY_CANDIDATE', 'APPLICATION', v_app.id, v_app.id, now(),
      'NOTIFY_CANDIDATE:' || v_app.id::text || ':' || v_history_id::text,
      jsonb_build_object('template_key', v_target.candidate_notice, 'from_status', v_app.status,
                         'to_status', p_to_status, 'history_id', v_history_id),
      v_corr);
  END IF;

  IF v_target.is_terminal THEN
    UPDATE ops.scheduled_actions sa
       SET status = 'CANCELLED', cancelled_at = now(), cancel_reason = 'application closed as ' || p_to_status
     WHERE sa.application_id = v_app.id
       AND sa.status = 'PENDING'
       AND sa.action_type IS DISTINCT FROM v_target.on_enter_action
       AND sa.dedupe_key IS DISTINCT FROM 'NOTIFY_CANDIDATE:' || v_app.id::text || ':' || v_history_id::text;
  END IF;

  RETURN QUERY SELECT v_app.id, v_app.status, p_to_status, true, v_history_id;
END;
$$;

CREATE OR REPLACE FUNCTION ops.cancel_entity_actions(p_entity_type text, p_entity_id uuid, p_action_types text[], p_reason text)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
  v_count integer;
BEGIN
  UPDATE ops.scheduled_actions sa
     SET status = 'CANCELLED', cancelled_at = now(), cancel_reason = p_reason
   WHERE sa.entity_type = p_entity_type AND sa.entity_id = p_entity_id AND sa.status = 'PENDING'
     AND (p_action_types IS NULL OR sa.action_type = ANY (p_action_types));
  GET DIAGNOSTICS v_count = ROW_COUNT;
  RETURN v_count;
END;
$$;

DROP TRIGGER interviews_notify_cancellation ON hiring.interviews;
DROP FUNCTION hiring.notify_interview_cancellation();
