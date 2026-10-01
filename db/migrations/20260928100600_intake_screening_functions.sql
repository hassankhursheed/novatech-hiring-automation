-- migrate:up

-- =============================================================================================
-- Read model used by workflows for templating and for re-checking state before acting.
-- =============================================================================================
CREATE FUNCTION api.application_snapshot(p_application_id uuid)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT jsonb_build_object(
    'application_id',    a.id,
    'application_code',  a.application_code,
    'correlation_id',    a.correlation_id,
    'status',            a.status,
    'status_changed_at', a.status_changed_at,
    'version',           a.version,
    'review_reason',     a.review_reason,
    'application_score', a.application_score,
    'ai_recommendation', a.ai_recommendation,
    'interview_score',   a.interview_score,
    'final_score',       a.final_score,
    'expected_salary',   a.expected_salary,
    'salary_currency',   a.salary_currency,
    'available_from',    a.available_from,
    'candidate', jsonb_build_object(
       'candidate_id', c.id, 'candidate_code', c.candidate_code, 'full_name', c.full_name,
       'email', c.email, 'phone', c.phone_e164, 'city', c.city),
    'position', jsonb_build_object(
       'job_position_id', p.id, 'code', p.code, 'title', p.title, 'department', p.department,
       'hiring_manager_id', p.hiring_manager_id, 'default_interviewer_id', p.default_interviewer_id),
    'company', jsonb_build_object(
       'name', hiring.setting_text('company.name'), 'timezone', hiring.company_timezone()))
  FROM hiring.applications a
  JOIN hiring.candidates c    ON c.id = a.candidate_id
  JOIN hiring.job_positions p ON p.id = a.job_position_id
  WHERE a.id = p_application_id
$$;

