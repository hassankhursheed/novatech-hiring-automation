-- migrate:up

-- =============================================================================================
-- Settings used by the interview / offer / onboarding / operations functions
-- =============================================================================================
INSERT INTO hiring.settings (key, value, value_type, description) VALUES
  ('company.portal_url',          '"http://localhost:5173"',         'text',     'Base URL of the candidate/HR portal used in email links'),
  ('company.careers_email',       '"careers@novatech.example"',      'text',     'Sender address for candidate communication'),
  ('ops.alert_email',             '"ops-alerts@novatech.example"',   'text',     'Recipient of operational alerts (error queue, review queues)'),
  ('interview.slot_min_notice',   '"2 hours"',                       'interval', 'Earliest a slot can be offered/booked relative to now'),
  ('offer.joining_lead_days',     '14',                              'number',   'Default minimum days between offer and joining date')
ON CONFLICT (key) DO NOTHING;

-- =============================================================================================
-- Helpers
-- =============================================================================================
CREATE FUNCTION hiring.assert_candidate_actor(p_ctx jsonb, p_candidate_id uuid)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
  IF p_ctx->>'actor_type' = 'CANDIDATE' AND p_ctx->>'actor_id' IS DISTINCT FROM p_candidate_id::text THEN
    PERFORM ops.fail('NT403', 'NOT_YOUR_APPLICATION', 'this link does not belong to the candidate');
  END IF;
END;
$$;

CREATE FUNCTION hiring.first_staff_with_role(p_role text)
RETURNS uuid
LANGUAGE sql
STABLE
AS $$
  SELECT s.id FROM hiring.staff_members s WHERE s.is_active AND p_role = ANY (s.roles) ORDER BY s.created_at, s.id LIMIT 1
$$;

CREATE FUNCTION ops.cancel_entity_actions(p_entity_type text, p_entity_id uuid, p_action_types text[], p_reason text)
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

-- =============================================================================================
-- Interviews
-- =============================================================================================
CREATE FUNCTION api.create_interview_invitation(p_application_id uuid, p_ctx jsonb)
RETURNS TABLE (interview_id uuid, interview_code text, round smallint, status text, interviewer_id uuid,
               interviewer_name text, interviewer_email text, invite_expires_at timestamptz, created boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx      jsonb := ops.require_ctx(p_ctx);
  v_app      hiring.applications%ROWTYPE;
  v_pos      hiring.job_positions%ROWTYPE;
  v_last     hiring.interviews%ROWTYPE;
  v_int      hiring.interviews%ROWTYPE;
  v_staff    hiring.staff_members%ROWTYPE;
  v_expires  timestamptz;
BEGIN
  SELECT * INTO v_app FROM hiring.applications a WHERE a.id = p_application_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'APPLICATION_NOT_FOUND', format('application %s does not exist', p_application_id));
  END IF;
  v_ctx := v_ctx || jsonb_build_object('correlation_id', v_app.correlation_id);

  SELECT * INTO v_last FROM hiring.interviews i WHERE i.application_id = v_app.id ORDER BY i.round DESC LIMIT 1 FOR UPDATE;
  IF v_last.id IS NOT NULL AND v_last.status IN ('INVITED', 'CONFIRMED') THEN
    SELECT * INTO v_staff FROM hiring.staff_members s WHERE s.id = v_last.interviewer_id;
    RETURN QUERY SELECT v_last.id, v_last.interview_code, v_last.round, v_last.status, v_staff.id, v_staff.full_name,
                        v_staff.email, v_last.invited_at + hiring.setting_interval('interview.invite_expires_after'), false;
    RETURN;
  END IF;
  IF v_app.status <> 'SHORTLISTED' THEN
    PERFORM ops.fail('NT409', 'STALE_STATE', format('application %s is %s; invitations require SHORTLISTED',
                                                   v_app.application_code, v_app.status));
  END IF;

  SELECT * INTO v_pos FROM hiring.job_positions p WHERE p.id = v_app.job_position_id;
  SELECT * INTO v_staff FROM hiring.staff_members s
   WHERE s.id = coalesce(v_pos.default_interviewer_id, v_pos.hiring_manager_id) AND s.is_active;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT422', 'NO_INTERVIEWER_CONFIGURED', format('position %s has no active interviewer', v_pos.code));
  END IF;

  INSERT INTO hiring.interviews (interview_code, application_id, round, interviewer_id, correlation_id)
  VALUES (ops.next_code('INT', to_char(now() AT TIME ZONE hiring.company_timezone(), 'YYYY'), 4), v_app.id,
          coalesce(v_last.round, 0) + 1, v_staff.id, v_app.correlation_id)
  RETURNING * INTO v_int;

  v_expires := v_int.invited_at + hiring.setting_interval('interview.invite_expires_after');
  PERFORM ops.enqueue_action('INTERVIEW_INVITE_REMINDER', 'INTERVIEW', v_int.id, v_app.id,
    v_int.invited_at + hiring.setting_interval('interview.invite_reminder_after'),
    'INTERVIEW_INVITE_REMINDER:' || v_int.id, jsonb_build_object('interview_id', v_int.id), v_app.correlation_id);
  PERFORM ops.enqueue_action('INTERVIEW_INVITE_EXPIRY', 'INTERVIEW', v_int.id, v_app.id, v_expires,
    'INTERVIEW_INVITE_EXPIRY:' || v_int.id, jsonb_build_object('interview_id', v_int.id), v_app.correlation_id);

  PERFORM ops.write_log(v_ctx, 'INTERVIEW', v_int.id::text, 'INTERVIEW_INVITED', 'SUCCESS',
    jsonb_build_object('interview_code', v_int.interview_code, 'round', v_int.round, 'interviewer', v_staff.full_name,
                       'expires_at', v_expires));

  RETURN QUERY SELECT v_int.id, v_int.interview_code, v_int.round, v_int.status, v_staff.id, v_staff.full_name,
                      v_staff.email, v_expires, true;
END;
$$;

CREATE FUNCTION api.interview_slot_options(p_interview_id uuid, p_limit integer DEFAULT 10)
RETURNS TABLE (slot_id uuid, starts_at timestamptz, ends_at timestamptz)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT s.id, s.starts_at, s.ends_at
    FROM hiring.interview_slots s
    JOIN hiring.interviews i ON i.interviewer_id = s.interviewer_id
   WHERE i.id = p_interview_id AND s.status = 'OPEN'
     AND s.starts_at > now() + hiring.setting_interval('interview.slot_min_notice')
   ORDER BY s.starts_at
   LIMIT least(greatest(p_limit, 1), 50)
$$;

CREATE FUNCTION api.interview_snapshot(p_interview_id uuid)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT jsonb_build_object(
    'interview_id', i.id, 'interview_code', i.interview_code, 'round', i.round, 'status', i.status, 'mode', i.mode,
    'scheduled_start', i.scheduled_start, 'scheduled_end', i.scheduled_end, 'meeting_url', i.meeting_url,
    'calendar_event_id', i.calendar_event_id, 'invited_at', i.invited_at, 'confirmed_at', i.confirmed_at,
    'has_feedback', EXISTS (SELECT 1 FROM hiring.interview_feedback f WHERE f.interview_id = i.id),
    'interviewer', jsonb_build_object('id', s.id, 'full_name', s.full_name, 'email', s.email),
    'application', api.application_snapshot(i.application_id))
  FROM hiring.interviews i
  JOIN hiring.staff_members s ON s.id = i.interviewer_id
  WHERE i.id = p_interview_id
$$;

