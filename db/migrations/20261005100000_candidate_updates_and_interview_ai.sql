-- migrate:up

-- =============================================================================================
-- 1. The candidate hears from us at every step
-- =============================================================================================
-- Most candidate emails are sent by the workflow that owns a step (interview invitation, booking confirmation, offer,
-- rejection, welcome). The remaining steps get a status update from WF-02 through the NOTIFY_CANDIDATE action, which
-- hiring.transition_application enqueues in the same transaction as the status change (transactional outbox).
ALTER TABLE hiring.application_statuses
  ADD COLUMN candidate_notice   text CHECK (candidate_notice ~ '^[a-z][a-z0-9_.]*$'),
  ADD COLUMN candidate_informed boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN hiring.application_statuses.candidate_notice IS
  'Template of the status update the candidate receives on entering this status (NOTIFY_CANDIDATE, sent by WF-02). '
  'NULL when the owning workflow already emails the candidate, or when the status is internal.';
COMMENT ON COLUMN hiring.application_statuses.candidate_informed IS
  'True when entering this status sends the candidate an email (a candidate_notice or the owning workflow''s email). '
  'A pending status update is skipped when a later candidate_informed status has already been reached.';

UPDATE hiring.application_statuses s
   SET candidate_notice = v.notice, candidate_informed = true
  FROM (VALUES ('SCREENING_REVIEW', 'application.under_review'),
               ('INTERVIEWED',      'interview.completed'),
               ('SELECTED',         'application.selected'),
               ('ONBOARDED',        'onboarding.finished'),
               ('WITHDRAWN',        'application.withdrawn')) AS v(code, notice)
 WHERE s.code = v.code;

-- Steps whose owning workflow emails the candidate itself.
UPDATE hiring.application_statuses s SET candidate_informed = true
 WHERE s.code IN ('SHORTLISTED',          -- WF-04 interview invitation with the slot link
                  'INTERVIEW_SCHEDULED',  -- WF-04 booking confirmation
                  'OFFERED',              -- WF-05 offer letter
                  'NEGOTIATION',          -- WF-05 acknowledgement of the request
                  'ACCEPTED', 'ONBOARDING', -- WF-06 welcome email with the first-day details
                  'REJECTED',             -- WF-02 rejection notice
                  'DECLINED', 'OFFER_EXPIRED'); -- WF-05 offer closed

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

-- =============================================================================================
-- 2. AI assessment of the interview (advisory)
-- =============================================================================================
-- After the scorecard is in, the backend asks the model to read the ratings, the interviewer's notes and the
-- screening analysis together. Like the screening analysis, it is advice: the weighted score decides, and the
-- assessment can only send a case to a person (disagreement, or notes that contradict the ratings).
CREATE TABLE hiring.interview_assessments (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  interview_id        uuid NOT NULL REFERENCES hiring.interviews(id),
  application_id      uuid NOT NULL REFERENCES hiring.applications(id),
  status              text NOT NULL CHECK (status IN ('COMPLETED','FALLBACK')),
  provider            text,
  model               text,
  prompt_version      text NOT NULL,
  input_hash          text NOT NULL,
  recommendation      text CHECK (recommendation IN ('SELECT','REVIEW','REJECT')),
  evidence_alignment  text CHECK (evidence_alignment IN ('ALIGNED','PARTIAL','CONTRADICTORY')),
  strengths           text[] NOT NULL DEFAULT '{}',
  concerns            text[] NOT NULL DEFAULT '{}',
  summary             text CHECK (char_length(summary) <= 1200),
  fallback_reason     text,
  attempts            smallint NOT NULL DEFAULT 1 CHECK (attempts BETWEEN 0 AND 5),
  latency_ms          integer CHECK (latency_ms >= 0),
  trace_id            text,
  correlation_id      text NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT interview_assessments_input_uq UNIQUE (interview_id, prompt_version, input_hash),
  CHECK (
    (status = 'COMPLETED' AND recommendation IS NOT NULL AND evidence_alignment IS NOT NULL AND summary IS NOT NULL)
    OR (status = 'FALLBACK' AND fallback_reason IS NOT NULL)
  )
);
COMMENT ON TABLE hiring.interview_assessments IS
  'Advisory AI reading of an interview scorecard. Never the actor of a decision.';
CREATE INDEX interview_assessments_application_idx ON hiring.interview_assessments (application_id, created_at);

INSERT INTO hiring.settings (key, value, value_type, description) VALUES
  ('evaluation.ai_enabled', 'true', 'boolean',
   'If true, an AI assessment of every interview scorecard is required: when it is unavailable, disagrees with the '
   'weighted score or finds notes that contradict the ratings, the case goes to the hiring manager');