-- =============================================================================================
-- WF-02: persist a validated submission.
-- Input p_submission = response of the backend validator:
--   { "outcome": "VALID|NEEDS_REVIEW|INVALID", "issues": [...], "application": {...}, "validator_version": "..." }
-- One transaction: candidate upsert, duplicate detection, application creation, NEW -> VALIDATING -> result.
-- Idempotent: a completed event returns its stored result.
-- =============================================================================================
CREATE FUNCTION api.submit_application(p_event_id uuid, p_submission jsonb, p_ctx jsonb)
RETURNS TABLE (outcome text, replayed boolean, correlation_id text, application_id uuid, application_code text,
               candidate_id uuid, candidate_code text, status text, reason text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx          jsonb := ops.require_ctx(p_ctx);
  v_app          jsonb := coalesce(p_submission->'application', '{}'::jsonb);
  v_outcome      text  := upper(coalesce(p_submission->>'outcome', ''));
  v_issues       jsonb := coalesce(p_submission->'issues', '[]'::jsonb);
  v_event        ops.processed_events%ROWTYPE;
  v_position     hiring.job_positions%ROWTYPE;
  v_candidate    hiring.candidates%ROWTYPE;
  v_existing     hiring.applications%ROWTYPE;
  v_email        text;
  v_phone        text;
  v_name         text;
  v_consent_at   timestamptz;
  v_phone_match  hiring.candidates%ROWTYPE;
  v_year         text;
  v_reason       text;
  v_app_id       uuid;
  v_app_code     text;
  v_final_status text;
  v_result       jsonb;
BEGIN
  IF v_outcome NOT IN ('VALID', 'NEEDS_REVIEW', 'INVALID') THEN
    PERFORM ops.fail('NT400', 'INVALID_SUBMISSION', 'outcome must be VALID, NEEDS_REVIEW or INVALID');
  END IF;
  IF jsonb_typeof(v_issues) <> 'array' OR jsonb_typeof(v_app) <> 'object' THEN
    PERFORM ops.fail('NT400', 'INVALID_SUBMISSION', 'issues must be an array and application an object');
  END IF;

  SELECT * INTO v_event FROM ops.processed_events e WHERE e.id = p_event_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'EVENT_NOT_FOUND', format('event %s does not exist', p_event_id));
  END IF;
  v_ctx := v_ctx || jsonb_build_object('correlation_id', v_event.correlation_id);

  -- Replay of a completed event: return the original outcome, create nothing.
  IF v_event.status = 'COMPLETED' THEN
    RETURN QUERY
      SELECT v_event.outcome, true, v_event.correlation_id,
             (v_event.result->>'application_id')::uuid, v_event.result->>'application_code',
             (v_event.result->>'candidate_id')::uuid, v_event.result->>'candidate_code',
             (SELECT a.status FROM hiring.applications a WHERE a.id = (v_event.result->>'application_id')::uuid),
             v_event.outcome_reason;
    RETURN;
  END IF;

  v_reason := (SELECT string_agg(coalesce(i->>'message', i->>'code'), '; ') FROM jsonb_array_elements(v_issues) AS i);

  -- Unusable submission (no identity, unknown/closed position): recorded with an explicit reason, never dropped.
  IF v_outcome = 'INVALID' THEN
    v_reason := coalesce(v_reason, 'invalid submission');
    UPDATE ops.processed_events e
       SET status = 'COMPLETED', outcome = 'INVALID', outcome_reason = v_reason, completed_at = now(),
           result = jsonb_build_object('issues', v_issues)
     WHERE e.id = v_event.id;
    PERFORM ops.write_log(v_ctx, 'EVENT', v_event.id::text, 'SUBMISSION_REJECTED_INVALID', 'SUCCESS',
      jsonb_build_object('issues', v_issues));
    RETURN QUERY SELECT 'INVALID'::text, false, v_event.correlation_id, NULL::uuid, NULL::text,
                        NULL::uuid, NULL::text, NULL::text, v_reason;
    RETURN;
  END IF;

  SELECT * INTO v_position FROM hiring.job_positions p WHERE p.code = upper(btrim(v_app->>'position_code'));
  IF NOT FOUND THEN
    PERFORM ops.fail('NT422', 'UNKNOWN_POSITION', format('position %s does not exist', v_app->>'position_code'));
  END IF;

  v_email := lower(nullif(btrim(v_app->>'email'), ''));
  v_phone := nullif(btrim(v_app->>'phone'), '');
  v_name  := nullif(btrim(v_app->>'full_name'), '');
  IF v_name IS NULL OR (v_email IS NULL AND v_phone IS NULL) THEN
    PERFORM ops.fail('NT422', 'MISSING_IDENTITY', 'a name and an email or phone are required to create an application');
  END IF;
  IF coalesce(v_app->>'consent', 'false') = 'true' THEN
    v_consent_at := now();
  END IF;
  v_year := to_char(now() AT TIME ZONE hiring.company_timezone(), 'YYYY');

  -- ---- Candidate identity: email is the key; a phone match on a different email is only flagged ----
  IF v_email IS NOT NULL THEN
    SELECT * INTO v_candidate FROM hiring.candidates c WHERE c.email = v_email FOR UPDATE;
  ELSE
    SELECT * INTO v_candidate FROM hiring.candidates c
     WHERE c.email IS NULL AND c.phone_e164 = v_phone ORDER BY c.created_at LIMIT 1 FOR UPDATE;
  END IF;

  IF v_candidate.id IS NULL THEN
    BEGIN
      INSERT INTO hiring.candidates (candidate_code, full_name, email, phone_e164, city, linkedin_url, consent_at)
      VALUES (ops.next_code('CAN', v_year, 4), v_name, v_email, v_phone,
              nullif(btrim(v_app->>'city'), ''), nullif(btrim(v_app->>'linkedin_url'), ''), v_consent_at)
      RETURNING * INTO v_candidate;
      PERFORM ops.write_log(v_ctx, 'CANDIDATE', v_candidate.id::text, 'CANDIDATE_CREATED', 'SUCCESS',
        jsonb_build_object('candidate_code', v_candidate.candidate_code));
    EXCEPTION WHEN unique_violation THEN
      -- A concurrent submission created the same candidate first.
      SELECT * INTO v_candidate FROM hiring.candidates c WHERE c.email = v_email FOR UPDATE;
      IF NOT FOUND THEN
        RAISE;
      END IF;
    END;
  ELSE
    UPDATE hiring.candidates c
       SET full_name    = v_name,
           phone_e164   = coalesce(v_phone, c.phone_e164),
           city         = coalesce(nullif(btrim(v_app->>'city'), ''), c.city),
           linkedin_url = coalesce(nullif(btrim(v_app->>'linkedin_url'), ''), c.linkedin_url),
           consent_at   = coalesce(v_consent_at, c.consent_at)
     WHERE c.id = v_candidate.id
    RETURNING * INTO v_candidate;
    PERFORM ops.write_log(v_ctx, 'CANDIDATE', v_candidate.id::text, 'CANDIDATE_UPDATED', 'SUCCESS',
      jsonb_build_object('candidate_code', v_candidate.candidate_code, 'reason', 'returning applicant'));
  END IF;

  IF v_phone IS NOT NULL THEN
    SELECT * INTO v_phone_match FROM hiring.candidates c
     WHERE c.phone_e164 = v_phone AND c.id <> v_candidate.id
     ORDER BY c.created_at LIMIT 1;
  END IF;

  -- ---- Business duplicate: an active application for the same position already exists ----
  SELECT * INTO v_existing FROM hiring.applications a
   WHERE a.candidate_id = v_candidate.id AND a.job_position_id = v_position.id
     AND a.status NOT IN ('REJECTED', 'DECLINED', 'OFFER_EXPIRED', 'WITHDRAWN', 'ONBOARDED')
   FOR UPDATE;

  IF FOUND THEN
    v_reason := format('candidate %s already has active application %s (%s) for %s',
                       v_candidate.candidate_code, v_existing.application_code, v_existing.status, v_position.title);
    v_result := jsonb_build_object('application_id', v_existing.id, 'application_code', v_existing.application_code,
                                   'candidate_id', v_candidate.id, 'candidate_code', v_candidate.candidate_code,
                                   'duplicate_of', v_existing.id);
    UPDATE ops.processed_events e
       SET status = 'COMPLETED', outcome = 'DUPLICATE', outcome_reason = v_reason, completed_at = now(), result = v_result
     WHERE e.id = v_event.id;
    PERFORM ops.write_log(v_ctx, 'APPLICATION', v_existing.id::text, 'DUPLICATE_APPLICATION_PREVENTED', 'SKIPPED',
      jsonb_build_object('event_id', v_event.id, 'existing_correlation_id', v_existing.correlation_id));
    RETURN QUERY SELECT 'DUPLICATE'::text, false, v_event.correlation_id, v_existing.id, v_existing.application_code,
                        v_candidate.id, v_candidate.candidate_code, v_existing.status, v_reason;
    RETURN;
  END IF;

  IF v_phone_match.id IS NOT NULL THEN
    v_issues  := v_issues || jsonb_build_array(jsonb_build_object(
                   'field', 'phone', 'code', 'POSSIBLE_DUPLICATE_CANDIDATE', 'severity', 'REVIEW',
                   'message', format('phone number is already used by candidate %s', v_phone_match.candidate_code)));
    v_outcome := 'NEEDS_REVIEW';
    v_reason  := (SELECT string_agg(coalesce(i->>'message', i->>'code'), '; ') FROM jsonb_array_elements(v_issues) AS i);
  END IF;

  -- ---- Create the application and walk it through intake ----
  INSERT INTO hiring.applications (
    application_code, candidate_id, job_position_id, source_event_id, correlation_id, source,
    experience_years, skills, expected_salary, salary_currency, available_from, notice_period_days,
    current_company, current_title, cover_letter, cv_storage_key, cv_text, validation_issues, possible_duplicate_of)
  VALUES (
    ops.next_code('APP', v_year, 5), v_candidate.id, v_position.id, v_event.id, v_event.correlation_id, v_event.source,
    (v_app->>'experience_years')::numeric,
    ARRAY(SELECT DISTINCT lower(btrim(s)) FROM jsonb_array_elements_text(coalesce(v_app->'skills', '[]'::jsonb)) AS s
           WHERE btrim(s) <> ''),
    (v_app->>'expected_salary')::bigint,
    coalesce(upper(nullif(v_app->>'salary_currency', '')), v_position.currency),
    (v_app->>'available_from')::date,
    (v_app->>'notice_period_days')::smallint,
    nullif(btrim(v_app->>'current_company'), ''), nullif(btrim(v_app->>'current_title'), ''),
    nullif(v_app->>'cover_letter', ''), nullif(v_app->>'cv_storage_key', ''), nullif(v_app->>'cv_text', ''),
    v_issues, v_phone_match.id)
  RETURNING id, application_code INTO v_app_id, v_app_code;

  PERFORM hiring.record_status_history(v_app_id, v_candidate.id, NULL, 'NEW', 'submission received', v_ctx,
    jsonb_build_object('event_id', v_event.id, 'source', v_event.source));
  PERFORM ops.write_log(v_ctx, 'APPLICATION', v_app_id::text, 'APPLICATION_CREATED', 'SUCCESS',
    jsonb_build_object('application_code', v_app_code, 'position', v_position.code), NULL, 'NEW');

  PERFORM 1 FROM hiring.transition_application(v_app_id, 'VALIDATING', 'validation started', v_ctx, 'NEW',
    'submit_application', jsonb_build_object('validator_version', p_submission->>'validator_version'));

  IF v_outcome = 'VALID' THEN
    v_final_status := 'VALIDATED';
    PERFORM 1 FROM hiring.transition_application(v_app_id, 'VALIDATED', 'submission complete and valid', v_ctx,
      'VALIDATING', 'submit_application');
  ELSE
    v_final_status := 'SCREENING_REVIEW';
    PERFORM 1 FROM hiring.transition_application(v_app_id, 'SCREENING_REVIEW',
      coalesce(v_reason, 'submission needs review'), v_ctx, 'VALIDATING', 'submit_application',
      jsonb_build_object('issues', v_issues));
  END IF;

  v_result := jsonb_build_object('application_id', v_app_id, 'application_code', v_app_code,
                                 'candidate_id', v_candidate.id, 'candidate_code', v_candidate.candidate_code);
  UPDATE ops.processed_events e
     SET status = 'COMPLETED',
         outcome = CASE WHEN v_outcome = 'VALID' THEN 'ACCEPTED' ELSE 'NEEDS_REVIEW' END,
         outcome_reason = v_reason, completed_at = now(), result = v_result
   WHERE e.id = v_event.id;

  RETURN QUERY SELECT CASE WHEN v_outcome = 'VALID' THEN 'ACCEPTED' ELSE 'NEEDS_REVIEW' END, false,
                      v_event.correlation_id, v_app_id, v_app_code, v_candidate.id, v_candidate.candidate_code,
                      v_final_status, v_reason;