CREATE FUNCTION api.confirm_interview_slot(p_interview_id uuid, p_slot_id uuid, p_ctx jsonb)
RETURNS TABLE (interview_id uuid, status text, scheduled_start timestamptz, scheduled_end timestamptz,
               meeting_url text, changed boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx  jsonb := ops.require_ctx(p_ctx);
  v_int  hiring.interviews%ROWTYPE;
  v_app  hiring.applications%ROWTYPE;
  v_slot hiring.interview_slots%ROWTYPE;
BEGIN
  SELECT * INTO v_int FROM hiring.interviews i WHERE i.id = p_interview_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'INTERVIEW_NOT_FOUND', format('interview %s does not exist', p_interview_id));
  END IF;
  SELECT * INTO v_app FROM hiring.applications a WHERE a.id = v_int.application_id;
  PERFORM hiring.assert_candidate_actor(v_ctx, v_app.candidate_id);
  v_ctx := v_ctx || jsonb_build_object('correlation_id', v_app.correlation_id);

  IF v_int.status = 'CONFIRMED' AND v_int.slot_id = p_slot_id THEN
    RETURN QUERY SELECT v_int.id, v_int.status, v_int.scheduled_start, v_int.scheduled_end, v_int.meeting_url, false;
    RETURN;
  END IF;
  IF v_int.status <> 'INVITED' THEN
    PERFORM ops.fail('NT409', 'INTERVIEW_NOT_OPEN', format('interview %s is %s', v_int.interview_code, v_int.status));
  END IF;

  SELECT * INTO v_slot FROM hiring.interview_slots s WHERE s.id = p_slot_id FOR UPDATE;
  IF NOT FOUND OR v_slot.interviewer_id <> v_int.interviewer_id OR v_slot.status <> 'OPEN'
     OR v_slot.starts_at <= now() + hiring.setting_interval('interview.slot_min_notice') THEN
    PERFORM ops.fail('NT409', 'SLOT_UNAVAILABLE', 'this slot is no longer available; please choose another one');
  END IF;

  UPDATE hiring.interview_slots s SET status = 'BOOKED' WHERE s.id = v_slot.id;
  UPDATE hiring.interviews i
     SET status = 'CONFIRMED', slot_id = v_slot.id, scheduled_start = v_slot.starts_at, scheduled_end = v_slot.ends_at,
         confirmed_at = now(), meeting_url = 'https://meet.novatech.example/' || lower(v_int.interview_code)
   WHERE i.id = v_int.id
  RETURNING * INTO v_int;

  PERFORM ops.cancel_entity_actions('INTERVIEW', v_int.id, ARRAY['INTERVIEW_INVITE_REMINDER', 'INTERVIEW_INVITE_EXPIRY'],
                                    'interview confirmed');
  PERFORM 1 FROM hiring.transition_application(v_app.id, 'INTERVIEW_SCHEDULED',
    format('interview %s confirmed for %s', v_int.interview_code, v_int.scheduled_start), v_ctx, 'SHORTLISTED',
    'confirm_interview_slot', jsonb_build_object('interview_id', v_int.id));

  PERFORM ops.enqueue_action('FEEDBACK_REMINDER', 'INTERVIEW', v_int.id, v_app.id,
    v_int.scheduled_end + hiring.setting_interval('interview.feedback_reminder_after'),
    'FEEDBACK_REMINDER:' || v_int.id, jsonb_build_object('interview_id', v_int.id), v_app.correlation_id);
  PERFORM ops.enqueue_action('FEEDBACK_ESCALATION', 'INTERVIEW', v_int.id, v_app.id,
    v_int.scheduled_end + hiring.setting_interval('interview.feedback_escalate_after'),
    'FEEDBACK_ESCALATION:' || v_int.id, jsonb_build_object('interview_id', v_int.id), v_app.correlation_id);

  PERFORM ops.write_log(v_ctx, 'INTERVIEW', v_int.id::text, 'INTERVIEW_CONFIRMED', 'SUCCESS',
    jsonb_build_object('slot_id', v_slot.id, 'starts_at', v_slot.starts_at));
  RETURN QUERY SELECT v_int.id, v_int.status, v_int.scheduled_start, v_int.scheduled_end, v_int.meeting_url, true;
END;
$$;

CREATE FUNCTION api.record_calendar_event(p_interview_id uuid, p_calendar_event_id text, p_ctx jsonb)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_ctx jsonb := ops.require_ctx(p_ctx);
  v_id  text;
BEGIN
  UPDATE hiring.interviews i SET calendar_event_id = coalesce(i.calendar_event_id, p_calendar_event_id)
   WHERE i.id = p_interview_id
  RETURNING i.calendar_event_id INTO v_id;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'INTERVIEW_NOT_FOUND', format('interview %s does not exist', p_interview_id));
  END IF;
  PERFORM ops.write_log(v_ctx, 'INTERVIEW', p_interview_id::text, 'CALENDAR_EVENT_RECORDED', 'SUCCESS',
    jsonb_build_object('calendar_event_id', v_id));
  RETURN v_id;
END;
$$;

CREATE FUNCTION api.expire_interview_invitation(p_interview_id uuid, p_ctx jsonb)
RETURNS TABLE (interview_id uuid, status text, changed boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx jsonb := ops.require_ctx(p_ctx);
  v_int hiring.interviews%ROWTYPE;
  v_app hiring.applications%ROWTYPE;
BEGIN
  SELECT * INTO v_int FROM hiring.interviews i WHERE i.id = p_interview_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'INTERVIEW_NOT_FOUND', format('interview %s does not exist', p_interview_id));
  END IF;
  IF v_int.status <> 'INVITED' THEN
    RETURN QUERY SELECT v_int.id, v_int.status, false;
    RETURN;
  END IF;
  SELECT * INTO v_app FROM hiring.applications a WHERE a.id = v_int.application_id FOR UPDATE;
  v_ctx := v_ctx || jsonb_build_object('correlation_id', v_app.correlation_id);

  UPDATE hiring.interviews i SET status = 'EXPIRED', cancelled_at = now(), cancel_reason = 'invitation not confirmed in time'
   WHERE i.id = v_int.id;
  PERFORM ops.cancel_entity_actions('INTERVIEW', v_int.id, NULL, 'invitation expired');
  IF v_app.status = 'SHORTLISTED' THEN
    PERFORM 1 FROM hiring.transition_application(v_app.id, 'SCREENING_REVIEW',
      format('interview invitation %s was not confirmed in time', v_int.interview_code), v_ctx, 'SHORTLISTED',
      'expire_interview_invitation');
  END IF;
  PERFORM ops.write_log(v_ctx, 'INTERVIEW', v_int.id::text, 'INTERVIEW_INVITATION_EXPIRED', 'SUCCESS');
  RETURN QUERY SELECT v_int.id, 'EXPIRED'::text, true;
END;
$$;

CREATE FUNCTION api.cancel_interview(p_interview_id uuid, p_reason text, p_ctx jsonb)
RETURNS TABLE (interview_id uuid, status text, changed boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx jsonb := ops.require_ctx(p_ctx);
  v_int hiring.interviews%ROWTYPE;
  v_app hiring.applications%ROWTYPE;
BEGIN
  IF nullif(btrim(p_reason), '') IS NULL THEN
    PERFORM ops.fail('NT400', 'REASON_REQUIRED', 'a cancellation reason is required');
  END IF;
  SELECT * INTO v_int FROM hiring.interviews i WHERE i.id = p_interview_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'INTERVIEW_NOT_FOUND', format('interview %s does not exist', p_interview_id));
  END IF;
  SELECT * INTO v_app FROM hiring.applications a WHERE a.id = v_int.application_id FOR UPDATE;
  PERFORM hiring.assert_candidate_actor(v_ctx, v_app.candidate_id);
  v_ctx := v_ctx || jsonb_build_object('correlation_id', v_app.correlation_id);
  IF v_int.status = 'CANCELLED' THEN
    RETURN QUERY SELECT v_int.id, v_int.status, false;
    RETURN;
  END IF;
  IF v_int.status <> 'CONFIRMED' THEN
    PERFORM ops.fail('NT409', 'INTERVIEW_NOT_CANCELLABLE', format('interview %s is %s', v_int.interview_code, v_int.status));
  END IF;

  UPDATE hiring.interviews i SET status = 'CANCELLED', cancelled_at = now(), cancel_reason = p_reason WHERE i.id = v_int.id;
  UPDATE hiring.interview_slots s SET status = 'OPEN' WHERE s.id = v_int.slot_id AND s.status = 'BOOKED';
  PERFORM ops.cancel_entity_actions('INTERVIEW', v_int.id, NULL, 'interview cancelled');
  PERFORM 1 FROM hiring.transition_application(v_app.id, 'SHORTLISTED',
    format('interview %s cancelled: %s', v_int.interview_code, p_reason), v_ctx, 'INTERVIEW_SCHEDULED', 'cancel_interview');
  PERFORM ops.write_log(v_ctx, 'INTERVIEW', v_int.id::text, 'INTERVIEW_CANCELLED', 'SUCCESS', jsonb_build_object('reason', p_reason));
  RETURN QUERY SELECT v_int.id, 'CANCELLED'::text, true;