-- p_assessment = AssessmentResult from POST /v1/interviews/ai-assessment
CREATE FUNCTION api.record_interview_assessment(p_interview_id uuid, p_assessment jsonb, p_ctx jsonb)
RETURNS TABLE (assessment_id uuid, status text, recommendation text, evidence_alignment text, replayed boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx    jsonb := ops.require_ctx(p_ctx);
  v_int    hiring.interviews%ROWTYPE;
  v_corr   text;
  v_row    hiring.interview_assessments%ROWTYPE;
  v_status text := upper(coalesce(p_assessment->>'status', ''));
  v_a      jsonb := coalesce(p_assessment->'assessment', '{}'::jsonb);
  v_new    boolean := false;
BEGIN
  SELECT * INTO v_int FROM hiring.interviews i WHERE i.id = p_interview_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'INTERVIEW_NOT_FOUND', format('interview %s does not exist', p_interview_id));
  END IF;
  SELECT a.correlation_id INTO v_corr FROM hiring.applications a WHERE a.id = v_int.application_id;
  v_ctx := v_ctx || jsonb_build_object('correlation_id', v_corr);

  IF v_status NOT IN ('COMPLETED', 'FALLBACK') OR nullif(p_assessment->>'prompt_version', '') IS NULL
     OR nullif(p_assessment->>'input_hash', '') IS NULL THEN
    PERFORM ops.fail('NT400', 'INVALID_AI_ASSESSMENT', 'status, prompt_version and input_hash are required');
  END IF;

  INSERT INTO hiring.interview_assessments AS x (
    interview_id, application_id, status, provider, model, prompt_version, input_hash, recommendation,
    evidence_alignment, strengths, concerns, summary, fallback_reason, attempts, latency_ms, trace_id, correlation_id)
  VALUES (
    v_int.id, v_int.application_id, v_status, p_assessment->>'provider', p_assessment->>'model',
    p_assessment->>'prompt_version', p_assessment->>'input_hash', upper(v_a->>'recommendation'),
    upper(v_a->>'evidence_alignment'),
    ARRAY(SELECT jsonb_array_elements_text(coalesce(v_a->'strengths', '[]'::jsonb))),
    ARRAY(SELECT jsonb_array_elements_text(coalesce(v_a->'concerns', '[]'::jsonb))),
    v_a->>'summary', p_assessment->>'fallback_reason', coalesce((p_assessment->>'attempts')::smallint, 1),
    (p_assessment->>'latency_ms')::integer, p_assessment->>'trace_id', v_corr)
  ON CONFLICT ON CONSTRAINT interview_assessments_input_uq DO UPDATE
    SET status = EXCLUDED.status, provider = EXCLUDED.provider, model = EXCLUDED.model,
        recommendation = EXCLUDED.recommendation, evidence_alignment = EXCLUDED.evidence_alignment,
        strengths = EXCLUDED.strengths, concerns = EXCLUDED.concerns, summary = EXCLUDED.summary,
        fallback_reason = NULL, attempts = EXCLUDED.attempts, latency_ms = EXCLUDED.latency_ms,
        trace_id = EXCLUDED.trace_id, created_at = now()
    WHERE x.status = 'FALLBACK' AND EXCLUDED.status = 'COMPLETED'
  RETURNING x.* INTO v_row;

  IF FOUND THEN
    v_new := true;
  ELSE
    SELECT * INTO v_row FROM hiring.interview_assessments x
     WHERE x.interview_id = v_int.id AND x.prompt_version = p_assessment->>'prompt_version'
       AND x.input_hash = p_assessment->>'input_hash';
  END IF;

  IF v_new THEN
    PERFORM ops.write_log(v_ctx, 'INTERVIEW', v_int.id::text,
      CASE WHEN v_row.status = 'COMPLETED' THEN 'AI_ASSESSMENT_COMPLETED' ELSE 'AI_ASSESSMENT_FALLBACK' END, 'SUCCESS',
      jsonb_build_object('assessment_id', v_row.id, 'recommendation', v_row.recommendation,
                         'evidence_alignment', v_row.evidence_alignment, 'model', v_row.model,
                         'fallback_reason', v_row.fallback_reason, 'attempts', v_row.attempts));
  END IF;

  RETURN QUERY SELECT v_row.id, v_row.status, v_row.recommendation, v_row.evidence_alignment, NOT v_new;
END;
$$;

-- migrate:down
DELETE FROM hiring.settings WHERE key = 'evaluation.ai_enabled';
DROP FUNCTION IF EXISTS api.record_interview_assessment(uuid, jsonb, jsonb);
DROP TABLE IF EXISTS hiring.interview_assessments;

-- The previous transition function (without the candidate status update).
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

ALTER TABLE hiring.application_statuses DROP COLUMN IF EXISTS candidate_notice,
                                        DROP COLUMN IF EXISTS candidate_informed;