END;
$$;

-- =============================================================================================
-- WF-03: rule-based score. Idempotent on (application, scoring_version, input_hash).
-- p_score = backend /v1/screening/score response.
-- =============================================================================================
CREATE FUNCTION api.record_application_score(p_application_id uuid, p_score jsonb, p_ctx jsonb)
RETURNS TABLE (application_id uuid, score numeric, route text, status text, replayed boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx      jsonb := ops.require_ctx(p_ctx);
  v_app      hiring.applications%ROWTYPE;
  v_existing hiring.application_scores%ROWTYPE;
  v_has      boolean;
  v_version  integer;
  v_hash     text := nullif(btrim(p_score->>'input_hash'), '');
  v_route    text := upper(coalesce(p_score->>'route', ''));
BEGIN
  SELECT * INTO v_app FROM hiring.applications a WHERE a.id = p_application_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'APPLICATION_NOT_FOUND', format('application %s does not exist', p_application_id));
  END IF;
  v_ctx := v_ctx || jsonb_build_object('correlation_id', v_app.correlation_id);

  IF coalesce(p_score->>'scoring_version', '') !~ '^[0-9]+$' OR v_hash IS NULL
     OR v_route NOT IN ('SHORTLIST', 'REVIEW', 'REJECT') OR jsonb_typeof(p_score->'breakdown') <> 'array' THEN
    PERFORM ops.fail('NT400', 'INVALID_SCORE', 'scoring_version, input_hash, route and breakdown[] are required');
  END IF;
  v_version := (p_score->>'scoring_version')::integer;

  SELECT * INTO v_existing FROM hiring.application_scores s
   WHERE s.application_id = v_app.id AND s.scoring_version = v_version AND s.input_hash = v_hash;
  v_has := FOUND;

  IF v_has AND v_app.status <> 'VALIDATED' THEN
    RETURN QUERY SELECT v_app.id, v_existing.score, v_existing.route, v_app.status, true;
    RETURN;
  END IF;
  IF v_app.status <> 'VALIDATED' THEN
    PERFORM ops.fail('NT409', 'STALE_STATE', format('application %s is %s; scoring requires VALIDATED',
                                                   v_app.application_code, v_app.status));
  END IF;

  IF NOT v_has THEN
    INSERT INTO hiring.application_scores (application_id, scoring_version, input_hash, points_awarded,
      points_possible, score, route, shortlist_min_score, review_min_score, breakdown, correlation_id)
    VALUES (v_app.id, v_version, v_hash, (p_score->>'points_awarded')::integer, (p_score->>'points_possible')::integer,
            (p_score->>'score')::numeric, v_route, (p_score->>'shortlist_min_score')::numeric,
            (p_score->>'review_min_score')::numeric, p_score->'breakdown', v_app.correlation_id)
    RETURNING * INTO v_existing;
  END IF;

  UPDATE hiring.applications a SET application_score = v_existing.score WHERE a.id = v_app.id;

  PERFORM 1 FROM hiring.transition_application(v_app.id, 'SCORED',
    format('rule score %s (%s) using scoring v%s', v_existing.score, v_route, v_version), v_ctx, 'VALIDATED',
    'record_application_score', jsonb_build_object('score', v_existing.score, 'scoring_version', v_version));

  RETURN QUERY SELECT v_app.id, v_existing.score, v_existing.route, 'SCORED'::text, false;