END;
$$;

CREATE FUNCTION api.mark_interview_no_show(p_interview_id uuid, p_ctx jsonb)
RETURNS TABLE (interview_id uuid, status text, changed boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx jsonb := ops.require_ctx(p_ctx);
  v_int hiring.interviews%ROWTYPE;
  v_app hiring.applications%ROWTYPE;
BEGIN
  SELECT * INTO v_int FROM hiring.interviews i WHERE i.id = p_interview_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'INTERVIEW_NOT_FOUND', format('interview %s does not exist', p_interview_id));
  END IF;
  IF v_int.status = 'NO_SHOW' THEN
    RETURN QUERY SELECT v_int.id, v_int.status, false;
    RETURN;
  END IF;
  IF v_int.status <> 'CONFIRMED' OR v_int.scheduled_end > now() THEN
    PERFORM ops.fail('NT409', 'NO_SHOW_NOT_APPLICABLE', 'only a confirmed interview that has ended can be a no-show');
  END IF;
  SELECT * INTO v_app FROM hiring.applications a WHERE a.id = v_int.application_id FOR UPDATE;
  v_ctx := v_ctx || jsonb_build_object('correlation_id', v_app.correlation_id);

  UPDATE hiring.interviews i SET status = 'NO_SHOW' WHERE i.id = v_int.id;
  PERFORM ops.cancel_entity_actions('INTERVIEW', v_int.id, NULL, 'candidate did not attend');
  PERFORM 1 FROM hiring.transition_application(v_app.id, 'INTERVIEW_REVIEW',
    format('candidate did not attend interview %s', v_int.interview_code), v_ctx, 'INTERVIEW_SCHEDULED',
    'mark_interview_no_show');
  RETURN QUERY SELECT v_int.id, 'NO_SHOW'::text, true;
END;
$$;

CREATE FUNCTION api.submit_interview_feedback(p_interview_id uuid, p_feedback jsonb, p_ctx jsonb)
RETURNS TABLE (feedback_id uuid, interview_score numeric, recommendation text, replayed boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx jsonb := ops.require_ctx(p_ctx);
  v_int hiring.interviews%ROWTYPE;
  v_app hiring.applications%ROWTYPE;
  v_fb  hiring.interview_feedback%ROWTYPE;
BEGIN
  SELECT * INTO v_int FROM hiring.interviews i WHERE i.id = p_interview_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'INTERVIEW_NOT_FOUND', format('interview %s does not exist', p_interview_id));
  END IF;

  SELECT * INTO v_fb FROM hiring.interview_feedback f WHERE f.interview_id = v_int.id;
  IF FOUND THEN
    RETURN QUERY SELECT v_fb.id, v_fb.interview_score, v_fb.recommendation, true;
    RETURN;
  END IF;
  IF v_int.status <> 'CONFIRMED' THEN
    PERFORM ops.fail('NT409', 'INTERVIEW_NOT_CONFIRMED', format('interview %s is %s', v_int.interview_code, v_int.status));
  END IF;
  IF v_ctx->>'actor_type' = 'STAFF' AND v_ctx->>'actor_id' <> v_int.interviewer_id::text
     AND NOT EXISTS (SELECT 1 FROM hiring.staff_members s WHERE s.id::text = v_ctx->>'actor_id' AND 'HR_ADMIN' = ANY (s.roles)) THEN
    PERFORM ops.fail('NT403', 'NOT_ASSIGNED_INTERVIEWER', 'only the assigned interviewer (or an HR admin) can submit feedback');
  END IF;

  SELECT * INTO v_app FROM hiring.applications a WHERE a.id = v_int.application_id FOR UPDATE;
  v_ctx := v_ctx || jsonb_build_object('correlation_id', v_app.correlation_id);

  INSERT INTO hiring.interview_feedback (interview_id, interviewer_id, technical_skills, communication, problem_solving,
                                         experience, team_fit, recommendation, comments, correlation_id)
  VALUES (v_int.id, v_int.interviewer_id, (p_feedback->>'technical_skills')::smallint, (p_feedback->>'communication')::smallint,
          (p_feedback->>'problem_solving')::smallint, (p_feedback->>'experience')::smallint, (p_feedback->>'team_fit')::smallint,
          upper(p_feedback->>'recommendation'), p_feedback->>'comments', v_app.correlation_id)
  RETURNING * INTO v_fb;

  UPDATE hiring.interviews i SET status = 'COMPLETED', completed_at = now() WHERE i.id = v_int.id;
  UPDATE hiring.applications a SET interview_score = v_fb.interview_score WHERE a.id = v_app.id;
  PERFORM ops.cancel_entity_actions('INTERVIEW', v_int.id, ARRAY['FEEDBACK_REMINDER', 'FEEDBACK_ESCALATION'],
                                    'feedback submitted');
  PERFORM 1 FROM hiring.transition_application(v_app.id, 'INTERVIEWED',
    format('feedback submitted for %s (score %s, %s)', v_int.interview_code, v_fb.interview_score, v_fb.recommendation),
    v_ctx, 'INTERVIEW_SCHEDULED', 'submit_interview_feedback', jsonb_build_object('interview_id', v_int.id));
  PERFORM ops.write_log(v_ctx, 'INTERVIEW', v_int.id::text, 'FEEDBACK_SUBMITTED', 'SUCCESS',
    jsonb_build_object('interview_score', v_fb.interview_score, 'recommendation', v_fb.recommendation));
  RETURN QUERY SELECT v_fb.id, v_fb.interview_score, v_fb.recommendation, false;
END;
$$;

CREATE FUNCTION api.apply_interview_decision(p_application_id uuid, p_decision jsonb, p_ctx jsonb)
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
  IF v_decision NOT IN ('SELECTED', 'REJECTED', 'INTERVIEW_REVIEW') THEN
    PERFORM ops.fail('NT400', 'INVALID_DECISION', 'decision must be SELECTED, REJECTED or INTERVIEW_REVIEW');
  END IF;
  IF nullif(btrim(p_decision->>'reason'), '') IS NULL THEN
    PERFORM ops.fail('NT400', 'REASON_REQUIRED', 'every interview decision needs a reason');
  END IF;
  SELECT a.status INTO v_status FROM hiring.applications a WHERE a.id = p_application_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'APPLICATION_NOT_FOUND', format('application %s does not exist', p_application_id));
  END IF;
  IF v_status = v_decision THEN
    RETURN QUERY SELECT p_application_id, v_status, v_status, false;
    RETURN;
  END IF;
  IF coalesce(p_decision->>'final_score', '') ~ '^[0-9]+(\.[0-9]+)?$' THEN
    UPDATE hiring.applications a SET final_score = (p_decision->>'final_score')::numeric WHERE a.id = p_application_id;
  END IF;
  RETURN QUERY
    SELECT t.application_id, t.from_status, t.to_status, t.changed
      FROM hiring.transition_application(p_application_id, v_decision, p_decision->>'reason', v_ctx, 'INTERVIEWED',
                                         'apply_interview_decision', p_decision) AS t;
END;
$$;

-- =============================================================================================
-- Offers
-- =============================================================================================
CREATE FUNCTION api.create_offer(p_application_id uuid, p_offer jsonb, p_ctx jsonb)
RETURNS TABLE (offer_id uuid, offer_code text, revision smallint, status text, monthly_salary bigint, currency text,
               required_approval_levels smallint, created boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx       jsonb := ops.require_ctx(p_ctx);
  v_offer_in  jsonb := coalesce(p_offer, '{}'::jsonb);
  v_app       hiring.applications%ROWTYPE;
  v_pos       hiring.job_positions%ROWTYPE;
  v_open      hiring.offers%ROWTYPE;
  v_last      hiring.offers%ROWTYPE;
  v_offer     hiring.offers%ROWTYPE;
  v_salary    bigint;
  v_joining   date;
  v_manager   uuid;
  v_threshold bigint;
  v_today     date := (now() AT TIME ZONE hiring.company_timezone())::date;
BEGIN
  SELECT * INTO v_app FROM hiring.applications a WHERE a.id = p_application_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'APPLICATION_NOT_FOUND', format('application %s does not exist', p_application_id));
  END IF;
  v_ctx := v_ctx || jsonb_build_object('correlation_id', v_app.correlation_id);

  SELECT * INTO v_open FROM hiring.offers o
   WHERE o.application_id = v_app.id AND o.status IN ('PENDING_APPROVAL', 'APPROVED', 'SENT', 'NEGOTIATION') FOR UPDATE;
  IF v_open.id IS NOT NULL AND v_open.status <> 'NEGOTIATION' THEN
    RETURN QUERY SELECT v_open.id, v_open.offer_code, v_open.revision, v_open.status, v_open.monthly_salary,
                        v_open.currency::text, v_open.required_approval_levels, false;
    RETURN;
  END IF;
  IF v_app.status NOT IN ('SELECTED', 'NEGOTIATION') THEN
    PERFORM ops.fail('NT409', 'STALE_STATE', format('application %s is %s; offers require SELECTED or NEGOTIATION',
                                                   v_app.application_code, v_app.status));
  END IF;

  -- After an approver rejection the system never re-drafts automatically: HR must revise the terms.
  SELECT * INTO v_last FROM hiring.offers o WHERE o.application_id = v_app.id ORDER BY o.revision DESC LIMIT 1;
  IF v_ctx->>'actor_type' = 'SYSTEM' AND v_last.status = 'REJECTED_BY_APPROVER' THEN
    RETURN QUERY SELECT v_last.id, v_last.offer_code, v_last.revision, 'REVISION_REQUIRED'::text, v_last.monthly_salary,
                        v_last.currency::text, v_last.required_approval_levels, false;
    RETURN;
  END IF;

  SELECT * INTO v_pos FROM hiring.job_positions p WHERE p.id = v_app.job_position_id;
  v_salary := coalesce((v_offer_in->>'monthly_salary')::bigint, v_app.expected_salary, v_pos.salary_min, v_pos.salary_max);
  IF v_salary IS NULL THEN
    PERFORM ops.fail('NT422', 'SALARY_REQUIRED', 'no salary given and no expected salary or band to derive one from');
  END IF;
  IF v_offer_in->>'monthly_salary' IS NULL THEN
    v_salary := greatest(v_salary, coalesce(v_pos.salary_min, v_salary));
    v_salary := least(v_salary, coalesce(v_pos.salary_max, v_salary));
  END IF;
  v_joining := coalesce((v_offer_in->>'joining_date')::date,
                        greatest(coalesce(v_app.available_from, v_today),
                                 v_today + hiring.setting_number('offer.joining_lead_days')::integer));
  v_manager := coalesce((v_offer_in->>'reporting_manager_id')::uuid, v_pos.hiring_manager_id);
  IF v_manager IS NULL THEN
    PERFORM ops.fail('NT422', 'MANAGER_REQUIRED', 'no reporting manager given or configured for the position');
  END IF;
  v_threshold := hiring.setting_number('offer.second_approval_threshold')::bigint;

  IF v_open.id IS NOT NULL THEN  -- negotiation: the previous revision is superseded
    UPDATE hiring.offers o SET status = 'SUPERSEDED' WHERE o.id = v_open.id;
  END IF;

  INSERT INTO hiring.offers (offer_code, application_id, revision, job_position_id, department, monthly_salary, currency,
                             joining_date, probation_months, reporting_manager_id, required_approval_levels,
                             approval_threshold_applied, created_by, correlation_id)
  VALUES (ops.next_code('OFF', to_char(now() AT TIME ZONE hiring.company_timezone(), 'YYYY'), 4), v_app.id,
          coalesce(v_last.revision, 0) + 1, v_pos.id, coalesce(v_offer_in->>'department', v_pos.department), v_salary,
          coalesce(upper(v_offer_in->>'currency'), v_pos.currency),
          v_joining, coalesce((v_offer_in->>'probation_months')::smallint,
                              hiring.setting_number('offer.default_probation_months')::smallint),
          v_manager, CASE WHEN v_salary > v_threshold THEN 2 ELSE 1 END, v_threshold, v_ctx->>'actor_id',
          v_app.correlation_id)
  RETURNING * INTO v_offer;

  PERFORM 1 FROM hiring.transition_application(v_app.id, 'OFFER_PENDING_APPROVAL',
    format('offer %s drafted: %s %s/month, %s approval level(s)', v_offer.offer_code, v_offer.monthly_salary,
           v_offer.currency, v_offer.required_approval_levels),
    v_ctx, v_app.status, 'create_offer', jsonb_build_object('offer_id', v_offer.id));
  PERFORM ops.write_log(v_ctx, 'OFFER', v_offer.id::text, 'OFFER_CREATED', 'SUCCESS',
    jsonb_build_object('offer_code', v_offer.offer_code, 'revision', v_offer.revision, 'salary', v_offer.monthly_salary,
                       'required_approval_levels', v_offer.required_approval_levels));
  RETURN QUERY SELECT v_offer.id, v_offer.offer_code, v_offer.revision, v_offer.status, v_offer.monthly_salary,
                      v_offer.currency::text, v_offer.required_approval_levels, true;
END;
$$;

CREATE FUNCTION api.offer_snapshot(p_offer_id uuid)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
  WITH o AS (SELECT * FROM hiring.offers WHERE id = p_offer_id),
  granted AS (SELECT oa.level, oa.approver_id, oa.decision FROM hiring.offer_approvals oa JOIN o ON o.id = oa.offer_id),
  next_level AS (
    SELECT min(lvl) AS lvl FROM o, generate_series(1, o.required_approval_levels) AS lvl
     WHERE NOT EXISTS (SELECT 1 FROM granted g WHERE g.level = lvl))
  SELECT jsonb_build_object(
    'offer_id', o.id, 'offer_code', o.offer_code, 'revision', o.revision, 'status', o.status,
    'monthly_salary', o.monthly_salary, 'currency', o.currency, 'joining_date', o.joining_date,
    'probation_months', o.probation_months, 'department', o.department,
    'required_approval_levels', o.required_approval_levels, 'approval_threshold_applied', o.approval_threshold_applied,
    'sent_at', o.sent_at, 'expires_at', o.expires_at, 'candidate_response', o.candidate_response,
    'candidate_message', o.candidate_message, 'document_storage_key', o.document_storage_key, 'created_by', o.created_by,
    'reporting_manager', (SELECT jsonb_build_object('id', s.id, 'full_name', s.full_name, 'email', s.email, 'job_title', s.job_title)
                            FROM hiring.staff_members s WHERE s.id = o.reporting_manager_id),
    'approvals', coalesce((SELECT jsonb_agg(jsonb_build_object('level', g.level, 'decision', g.decision, 'approver_id', g.approver_id)
                                              ORDER BY g.level) FROM granted g), '[]'::jsonb),
    'next_level', (SELECT lvl FROM next_level),
    'next_approvers', coalesce((
        SELECT jsonb_agg(jsonb_build_object('id', s.id, 'full_name', s.full_name, 'email', s.email) ORDER BY s.full_name)
          FROM hiring.staff_members s, next_level nl
         WHERE nl.lvl IS NOT NULL AND s.is_active AND ('APPROVER_L' || nl.lvl) = ANY (s.roles)
           AND s.id::text IS DISTINCT FROM o.created_by
           AND NOT EXISTS (SELECT 1 FROM granted g WHERE g.approver_id = s.id)), '[]'::jsonb),
    'application', api.application_snapshot(o.application_id))
  FROM o
$$;