END;
$$;

-- =============================================================================================
-- WF-03: advisory AI analysis. Stored for humans; never changes status.
-- A FALLBACK row (AI unavailable/malformed) is upgraded if a later run for the same input completes.
-- =============================================================================================
CREATE FUNCTION api.record_ai_analysis(p_application_id uuid, p_analysis jsonb, p_ctx jsonb)
RETURNS TABLE (analysis_id uuid, status text, recommendation text, replayed boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx    jsonb := ops.require_ctx(p_ctx);
  v_app    hiring.applications%ROWTYPE;
  v_row    hiring.ai_analyses%ROWTYPE;
  v_status text := upper(coalesce(p_analysis->>'status', ''));
  v_a      jsonb := coalesce(p_analysis->'analysis', '{}'::jsonb);
  v_new    boolean := false;
BEGIN
  SELECT * INTO v_app FROM hiring.applications a WHERE a.id = p_application_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'APPLICATION_NOT_FOUND', format('application %s does not exist', p_application_id));
  END IF;
  v_ctx := v_ctx || jsonb_build_object('correlation_id', v_app.correlation_id);

  IF v_status NOT IN ('COMPLETED', 'FALLBACK') OR nullif(p_analysis->>'prompt_version', '') IS NULL
     OR nullif(p_analysis->>'input_hash', '') IS NULL THEN
    PERFORM ops.fail('NT400', 'INVALID_AI_ANALYSIS', 'status, prompt_version and input_hash are required');
  END IF;

  INSERT INTO hiring.ai_analyses AS x (
    application_id, status, provider, model, prompt_version, input_hash, technical_strength, experience_relevance,
    communication_indication, missing_skills, summary, recommendation, fallback_reason, attempts, latency_ms,
    trace_id, correlation_id)
  VALUES (
    v_app.id, v_status, p_analysis->>'provider', p_analysis->>'model', p_analysis->>'prompt_version',
    p_analysis->>'input_hash',
    (v_a->>'technical_strength')::smallint, (v_a->>'experience_relevance')::smallint,
    (v_a->>'communication_indication')::smallint,
    ARRAY(SELECT jsonb_array_elements_text(coalesce(v_a->'missing_skills', '[]'::jsonb))),
    v_a->>'summary', upper(v_a->>'recommendation'), p_analysis->>'fallback_reason',
    coalesce((p_analysis->>'attempts')::smallint, 1), (p_analysis->>'latency_ms')::integer,
    p_analysis->>'trace_id', v_app.correlation_id)
  ON CONFLICT ON CONSTRAINT ai_analyses_input_uq DO UPDATE
    SET status = EXCLUDED.status, provider = EXCLUDED.provider, model = EXCLUDED.model,
        technical_strength = EXCLUDED.technical_strength, experience_relevance = EXCLUDED.experience_relevance,
        communication_indication = EXCLUDED.communication_indication, missing_skills = EXCLUDED.missing_skills,
        summary = EXCLUDED.summary, recommendation = EXCLUDED.recommendation, fallback_reason = NULL,
        attempts = EXCLUDED.attempts, latency_ms = EXCLUDED.latency_ms, trace_id = EXCLUDED.trace_id,
        created_at = now()
    WHERE x.status = 'FALLBACK' AND EXCLUDED.status = 'COMPLETED'
  RETURNING x.* INTO v_row;

  IF FOUND THEN
    v_new := true;
  ELSE
    SELECT * INTO v_row FROM hiring.ai_analyses x
     WHERE x.application_id = v_app.id AND x.prompt_version = p_analysis->>'prompt_version'
       AND x.input_hash = p_analysis->>'input_hash';
  END IF;

  IF v_new THEN
    UPDATE hiring.applications a SET ai_recommendation = v_row.recommendation WHERE a.id = v_app.id;
    PERFORM ops.write_log(v_ctx, 'APPLICATION', v_app.id::text,
      CASE WHEN v_row.status = 'COMPLETED' THEN 'AI_ANALYSIS_COMPLETED' ELSE 'AI_ANALYSIS_FALLBACK' END, 'SUCCESS',
      jsonb_build_object('analysis_id', v_row.id, 'recommendation', v_row.recommendation, 'model', v_row.model,
                         'fallback_reason', v_row.fallback_reason, 'attempts', v_row.attempts));
  END IF;

  RETURN QUERY SELECT v_row.id, v_row.status, v_row.recommendation, NOT v_new;