CREATE FUNCTION api.decide_offer_approval(p_offer_id uuid, p_level smallint, p_decision text, p_reason text, p_ctx jsonb)
RETURNS TABLE (offer_id uuid, offer_status text, level smallint, decision text, next_level integer, changed boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx      jsonb := ops.require_ctx(p_ctx);
  v_decision text  := upper(coalesce(p_decision, ''));
  v_offer    hiring.offers%ROWTYPE;
  v_app      hiring.applications%ROWTYPE;
  v_existing hiring.offer_approvals%ROWTYPE;
  v_staff    hiring.staff_members%ROWTYPE;
BEGIN
  IF v_ctx->>'actor_type' <> 'STAFF' THEN
    PERFORM ops.fail('NT403', 'APPROVER_MUST_BE_STAFF', 'offer approvals are made by staff members');
  END IF;
  IF v_decision NOT IN ('APPROVED', 'REJECTED') THEN
    PERFORM ops.fail('NT400', 'INVALID_DECISION', 'decision must be APPROVED or REJECTED');
  END IF;
  SELECT * INTO v_offer FROM hiring.offers o WHERE o.id = p_offer_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'OFFER_NOT_FOUND', format('offer %s does not exist', p_offer_id));
  END IF;
  SELECT * INTO v_app FROM hiring.applications a WHERE a.id = v_offer.application_id FOR UPDATE;
  v_ctx := v_ctx || jsonb_build_object('correlation_id', v_app.correlation_id);

  SELECT * INTO v_existing FROM hiring.offer_approvals oa WHERE oa.offer_id = v_offer.id AND oa.level = p_level;
  IF FOUND THEN
    IF v_existing.approver_id::text = v_ctx->>'actor_id' AND v_existing.decision = v_decision THEN
      RETURN QUERY SELECT v_offer.id, v_offer.status, p_level, v_decision, NULL::integer, false;
      RETURN;
    END IF;
    PERFORM ops.fail('NT409', 'LEVEL_ALREADY_DECIDED', format('level %s of offer %s was already decided', p_level, v_offer.offer_code));
  END IF;
  IF v_offer.status <> 'PENDING_APPROVAL' THEN
    PERFORM ops.fail('NT409', 'OFFER_NOT_PENDING', format('offer %s is %s', v_offer.offer_code, v_offer.status));
  END IF;
  IF p_level NOT BETWEEN 1 AND v_offer.required_approval_levels THEN
    PERFORM ops.fail('NT400', 'INVALID_LEVEL', format('offer %s needs %s approval level(s)', v_offer.offer_code,
                                                     v_offer.required_approval_levels));
  END IF;
  IF p_level = 2 AND NOT EXISTS (SELECT 1 FROM hiring.offer_approvals oa
                                  WHERE oa.offer_id = v_offer.id AND oa.level = 1 AND oa.decision = 'APPROVED') THEN
    PERFORM ops.fail('NT409', 'LEVEL_1_REQUIRED', 'level 2 can only decide after level 1 approved');
  END IF;

  SELECT * INTO v_staff FROM hiring.staff_members s WHERE s.id = (v_ctx->>'actor_id')::uuid;
  IF NOT (('APPROVER_L' || p_level) = ANY (v_staff.roles)) THEN
    PERFORM ops.fail('NT403', 'NOT_AN_APPROVER', format('%s is not a level %s approver', v_staff.full_name, p_level));
  END IF;
  IF v_offer.created_by = v_staff.id::text THEN
    PERFORM ops.fail('NT403', 'SELF_APPROVAL_FORBIDDEN', 'the person who drafted an offer cannot approve it');
  END IF;

  BEGIN
    INSERT INTO hiring.offer_approvals (offer_id, level, approver_id, decision, reason, correlation_id)
    VALUES (v_offer.id, p_level, v_staff.id, v_decision, nullif(btrim(p_reason), ''), v_app.correlation_id);
  EXCEPTION WHEN unique_violation THEN
    PERFORM ops.fail('NT403', 'SEGREGATION_OF_DUTIES', 'the same person cannot approve more than one level of an offer');
  END;
  PERFORM ops.write_log(v_ctx, 'OFFER', v_offer.id::text, 'OFFER_' || v_decision, 'SUCCESS',
    jsonb_build_object('level', p_level, 'approver', v_staff.full_name, 'reason', p_reason));

  IF v_decision = 'REJECTED' THEN
    UPDATE hiring.offers o SET status = 'REJECTED_BY_APPROVER' WHERE o.id = v_offer.id;
    PERFORM 1 FROM hiring.transition_application(v_app.id, 'SELECTED',
      format('offer %s rejected at level %s by %s: %s', v_offer.offer_code, p_level, v_staff.full_name, p_reason),
      v_ctx, 'OFFER_PENDING_APPROVAL', 'decide_offer_approval', jsonb_build_object('offer_id', v_offer.id));
    RETURN QUERY SELECT v_offer.id, 'REJECTED_BY_APPROVER'::text, p_level, v_decision, NULL::integer, true;
    RETURN;
  END IF;

  IF p_level >= v_offer.required_approval_levels THEN
    UPDATE hiring.offers o SET status = 'APPROVED' WHERE o.id = v_offer.id;
    PERFORM ops.enqueue_action('SEND_OFFER', 'OFFER', v_offer.id, v_app.id, now(), 'SEND_OFFER:' || v_offer.id,
                               jsonb_build_object('offer_id', v_offer.id), v_app.correlation_id);
    RETURN QUERY SELECT v_offer.id, 'APPROVED'::text, p_level, v_decision, NULL::integer, true;
    RETURN;
  END IF;

  PERFORM ops.enqueue_action('REQUEST_OFFER_APPROVAL', 'OFFER', v_offer.id, v_app.id, now(),
                             'REQUEST_OFFER_APPROVAL:L' || (p_level + 1) || ':' || v_offer.id,
                             jsonb_build_object('offer_id', v_offer.id, 'level', p_level + 1), v_app.correlation_id);
  RETURN QUERY SELECT v_offer.id, v_offer.status, p_level, v_decision, (p_level + 1)::integer, true;
END;
$$;

CREATE FUNCTION api.mark_offer_sent(p_offer_id uuid, p_document_key text, p_ctx jsonb)
RETURNS TABLE (offer_id uuid, status text, sent_at timestamptz, expires_at timestamptz, changed boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx   jsonb := ops.require_ctx(p_ctx);
  v_offer hiring.offers%ROWTYPE;
  v_app   hiring.applications%ROWTYPE;
BEGIN
  SELECT * INTO v_offer FROM hiring.offers o WHERE o.id = p_offer_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'OFFER_NOT_FOUND', format('offer %s does not exist', p_offer_id));
  END IF;
  IF v_offer.status = 'SENT' THEN
    RETURN QUERY SELECT v_offer.id, v_offer.status, v_offer.sent_at, v_offer.expires_at, false;
    RETURN;
  END IF;
  IF v_offer.status <> 'APPROVED' THEN
    PERFORM ops.fail('NT409', 'OFFER_NOT_APPROVED', format('offer %s is %s; only approved offers can be sent',
                                                          v_offer.offer_code, v_offer.status));
  END IF;
  SELECT * INTO v_app FROM hiring.applications a WHERE a.id = v_offer.application_id FOR UPDATE;
  v_ctx := v_ctx || jsonb_build_object('correlation_id', v_app.correlation_id);

  UPDATE hiring.offers o
     SET status = 'SENT', sent_at = now(), expires_at = now() + hiring.setting_interval('offer.validity'),
         document_storage_key = coalesce(nullif(p_document_key, ''), o.document_storage_key)
   WHERE o.id = v_offer.id
  RETURNING * INTO v_offer;

  PERFORM 1 FROM hiring.transition_application(v_app.id, 'OFFERED',
    format('offer %s sent, valid until %s', v_offer.offer_code, v_offer.expires_at), v_ctx, 'OFFER_PENDING_APPROVAL',
    'mark_offer_sent', jsonb_build_object('offer_id', v_offer.id));
  PERFORM ops.enqueue_action('OFFER_REMINDER', 'OFFER', v_offer.id, v_app.id,
    v_offer.sent_at + hiring.setting_interval('offer.first_reminder_after'), 'OFFER_REMINDER:' || v_offer.id,
    jsonb_build_object('offer_id', v_offer.id, 'reminder', 1), v_app.correlation_id);
  PERFORM ops.enqueue_action('OFFER_FINAL_REMINDER', 'OFFER', v_offer.id, v_app.id,
    v_offer.sent_at + hiring.setting_interval('offer.final_reminder_after'), 'OFFER_FINAL_REMINDER:' || v_offer.id,
    jsonb_build_object('offer_id', v_offer.id, 'reminder', 2), v_app.correlation_id);
  PERFORM ops.enqueue_action('OFFER_EXPIRY', 'OFFER', v_offer.id, v_app.id, v_offer.expires_at,
    'OFFER_EXPIRY:' || v_offer.id, jsonb_build_object('offer_id', v_offer.id), v_app.correlation_id);
  PERFORM ops.write_log(v_ctx, 'OFFER', v_offer.id::text, 'OFFER_SENT', 'SUCCESS',
    jsonb_build_object('expires_at', v_offer.expires_at, 'document', v_offer.document_storage_key));
  RETURN QUERY SELECT v_offer.id, v_offer.status, v_offer.sent_at, v_offer.expires_at, true;
END;
$$;

CREATE FUNCTION api.respond_to_offer(p_offer_id uuid, p_response text, p_message text, p_ctx jsonb)
RETURNS TABLE (offer_id uuid, status text, application_status text, changed boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx      jsonb := ops.require_ctx(p_ctx);
  v_response text  := upper(coalesce(p_response, ''));
  v_offer    hiring.offers%ROWTYPE;
  v_app      hiring.applications%ROWTYPE;
  v_status   text;
BEGIN
  IF v_response NOT IN ('ACCEPT', 'DECLINE', 'NEGOTIATE') THEN
    PERFORM ops.fail('NT400', 'INVALID_RESPONSE', 'response must be ACCEPT, DECLINE or NEGOTIATE');
  END IF;
  SELECT * INTO v_offer FROM hiring.offers o WHERE o.id = p_offer_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'OFFER_NOT_FOUND', format('offer %s does not exist', p_offer_id));
  END IF;
  SELECT * INTO v_app FROM hiring.applications a WHERE a.id = v_offer.application_id FOR UPDATE;
  PERFORM hiring.assert_candidate_actor(v_ctx, v_app.candidate_id);
  v_ctx := v_ctx || jsonb_build_object('correlation_id', v_app.correlation_id);

  IF v_offer.candidate_response = v_response THEN
    RETURN QUERY SELECT v_offer.id, v_offer.status, v_app.status, false;
    RETURN;
  END IF;
  IF v_offer.status <> 'SENT' THEN
    PERFORM ops.fail('NT409', 'OFFER_NOT_OPEN', format('offer %s is %s', v_offer.offer_code, v_offer.status));
  END IF;
  IF v_offer.expires_at <= now() THEN
    PERFORM ops.fail('NT410', 'OFFER_EXPIRED', format('offer %s expired at %s', v_offer.offer_code, v_offer.expires_at));
  END IF;

  v_status := CASE v_response WHEN 'ACCEPT' THEN 'ACCEPTED' WHEN 'DECLINE' THEN 'DECLINED' ELSE 'NEGOTIATION' END;
  UPDATE hiring.offers o
     SET status = v_status, responded_at = now(), candidate_response = v_response,
         candidate_message = nullif(btrim(p_message), '')
   WHERE o.id = v_offer.id;
  PERFORM ops.cancel_entity_actions('OFFER', v_offer.id, ARRAY['OFFER_REMINDER', 'OFFER_FINAL_REMINDER', 'OFFER_EXPIRY'],
                                    'candidate responded: ' || v_response);
  PERFORM 1 FROM hiring.transition_application(v_app.id, v_status,
    format('candidate responded %s to offer %s', v_response, v_offer.offer_code), v_ctx, 'OFFERED', 'respond_to_offer',
    jsonb_build_object('offer_id', v_offer.id, 'message', p_message));
  PERFORM ops.write_log(v_ctx, 'OFFER', v_offer.id::text, 'OFFER_RESPONSE_' || v_response, 'SUCCESS',
    jsonb_build_object('message', p_message));
  RETURN QUERY SELECT v_offer.id, v_status, v_status, true;
END;
$$;

CREATE FUNCTION api.expire_offer(p_offer_id uuid, p_ctx jsonb)
RETURNS TABLE (offer_id uuid, status text, changed boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx   jsonb := ops.require_ctx(p_ctx);
  v_offer hiring.offers%ROWTYPE;
  v_app   hiring.applications%ROWTYPE;
BEGIN
  SELECT * INTO v_offer FROM hiring.offers o WHERE o.id = p_offer_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'OFFER_NOT_FOUND', format('offer %s does not exist', p_offer_id));
  END IF;
  IF v_offer.status <> 'SENT' OR v_offer.expires_at > now() THEN
    RETURN QUERY SELECT v_offer.id, v_offer.status, false;
    RETURN;
  END IF;
  SELECT * INTO v_app FROM hiring.applications a WHERE a.id = v_offer.application_id FOR UPDATE;
  v_ctx := v_ctx || jsonb_build_object('correlation_id', v_app.correlation_id);
  UPDATE hiring.offers o SET status = 'EXPIRED' WHERE o.id = v_offer.id;
  PERFORM 1 FROM hiring.transition_application(v_app.id, 'OFFER_EXPIRED',
    format('offer %s expired without a response', v_offer.offer_code), v_ctx, 'OFFERED', 'expire_offer');
  PERFORM ops.write_log(v_ctx, 'OFFER', v_offer.id::text, 'OFFER_EXPIRED', 'SUCCESS');
  RETURN QUERY SELECT v_offer.id, 'EXPIRED'::text, true;
END;
$$;

CREATE FUNCTION api.close_negotiation(p_offer_id uuid, p_reason text, p_ctx jsonb)
RETURNS TABLE (offer_id uuid, status text, changed boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx   jsonb := ops.require_ctx(p_ctx);
  v_offer hiring.offers%ROWTYPE;
  v_app   hiring.applications%ROWTYPE;
BEGIN
  IF nullif(btrim(p_reason), '') IS NULL THEN
    PERFORM ops.fail('NT400', 'REASON_REQUIRED', 'a reason is required to close a negotiation');
  END IF;
  SELECT * INTO v_offer FROM hiring.offers o WHERE o.id = p_offer_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'OFFER_NOT_FOUND', format('offer %s does not exist', p_offer_id));
  END IF;
  SELECT * INTO v_app FROM hiring.applications a WHERE a.id = v_offer.application_id FOR UPDATE;
  PERFORM hiring.assert_candidate_actor(v_ctx, v_app.candidate_id);
  v_ctx := v_ctx || jsonb_build_object('correlation_id', v_app.correlation_id);
  IF v_offer.status = 'DECLINED' THEN
    RETURN QUERY SELECT v_offer.id, v_offer.status, false;
    RETURN;
  END IF;
  IF v_offer.status <> 'NEGOTIATION' THEN
    PERFORM ops.fail('NT409', 'NOT_NEGOTIATING', format('offer %s is %s', v_offer.offer_code, v_offer.status));
  END IF;
  UPDATE hiring.offers o SET status = 'DECLINED' WHERE o.id = v_offer.id;
  PERFORM 1 FROM hiring.transition_application(v_app.id, 'DECLINED',
    format('negotiation on %s closed: %s', v_offer.offer_code, p_reason), v_ctx, 'NEGOTIATION', 'close_negotiation');
  RETURN QUERY SELECT v_offer.id, 'DECLINED'::text, true;
END;
$$;