END;
$$;

-- =============================================================================================
-- WF-03: apply the screening decision computed by the backend policy (rules decide, AI can only add review).
-- p_decision = { "decision": "SHORTLISTED|SCREENING_REVIEW|REJECTED", "reason": "...", "policy_version": "..." }
-- =============================================================================================
CREATE FUNCTION api.apply_screening_decision(p_application_id uuid, p_decision jsonb, p_ctx jsonb)
RETURNS TABLE (application_id uuid, from_status text, to_status text, changed boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx      jsonb := ops.require_ctx(p_ctx);
  v_decision text  := upper(coalesce(p_decision->>'decision', ''));
  v_status   text;
BEGIN
  IF v_decision NOT IN ('SHORTLISTED', 'SCREENING_REVIEW', 'REJECTED') THEN
    PERFORM ops.fail('NT400', 'INVALID_DECISION', 'decision must be SHORTLISTED, SCREENING_REVIEW or REJECTED');
  END IF;
  IF nullif(btrim(p_decision->>'reason'), '') IS NULL THEN
    PERFORM ops.fail('NT400', 'REASON_REQUIRED', 'every screening decision needs a reason');
  END IF;

  SELECT a.status INTO v_status FROM hiring.applications a WHERE a.id = p_application_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'APPLICATION_NOT_FOUND', format('application %s does not exist', p_application_id));
  END IF;
  IF v_status = v_decision THEN
    RETURN QUERY SELECT p_application_id, v_status, v_status, false;
    RETURN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM hiring.application_scores s WHERE s.application_id = p_application_id) THEN
    PERFORM ops.fail('NT422', 'SCORE_REQUIRED', 'a screening decision requires a recorded score');
  END IF;

  RETURN QUERY
    SELECT t.application_id, t.from_status, t.to_status, t.changed
      FROM hiring.transition_application(p_application_id, v_decision, p_decision->>'reason', v_ctx, 'SCORED',
                                         'apply_screening_decision', p_decision) AS t;
END;
$$;

-- migrate:down
DROP FUNCTION IF EXISTS api.apply_screening_decision, api.record_ai_analysis, api.record_application_score,
  api.submit_application, api.application_snapshot;