-- =============================================================================================
-- Onboarding
-- =============================================================================================
CREATE FUNCTION api.create_employee_from_offer(p_offer_id uuid, p_ctx jsonb)
RETURNS TABLE (employee_id uuid, employee_code text, company_email text, joining_date date, task_count integer,
               created boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx     jsonb := ops.require_ctx(p_ctx);
  v_offer   hiring.offers%ROWTYPE;
  v_app     hiring.applications%ROWTYPE;
  v_cand    hiring.candidates%ROWTYPE;
  v_emp     hiring.employees%ROWTYPE;
  v_local   text;
  v_email   text;
  v_domain  text := hiring.setting_text('company.email_domain');
  v_n       integer := 1;
  v_tasks   integer;
BEGIN
  SELECT * INTO v_offer FROM hiring.offers o WHERE o.id = p_offer_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'OFFER_NOT_FOUND', format('offer %s does not exist', p_offer_id));
  END IF;
  SELECT * INTO v_emp FROM hiring.employees e WHERE e.offer_id = v_offer.id;
  IF FOUND THEN  -- exactly once: a repeated trigger returns the existing employee
    RETURN QUERY SELECT v_emp.id, v_emp.employee_code, v_emp.company_email, v_emp.joining_date,
                        (SELECT count(*)::integer FROM hiring.onboarding_tasks t WHERE t.employee_id = v_emp.id), false;
    RETURN;
  END IF;
  IF v_offer.status <> 'ACCEPTED' THEN
    PERFORM ops.fail('NT409', 'OFFER_NOT_ACCEPTED', format('offer %s is %s', v_offer.offer_code, v_offer.status));
  END IF;
  SELECT * INTO v_app FROM hiring.applications a WHERE a.id = v_offer.application_id FOR UPDATE;
  IF v_app.status <> 'ACCEPTED' THEN
    PERFORM ops.fail('NT409', 'STALE_STATE', format('application %s is %s', v_app.application_code, v_app.status));
  END IF;
  SELECT * INTO v_cand FROM hiring.candidates c WHERE c.id = v_app.candidate_id;
  v_ctx := v_ctx || jsonb_build_object('correlation_id', v_app.correlation_id);

  v_local := regexp_replace(lower(split_part(btrim(v_cand.full_name), ' ', 1) || '.' ||
                                  reverse(split_part(reverse(btrim(v_cand.full_name)), ' ', 1))), '[^a-z0-9.]', '', 'g');
  IF v_local IN ('', '.') OR v_local !~ '^[a-z0-9]' THEN
    v_local := 'employee';
  END IF;
  v_email := v_local || '@' || v_domain;
  WHILE EXISTS (SELECT 1 FROM hiring.employees e WHERE e.company_email = v_email) LOOP
    v_n := v_n + 1;
    v_email := v_local || v_n || '@' || v_domain;
  END LOOP;

  INSERT INTO hiring.employees (employee_code, candidate_id, application_id, offer_id, full_name, personal_email, company_email,
                                job_position_id, department, reporting_manager_id, joining_date, probation_end_date,
                                correlation_id)
  VALUES (ops.next_code(hiring.setting_text('company.employee_code_prefix'),
                        to_char(now() AT TIME ZONE hiring.company_timezone(), 'YYYY'), 3),
          v_cand.id, v_app.id, v_offer.id, v_cand.full_name, v_cand.email, v_email, v_offer.job_position_id,
          v_offer.department, v_offer.reporting_manager_id, v_offer.joining_date,
          (v_offer.joining_date + make_interval(months => v_offer.probation_months))::date, v_app.correlation_id)
  RETURNING * INTO v_emp;

  INSERT INTO hiring.onboarding_tasks (employee_id, template_id, task_key, title, owner_role, assignee_id, due_date)
  SELECT v_emp.id, t.id, t.task_key, t.title, t.owner_role,
         CASE t.owner_role WHEN 'HR' THEN hiring.first_staff_with_role('HR_ADMIN')
                           WHEN 'IT' THEN hiring.first_staff_with_role('IT_ADMIN')
                           WHEN 'HIRING_MANAGER' THEN v_emp.reporting_manager_id END,
         v_emp.joining_date + t.due_offset_days
    FROM hiring.onboarding_task_templates t
   WHERE t.is_active AND (t.department IS NULL OR t.department = v_emp.department)
  ON CONFLICT ON CONSTRAINT onboarding_tasks_employee_task_uq DO NOTHING;
  GET DIAGNOSTICS v_tasks = ROW_COUNT;

  PERFORM 1 FROM hiring.transition_application(v_app.id, 'ONBOARDING',
    format('employee %s created; %s onboarding tasks', v_emp.employee_code, v_tasks), v_ctx, 'ACCEPTED',
    'create_employee_from_offer', jsonb_build_object('employee_id', v_emp.id));
  PERFORM ops.write_log(v_ctx, 'EMPLOYEE', v_emp.id::text, 'EMPLOYEE_CREATED', 'SUCCESS',
    jsonb_build_object('employee_code', v_emp.employee_code, 'company_email', v_emp.company_email, 'tasks', v_tasks));
  RETURN QUERY SELECT v_emp.id, v_emp.employee_code, v_emp.company_email, v_emp.joining_date, v_tasks, true;
END;
$$;

CREATE FUNCTION api.employee_snapshot(p_employee_id uuid)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT jsonb_build_object(
    'employee_id', e.id, 'employee_code', e.employee_code, 'full_name', e.full_name, 'personal_email', e.personal_email,
    'company_email', e.company_email, 'department', e.department, 'joining_date', e.joining_date,
    'probation_end_date', e.probation_end_date, 'status', e.status, 'account_provisioned_at', e.account_provisioned_at,
    'position', (SELECT p.title FROM hiring.job_positions p WHERE p.id = e.job_position_id),
    'manager', (SELECT jsonb_build_object('id', s.id, 'full_name', s.full_name, 'email', s.email)
                  FROM hiring.staff_members s WHERE s.id = e.reporting_manager_id),
    'hr_email', (SELECT s.email FROM hiring.staff_members s WHERE s.id = hiring.first_staff_with_role('HR_ADMIN')),
    'it_email', (SELECT s.email FROM hiring.staff_members s WHERE s.id = hiring.first_staff_with_role('IT_ADMIN')),
    'tasks', coalesce((SELECT jsonb_agg(jsonb_build_object('task_id', t.id, 'task_key', t.task_key, 'title', t.title,
                                                           'owner_role', t.owner_role, 'due_date', t.due_date,
                                                           'status', t.status) ORDER BY t.due_date, t.task_key)
                         FROM hiring.onboarding_tasks t WHERE t.employee_id = e.id), '[]'::jsonb),
    'application_id', e.application_id, 'correlation_id', e.correlation_id)
  FROM hiring.employees e
  WHERE e.id = p_employee_id
$$;

CREATE FUNCTION api.complete_onboarding_task(p_task_id uuid, p_ctx jsonb)
RETURNS TABLE (task_id uuid, status text, open_tasks integer, onboarding_complete boolean, changed boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx  jsonb := ops.require_ctx(p_ctx);
  v_task hiring.onboarding_tasks%ROWTYPE;
  v_emp  hiring.employees%ROWTYPE;
  v_open integer;
BEGIN
  SELECT * INTO v_task FROM hiring.onboarding_tasks t WHERE t.id = p_task_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'TASK_NOT_FOUND', format('onboarding task %s does not exist', p_task_id));
  END IF;
  SELECT * INTO v_emp FROM hiring.employees e WHERE e.id = v_task.employee_id FOR UPDATE;
  v_ctx := v_ctx || jsonb_build_object('correlation_id', v_emp.correlation_id);
  IF v_task.status = 'DONE' THEN
    SELECT count(*) INTO v_open FROM hiring.onboarding_tasks t
     WHERE t.employee_id = v_emp.id AND t.status IN ('PENDING', 'IN_PROGRESS');
    RETURN QUERY SELECT v_task.id, v_task.status, v_open, v_emp.status = 'ACTIVE', false;
    RETURN;
  END IF;
  IF v_task.status = 'CANCELLED' THEN
    PERFORM ops.fail('NT409', 'TASK_CANCELLED', 'a cancelled task cannot be completed');
  END IF;

  UPDATE hiring.onboarding_tasks t SET status = 'DONE', completed_at = now(), completed_by = v_ctx->>'actor_id'
   WHERE t.id = v_task.id;
  PERFORM ops.write_log(v_ctx, 'ONBOARDING_TASK', v_task.id::text, 'ONBOARDING_TASK_COMPLETED', 'SUCCESS',
    jsonb_build_object('task_key', v_task.task_key, 'employee_code', v_emp.employee_code));

  SELECT count(*) INTO v_open FROM hiring.onboarding_tasks t
   WHERE t.employee_id = v_emp.id AND t.status IN ('PENDING', 'IN_PROGRESS');
  IF v_open = 0 THEN
    UPDATE hiring.employees e SET status = 'ACTIVE', onboarded_at = now() WHERE e.id = v_emp.id;
    PERFORM 1 FROM hiring.transition_application(v_emp.application_id, 'ONBOARDED',
      format('all onboarding tasks completed for %s', v_emp.employee_code), v_ctx, 'ONBOARDING',
      'complete_onboarding_task', jsonb_build_object('employee_id', v_emp.id));
  END IF;
  RETURN QUERY SELECT v_task.id, 'DONE'::text, v_open, v_open = 0, true;
END;
$$;

CREATE FUNCTION api.mark_account_provisioned(p_employee_id uuid, p_ctx jsonb)
RETURNS timestamptz
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_ctx  jsonb := ops.require_ctx(p_ctx);
  v_at   timestamptz;
  v_task uuid;
BEGIN
  UPDATE hiring.employees e SET account_provisioned_at = coalesce(e.account_provisioned_at, now())
   WHERE e.id = p_employee_id
  RETURNING e.account_provisioned_at INTO v_at;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'EMPLOYEE_NOT_FOUND', format('employee %s does not exist', p_employee_id));
  END IF;
  SELECT t.id INTO v_task FROM hiring.onboarding_tasks t
   WHERE t.employee_id = p_employee_id AND t.task_key = 'create_accounts' AND t.status IN ('PENDING', 'IN_PROGRESS');
  IF v_task IS NOT NULL THEN
    PERFORM 1 FROM api.complete_onboarding_task(v_task, p_ctx);
  END IF;
  PERFORM ops.write_log(v_ctx, 'EMPLOYEE', p_employee_id::text, 'ACCOUNT_PROVISIONED', 'SUCCESS',
    jsonb_build_object('simulated', true));
  RETURN v_at;
END;
$$;

CREATE FUNCTION api.claim_overdue_onboarding_tasks(p_ctx jsonb, p_limit integer DEFAULT 50)
RETURNS TABLE (task_id uuid, task_title text, owner_role text, due_date date, days_overdue integer, reminder_count smallint,
               employee_code text, employee_name text, assignee_name text, assignee_email text, application_id uuid,
               correlation_id text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx   jsonb := ops.require_ctx(p_ctx);
  v_today date  := (now() AT TIME ZONE hiring.company_timezone())::date;
  v_gap   interval := hiring.setting_interval('onboarding.overdue_reminder_every');
BEGIN
  RETURN QUERY
  WITH due AS (
    SELECT t.id FROM hiring.onboarding_tasks t
     WHERE t.status IN ('PENDING', 'IN_PROGRESS') AND t.due_date < v_today
       AND (t.last_reminder_at IS NULL OR t.last_reminder_at < now() - v_gap)
     ORDER BY t.due_date
     LIMIT least(greatest(p_limit, 1), 500)
     FOR UPDATE SKIP LOCKED
  ), upd AS (
    UPDATE hiring.onboarding_tasks t SET last_reminder_at = now(), reminder_count = t.reminder_count + 1
      FROM due WHERE t.id = due.id
    RETURNING t.*
  )
  SELECT upd.id, upd.title, upd.owner_role, upd.due_date, (v_today - upd.due_date)::integer, upd.reminder_count,
         e.employee_code, e.full_name, s.full_name, coalesce(s.email, e.company_email), e.application_id, e.correlation_id
    FROM upd
    JOIN hiring.employees e ON e.id = upd.employee_id
    LEFT JOIN hiring.staff_members s ON s.id = upd.assignee_id;
END;
$$;

-- =============================================================================================
-- Withdrawal (candidate drops out / HR cancels)
-- =============================================================================================
CREATE FUNCTION api.withdraw_application(p_application_id uuid, p_reason text, p_ctx jsonb)
RETURNS TABLE (application_id uuid, from_status text, to_status text, changed boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx jsonb := ops.require_ctx(p_ctx);
  v_app hiring.applications%ROWTYPE;
BEGIN
  IF nullif(btrim(p_reason), '') IS NULL THEN
    PERFORM ops.fail('NT400', 'REASON_REQUIRED', 'a withdrawal reason is required');
  END IF;
  SELECT * INTO v_app FROM hiring.applications a WHERE a.id = p_application_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'APPLICATION_NOT_FOUND', format('application %s does not exist', p_application_id));
  END IF;
  PERFORM hiring.assert_candidate_actor(v_ctx, v_app.candidate_id);
  IF v_app.status = 'WITHDRAWN' THEN
    RETURN QUERY SELECT v_app.id, v_app.status, v_app.status, false;
    RETURN;
  END IF;

  UPDATE hiring.interview_slots s SET status = 'OPEN'
   WHERE s.id IN (SELECT i.slot_id FROM hiring.interviews i WHERE i.application_id = v_app.id AND i.status = 'CONFIRMED');
  UPDATE hiring.interviews i SET status = 'CANCELLED', cancelled_at = now(), cancel_reason = 'application withdrawn'
   WHERE i.application_id = v_app.id AND i.status IN ('INVITED', 'CONFIRMED');
  UPDATE hiring.offers o SET status = 'WITHDRAWN'
   WHERE o.application_id = v_app.id AND o.status IN ('PENDING_APPROVAL', 'APPROVED', 'SENT', 'NEGOTIATION');

  RETURN QUERY
    SELECT t.application_id, t.from_status, t.to_status, t.changed
      FROM hiring.transition_application(v_app.id, 'WITHDRAWN', p_reason, v_ctx || jsonb_build_object(
             'correlation_id', v_app.correlation_id), NULL, 'withdraw_application') AS t;
END;
$$;

-- =============================================================================================
-- Operations: at-most-once alerting for new error-queue items
-- =============================================================================================
CREATE FUNCTION api.claim_unalerted_errors(p_ctx jsonb, p_limit integer DEFAULT 20)
RETURNS TABLE (error_id uuid, workflow_name text, node_name text, error_class text, error_code text, error_message text,
               entity_type text, entity_id text, correlation_id text, retry_count integer, occurrence_count integer,
               created_at timestamptz)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx jsonb := ops.require_ctx(p_ctx);
BEGIN
  RETURN QUERY
  WITH due AS (
    SELECT x.id FROM ops.automation_errors x
     WHERE x.status IN ('OPEN', 'REPLAYING') AND x.alerted_at IS NULL
     ORDER BY x.created_at LIMIT least(greatest(p_limit, 1), 200)
     FOR UPDATE SKIP LOCKED
  )
  UPDATE ops.automation_errors x SET alerted_at = now()
    FROM due WHERE x.id = due.id
  RETURNING x.id, x.workflow_name, x.node_name, x.error_class, x.error_code, x.error_message, x.entity_type, x.entity_id,
            x.correlation_id, x.retry_count, x.occurrence_count, x.created_at;
END;
$$;

-- New functions inherit EXECUTE via default privileges; state it explicitly for managed Postgres too.
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA api TO n8n_app, backend_app;

-- migrate:down
DROP FUNCTION IF EXISTS api.claim_unalerted_errors, api.withdraw_application, api.claim_overdue_onboarding_tasks,
  api.mark_account_provisioned, api.complete_onboarding_task, api.employee_snapshot, api.create_employee_from_offer,
  api.close_negotiation, api.expire_offer, api.respond_to_offer, api.mark_offer_sent, api.decide_offer_approval,
  api.offer_snapshot, api.create_offer, api.apply_interview_decision, api.submit_interview_feedback,
  api.mark_interview_no_show, api.cancel_interview, api.expire_interview_invitation, api.record_calendar_event,
  api.confirm_interview_slot, api.interview_snapshot, api.interview_slot_options, api.create_interview_invitation,
  ops.cancel_entity_actions, hiring.first_staff_with_role, hiring.assert_candidate_actor;
DELETE FROM hiring.settings WHERE key IN ('company.portal_url', 'company.careers_email', 'ops.alert_email',
  'interview.slot_min_notice', 'offer.joining_lead_days');
