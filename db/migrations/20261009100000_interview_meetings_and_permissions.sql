-- migrate:up

-- Interview scheduling, meeting details, offer approvers and staff permissions.
--
--   * Response deadline: every invitation has interviews.respond_by (midnight, company timezone). The candidate is
--     only offered slots on days BEFORE that day, and the deadline moves out until the candidate can choose from at
--     least interview.min_choice_days days. The expiry timer runs at the deadline, the reminder a day before it.
--   * Availability: each interviewer's weekly interview hours (hiring.interviewer_availability). Slots for the next
--     interview.availability_days days are generated whenever an invitation is sent, so they never run out.
--   * Meeting details (link, meeting id, passcode, or on-site instructions) are entered by staff when they shortlist
--     (hiring.interview_meeting_plans) and copied to every interview of the application. The candidate only sees them
--     after booking; if they change after booking, the candidate is emailed (SEND_MEETING_DETAILS, WF-04). The
--     placeholder link the booking step used to generate is gone.
--   * Offer approval: level 1 belongs to the APPROVER_L1 staff of the offer's department (or the position's hiring
--     manager / reporting manager); level 2 to any APPROVER_L2. Segregation of duties (not the drafter, not the same
--     person on both levels) holds whenever another approver is available; with a single approver it is waived and
--     the waiver is audited (offer_approvals_approver_uq becomes a rule in decide_offer_approval).
--   * Staff decisions on an application (decisions, withdrawal, offers, interview changes, meeting details) are
--     limited to HR admins, recruiters and the position's hiring manager; interview changes also to the interviewer.

-- =============================================================================================
-- Settings, tables, columns
-- =============================================================================================
INSERT INTO hiring.settings (key, value, value_type, description) VALUES
  ('interview.availability_days', '15', 'number',
   'Interview slots are generated this many days ahead from each interviewer''s weekly availability'),
  ('interview.min_choice_days',   '2',  'number',
   'An invitation''s response deadline moves out until the candidate can choose from at least this many days')
ON CONFLICT (key) DO NOTHING;

-- Weekly interview hours per interviewer, in the company timezone.
CREATE TABLE hiring.interviewer_availability (
  interviewer_id   uuid NOT NULL REFERENCES hiring.staff_members(id),
  isodow           smallint NOT NULL CHECK (isodow BETWEEN 1 AND 7),
  start_time       time NOT NULL,
  duration_minutes smallint NOT NULL DEFAULT 45 CHECK (duration_minutes BETWEEN 15 AND 240),
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (interviewer_id, isodow, start_time)
);

-- The meeting the application's interviews use, entered by staff when they shortlist.
CREATE TABLE hiring.interview_meeting_plans (
  application_id   uuid PRIMARY KEY REFERENCES hiring.applications(id) ON DELETE CASCADE,
  mode             text NOT NULL CHECK (mode IN ('ONLINE','ONSITE')),
  meeting_url      text CHECK (meeting_url ~ '^https?://\S+$' AND char_length(meeting_url) <= 500),
  meeting_id       text CHECK (char_length(meeting_id) <= 100),
  meeting_passcode text CHECK (char_length(meeting_passcode) <= 100),
  meeting_notes    text CHECK (char_length(meeting_notes) <= 1000),
  updated_by       text NOT NULL,
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CHECK (mode <> 'ONLINE' OR meeting_url IS NOT NULL),
  CHECK (mode <> 'ONSITE' OR meeting_notes IS NOT NULL)
);

ALTER TABLE hiring.interviews
  ADD COLUMN respond_by       timestamptz,
  ADD COLUMN meeting_id       text CHECK (char_length(meeting_id) <= 100),
  ADD COLUMN meeting_passcode text CHECK (char_length(meeting_passcode) <= 100),
  ADD COLUMN meeting_notes    text CHECK (char_length(meeting_notes) <= 1000);

GRANT SELECT ON hiring.interviewer_availability, hiring.interview_meeting_plans TO n8n_app, backend_app;

-- =============================================================================================
-- Helpers
-- =============================================================================================
-- Generates the OPEN slots of the next interview.availability_days days from the weekly availability
-- (all interviewers when p_interviewer_id is NULL). Existing and overlapping slots are left alone.
CREATE FUNCTION hiring.ensure_interview_slots(p_interviewer_id uuid)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
  v_tz    text := hiring.company_timezone();
  v_today date := (now() AT TIME ZONE hiring.company_timezone())::date;
  v_count integer;
BEGIN
  INSERT INTO hiring.interview_slots (interviewer_id, starts_at, ends_at)
  SELECT av.interviewer_id,
         (d.day + av.start_time) AT TIME ZONE v_tz,
         (d.day + av.start_time + make_interval(mins => av.duration_minutes)) AT TIME ZONE v_tz
    FROM generate_series(v_today + 1, v_today + hiring.setting_number('interview.availability_days')::integer,
                         interval '1 day') AS g(ts)
   CROSS JOIN LATERAL (SELECT g.ts::date AS day) AS d
    JOIN hiring.interviewer_availability av ON av.isodow = extract(isodow FROM d.day)
    JOIN hiring.staff_members s ON s.id = av.interviewer_id AND s.is_active
   WHERE p_interviewer_id IS NULL OR av.interviewer_id = p_interviewer_id
  ON CONFLICT DO NOTHING;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  RETURN v_count;
END;
$$;

-- The response deadline of an invitation sent at p_from: midnight after the response window
-- (interview.invite_expires_after) ends, moved out until the candidate can choose from at least
-- interview.min_choice_days days with open slots. Slots are offered only before this moment.
CREATE FUNCTION hiring.interview_respond_by(p_interviewer_id uuid, p_from timestamptz)
RETURNS timestamptz
LANGUAGE sql
STABLE
AS $$
  WITH tz AS (SELECT hiring.company_timezone() AS name),
  base AS (
    SELECT ((p_from + hiring.setting_interval('interview.invite_expires_after')) AT TIME ZONE tz.name)::date + 1 AS day
      FROM tz),
  slot_days AS (
    SELECT DISTINCT (s.starts_at AT TIME ZONE tz.name)::date AS day
      FROM hiring.interview_slots s, tz
     WHERE s.interviewer_id = p_interviewer_id AND s.status = 'OPEN'
       AND s.starts_at > greatest(now(), p_from) + hiring.setting_interval('interview.slot_min_notice')),
  wanted AS (
    SELECT max(day) + 1 AS day
      FROM (SELECT day FROM slot_days ORDER BY day
             LIMIT greatest(hiring.setting_number('interview.min_choice_days')::integer, 1)) AS first_days)
  SELECT (greatest(base.day, coalesce(wanted.day, base.day))::timestamp) AT TIME ZONE tz.name
    FROM base, wanted, tz
$$;

-- HR admins, recruiters and the position's hiring manager may make decisions on an application.
CREATE FUNCTION hiring.staff_may_manage(p_staff_id text, p_application_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
  SELECT EXISTS (
    SELECT 1
      FROM hiring.staff_members s
      JOIN hiring.applications a ON a.id = p_application_id
      JOIN hiring.job_positions p ON p.id = a.job_position_id
     WHERE s.id::text = p_staff_id AND s.is_active
       AND (s.roles && ARRAY['HR_ADMIN', 'RECRUITER'] OR s.id = p.hiring_manager_id))
$$;

-- Fails for staff actors who may not change the application (p_interviewer_id: also allowed, e.g. the interviewer).
CREATE FUNCTION hiring.assert_staff_may_manage(p_ctx jsonb, p_application_id uuid, p_interviewer_id uuid DEFAULT NULL)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
  IF p_ctx->>'actor_type' = 'STAFF'
     AND NOT hiring.staff_may_manage(p_ctx->>'actor_id', p_application_id)
     AND p_ctx->>'actor_id' IS DISTINCT FROM p_interviewer_id::text THEN
    PERFORM ops.fail('NT403', 'NOT_ALLOWED_FOR_ROLE',
      'only HR admins, recruiters and the hiring manager of the position'
      || CASE WHEN p_interviewer_id IS NULL THEN '' ELSE ' (or the interviewer)' END || ' can do this');
  END IF;
END;
$$;

-- Who may decide level p_level of an offer now: the active holders of role APPROVER_L<level>, best tier only.
--   Segregation of duties: the person who drafted the offer and anyone who already approved a level of it come last.
--   Scope (level 1): staff of the offer's department, the position's hiring manager and the reporting manager come
--   before approvers of other departments. Level 2 is company-wide.
-- So when another approver is available the rules hold; a company with a single approver (e.g. HR only) can still
-- approve, and decide_offer_approval records that segregation of duties was waived.
CREATE FUNCTION hiring.offer_approver_ids(p_offer_id uuid, p_level integer)
RETURNS SETOF uuid
LANGUAGE sql
STABLE
AS $$
  WITH o AS (
    SELECT o.id, o.department, o.created_by, o.reporting_manager_id, p.hiring_manager_id
      FROM hiring.offers o JOIN hiring.job_positions p ON p.id = o.job_position_id
     WHERE o.id = p_offer_id),
  holders AS (
    SELECT s.id,
           CASE WHEN s.id::text = o.created_by
                  OR EXISTS (SELECT 1 FROM hiring.offer_approvals oa WHERE oa.offer_id = o.id AND oa.approver_id = s.id)
                THEN 2 ELSE 0 END
           + CASE WHEN p_level = 2 OR s.department = o.department OR s.id = o.hiring_manager_id
                       OR s.id = o.reporting_manager_id
                  THEN 0 ELSE 1 END AS tier
      FROM hiring.staff_members s, o
     WHERE s.is_active AND ('APPROVER_L' || p_level) = ANY (s.roles))
  SELECT h.id FROM holders h WHERE h.tier = (SELECT min(x.tier) FROM holders x)
$$;

-- What the signed-in staff member may do on an application (the HR portal shows only those actions;
-- the api functions enforce the same rules).
CREATE FUNCTION api.staff_permissions(p_application_id uuid, p_staff_id text)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT jsonb_build_object(
    'can_manage', hiring.staff_may_manage(p_staff_id, p_application_id),
    'can_manage_interviews', hiring.staff_may_manage(p_staff_id, p_application_id)
       OR EXISTS (SELECT 1 FROM hiring.interviews i
                   WHERE i.application_id = p_application_id AND i.interviewer_id::text = p_staff_id),
    'is_hr_admin', EXISTS (SELECT 1 FROM hiring.staff_members s
                            WHERE s.id::text = p_staff_id AND s.is_active AND 'HR_ADMIN' = ANY (s.roles)))
$$;

-- =============================================================================================
-- Meeting details
-- =============================================================================================
-- Sets the meeting of an application's interviews (before shortlisting, or later). The open interview gets the
-- same details; when the candidate has already booked it, they are emailed the new details (SEND_MEETING_DETAILS).
-- The passcode is never written to the audit log.
CREATE FUNCTION api.set_interview_meeting(p_application_id uuid, p_meeting jsonb, p_ctx jsonb)
RETURNS TABLE (application_id uuid, interview_id uuid, interview_status text, changed boolean,
               candidate_notified boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx     jsonb := ops.require_ctx(p_ctx);
  v_in      jsonb := coalesce(p_meeting, '{}'::jsonb);
  v_mode    text  := upper(coalesce(nullif(btrim(v_in->>'mode'), ''), 'ONLINE'));
  v_url     text  := nullif(btrim(v_in->>'meeting_url'), '');
  v_mid     text  := nullif(btrim(v_in->>'meeting_id'), '');
  v_pass    text  := nullif(btrim(v_in->>'meeting_passcode'), '');
  v_notes   text  := nullif(btrim(v_in->>'meeting_notes'), '');
  v_app     hiring.applications%ROWTYPE;
  v_old     hiring.interview_meeting_plans%ROWTYPE;
  v_int     hiring.interviews%ROWTYPE;
  v_changed boolean;
  v_notify  boolean := false;
BEGIN
  IF v_ctx->>'actor_type' <> 'STAFF' THEN
    PERFORM ops.fail('NT403', 'STAFF_ONLY', 'interview meeting details are set by staff');
  END IF;
  IF v_mode NOT IN ('ONLINE', 'ONSITE') THEN
    PERFORM ops.fail('NT400', 'INVALID_MEETING_MODE', 'mode must be ONLINE or ONSITE');
  END IF;
  IF v_mode = 'ONLINE' AND (v_url IS NULL OR v_url !~ '^https?://\S+$') THEN
    PERFORM ops.fail('NT400', 'MEETING_LINK_REQUIRED', 'an online interview needs a meeting link (https://...)');
  END IF;
  IF v_mode = 'ONSITE' THEN
    IF v_notes IS NULL THEN
      PERFORM ops.fail('NT400', 'MEETING_LOCATION_REQUIRED', 'an on-site interview needs the address or instructions');
    END IF;
    v_url := NULL; v_mid := NULL; v_pass := NULL;
  END IF;
  IF char_length(coalesce(v_url, '')) > 500 OR char_length(coalesce(v_mid, '')) > 100
     OR char_length(coalesce(v_pass, '')) > 100 OR char_length(coalesce(v_notes, '')) > 1000 THEN
    PERFORM ops.fail('NT400', 'MEETING_DETAILS_TOO_LONG', 'meeting details are too long');
  END IF;

  SELECT * INTO v_app FROM hiring.applications a WHERE a.id = p_application_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'APPLICATION_NOT_FOUND', format('application %s does not exist', p_application_id));
  END IF;
  PERFORM hiring.assert_staff_may_manage(v_ctx, v_app.id,
    (SELECT i.interviewer_id FROM hiring.interviews i WHERE i.application_id = v_app.id ORDER BY i.round DESC LIMIT 1));
  IF (SELECT s.is_terminal FROM hiring.application_statuses s WHERE s.code = v_app.status) THEN
    PERFORM ops.fail('NT409', 'APPLICATION_CLOSED', format('application %s is %s', v_app.application_code, v_app.status));
  END IF;
  v_ctx := v_ctx || jsonb_build_object('correlation_id', v_app.correlation_id);

  SELECT * INTO v_old FROM hiring.interview_meeting_plans mp WHERE mp.application_id = v_app.id;
  v_changed := v_old.application_id IS NULL
    OR (v_old.mode, v_old.meeting_url, v_old.meeting_id, v_old.meeting_passcode, v_old.meeting_notes)
       IS DISTINCT FROM (v_mode, v_url, v_mid, v_pass, v_notes);
  IF v_changed THEN
    INSERT INTO hiring.interview_meeting_plans AS mp (application_id, mode, meeting_url, meeting_id, meeting_passcode,
                                                      meeting_notes, updated_by)
    VALUES (v_app.id, v_mode, v_url, v_mid, v_pass, v_notes, v_ctx->>'actor_id')
    ON CONFLICT ON CONSTRAINT interview_meeting_plans_pkey DO UPDATE
      SET mode = EXCLUDED.mode, meeting_url = EXCLUDED.meeting_url, meeting_id = EXCLUDED.meeting_id,
          meeting_passcode = EXCLUDED.meeting_passcode, meeting_notes = EXCLUDED.meeting_notes,
          updated_by = EXCLUDED.updated_by, updated_at = now();
  END IF;

  SELECT * INTO v_int FROM hiring.interviews i
   WHERE i.application_id = v_app.id AND i.status IN ('INVITED', 'CONFIRMED')
   ORDER BY i.round DESC LIMIT 1 FOR UPDATE;
  IF v_int.id IS NOT NULL
     AND (v_int.mode, v_int.meeting_url, v_int.meeting_id, v_int.meeting_passcode, v_int.meeting_notes)
         IS DISTINCT FROM (v_mode, v_url, v_mid, v_pass, v_notes) THEN
    UPDATE hiring.interviews i
       SET mode = v_mode, meeting_url = v_url, meeting_id = v_mid, meeting_passcode = v_pass, meeting_notes = v_notes
     WHERE i.id = v_int.id;
    v_changed := true;
    IF v_int.status = 'CONFIRMED' THEN  -- already booked: the candidate gets the new details by email
      PERFORM ops.enqueue_action('SEND_MEETING_DETAILS', 'INTERVIEW', v_int.id, v_app.id, now(),
        'SEND_MEETING_DETAILS:' || v_int.id || ':' || to_char(clock_timestamp(), 'YYYYMMDDHH24MISSUS'),
        jsonb_build_object('interview_id', v_int.id), v_app.correlation_id);
      v_notify := true;
    END IF;
  END IF;

  IF v_changed THEN
    PERFORM ops.write_log(v_ctx, 'APPLICATION', v_app.id::text, 'MEETING_DETAILS_SET', 'SUCCESS',
      jsonb_build_object('mode', v_mode, 'interview_code', v_int.interview_code, 'candidate_notified', v_notify));
  END IF;
  RETURN QUERY SELECT v_app.id, v_int.id, v_int.status, v_changed, v_notify;
END;
$$;

-- =============================================================================================
-- Interviews
-- =============================================================================================
CREATE OR REPLACE FUNCTION api.create_interview_invitation(p_application_id uuid, p_ctx jsonb)
RETURNS TABLE (interview_id uuid, interview_code text, round smallint, status text, interviewer_id uuid,
               interviewer_name text, interviewer_email text, invite_expires_at timestamptz, created boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx        jsonb := ops.require_ctx(p_ctx);
  v_app        hiring.applications%ROWTYPE;
  v_pos        hiring.job_positions%ROWTYPE;
  v_last       hiring.interviews%ROWTYPE;
  v_int        hiring.interviews%ROWTYPE;
  v_staff      hiring.staff_members%ROWTYPE;
  v_plan       hiring.interview_meeting_plans%ROWTYPE;
  v_respond_by timestamptz;
  v_remind_at  timestamptz;
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
                        v_staff.email,
                        coalesce(v_last.respond_by,
                                 v_last.invited_at + hiring.setting_interval('interview.invite_expires_after')),
                        false;
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

  PERFORM hiring.ensure_interview_slots(v_staff.id);
  v_respond_by := hiring.interview_respond_by(v_staff.id, now());
  SELECT * INTO v_plan FROM hiring.interview_meeting_plans mp WHERE mp.application_id = v_app.id;

  INSERT INTO hiring.interviews (interview_code, application_id, round, interviewer_id, correlation_id, respond_by,
                                 mode, meeting_url, meeting_id, meeting_passcode, meeting_notes)
  VALUES (ops.next_code('INT', to_char(now() AT TIME ZONE hiring.company_timezone(), 'YYYY'), 4), v_app.id,
          coalesce(v_last.round, 0) + 1, v_staff.id, v_app.correlation_id, v_respond_by,
          coalesce(v_plan.mode, 'ONLINE'), v_plan.meeting_url, v_plan.meeting_id, v_plan.meeting_passcode,
          v_plan.meeting_notes)
  RETURNING * INTO v_int;

  -- The reminder follows interview.invite_reminder_after, but never later than a day before the deadline.
  v_remind_at := least(v_int.invited_at + hiring.setting_interval('interview.invite_reminder_after'),
                       v_respond_by - interval '1 day');
  IF v_remind_at > now() THEN
    PERFORM ops.enqueue_action('INTERVIEW_INVITE_REMINDER', 'INTERVIEW', v_int.id, v_app.id, v_remind_at,
      'INTERVIEW_INVITE_REMINDER:' || v_int.id, jsonb_build_object('interview_id', v_int.id), v_app.correlation_id);
  END IF;
  PERFORM ops.enqueue_action('INTERVIEW_INVITE_EXPIRY', 'INTERVIEW', v_int.id, v_app.id, v_respond_by,
    'INTERVIEW_INVITE_EXPIRY:' || v_int.id, jsonb_build_object('interview_id', v_int.id), v_app.correlation_id);

  PERFORM ops.write_log(v_ctx, 'INTERVIEW', v_int.id::text, 'INTERVIEW_INVITED', 'SUCCESS',
    jsonb_build_object('interview_code', v_int.interview_code, 'round', v_int.round, 'interviewer', v_staff.full_name,
                       'expires_at', v_respond_by, 'meeting_details', v_plan.application_id IS NOT NULL));

  RETURN QUERY SELECT v_int.id, v_int.interview_code, v_int.round, v_int.status, v_staff.id, v_staff.full_name,
                      v_staff.email, v_respond_by, true;
END;
$$;

-- Only slots on days before the response deadline are offered.
CREATE OR REPLACE FUNCTION api.interview_slot_options(p_interview_id uuid, p_limit integer DEFAULT 10)
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
     AND (i.respond_by IS NULL OR s.starts_at < i.respond_by)
   ORDER BY s.starts_at
   LIMIT least(greatest(p_limit, 1), 50)
$$;

CREATE OR REPLACE FUNCTION api.interview_snapshot(p_interview_id uuid)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT jsonb_build_object(
    'interview_id', i.id, 'interview_code', i.interview_code, 'round', i.round, 'status', i.status, 'mode', i.mode,
    'scheduled_start', i.scheduled_start, 'scheduled_end', i.scheduled_end, 'meeting_url', i.meeting_url,
    'meeting_id', i.meeting_id, 'meeting_passcode', i.meeting_passcode, 'meeting_notes', i.meeting_notes,
    'respond_by', i.respond_by,
    'calendar_event_id', i.calendar_event_id, 'invited_at', i.invited_at, 'confirmed_at', i.confirmed_at,
    'has_feedback', EXISTS (SELECT 1 FROM hiring.interview_feedback f WHERE f.interview_id = i.id),
    'interviewer', jsonb_build_object('id', s.id, 'full_name', s.full_name, 'email', s.email),
    'application', api.application_snapshot(i.application_id))
  FROM hiring.interviews i
  JOIN hiring.staff_members s ON s.id = i.interviewer_id
  WHERE i.id = p_interview_id
$$;

CREATE OR REPLACE FUNCTION api.confirm_interview_slot(p_interview_id uuid, p_slot_id uuid, p_ctx jsonb)
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
     OR v_slot.starts_at <= now() + hiring.setting_interval('interview.slot_min_notice')
     OR v_slot.starts_at >= coalesce(v_int.respond_by, 'infinity'::timestamptz) THEN
    PERFORM ops.fail('NT409', 'SLOT_UNAVAILABLE', 'this slot is no longer available; please choose another one');
  END IF;

  UPDATE hiring.interview_slots s SET status = 'BOOKED' WHERE s.id = v_slot.id;
  UPDATE hiring.interviews i
     SET status = 'CONFIRMED', slot_id = v_slot.id, scheduled_start = v_slot.starts_at, scheduled_end = v_slot.ends_at,
         confirmed_at = now()
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

-- =============================================================================================
-- Staff decisions: role check, and shortlisting needs the interview meeting
-- =============================================================================================
CREATE OR REPLACE FUNCTION api.transition_application_status(
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
  IF upper(coalesce(p_ctx->>'actor_type', '')) = 'STAFF' THEN
    IF nullif(btrim(p_reason), '') IS NULL THEN
      PERFORM ops.fail('NT400', 'REASON_REQUIRED', 'a reason is required for human decisions');
    END IF;
    PERFORM hiring.assert_staff_may_manage(p_ctx, p_application_id);
    IF upper(btrim(p_to_status)) = 'SHORTLISTED'
       AND NOT EXISTS (SELECT 1 FROM hiring.interview_meeting_plans mp WHERE mp.application_id = p_application_id) THEN
      PERFORM ops.fail('NT422', 'MEETING_DETAILS_REQUIRED',
        'enter the interview meeting details (link, meeting ID and passcode, or on-site instructions) before shortlisting');
    END IF;
  END IF;
  RETURN QUERY
    SELECT t.application_id, t.from_status, t.to_status, t.changed
      FROM hiring.transition_application(p_application_id, upper(btrim(p_to_status)), p_reason, p_ctx,
                                         p_expected_from, NULL, '{}'::jsonb) AS t;
END;
$$;

CREATE OR REPLACE FUNCTION api.withdraw_application(p_application_id uuid, p_reason text, p_ctx jsonb)
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
  PERFORM hiring.assert_staff_may_manage(v_ctx, v_app.id);
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

CREATE OR REPLACE FUNCTION api.close_negotiation(p_offer_id uuid, p_reason text, p_ctx jsonb)
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
  PERFORM hiring.assert_staff_may_manage(v_ctx, v_app.id);
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

CREATE OR REPLACE FUNCTION api.cancel_interview(p_interview_id uuid, p_reason text, p_ctx jsonb)
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
  PERFORM hiring.assert_staff_may_manage(v_ctx, v_app.id, v_int.interviewer_id);
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

CREATE OR REPLACE FUNCTION api.mark_interview_no_show(p_interview_id uuid, p_ctx jsonb)
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
  PERFORM hiring.assert_staff_may_manage(v_ctx, v_app.id, v_int.interviewer_id);
  v_ctx := v_ctx || jsonb_build_object('correlation_id', v_app.correlation_id);

  UPDATE hiring.interviews i SET status = 'NO_SHOW' WHERE i.id = v_int.id;
  PERFORM ops.cancel_entity_actions('INTERVIEW', v_int.id, NULL, 'candidate did not attend');
  PERFORM 1 FROM hiring.transition_application(v_app.id, 'INTERVIEW_REVIEW',
    format('candidate did not attend interview %s', v_int.interview_code), v_ctx, 'INTERVIEW_SCHEDULED',
    'mark_interview_no_show');
  RETURN QUERY SELECT v_int.id, 'NO_SHOW'::text, true;
END;
$$;

CREATE OR REPLACE FUNCTION api.create_offer(p_application_id uuid, p_offer jsonb, p_ctx jsonb)
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
  PERFORM hiring.assert_staff_may_manage(v_ctx, v_app.id);

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

-- =============================================================================================
-- Offers: department-scoped level 1 approval
-- =============================================================================================
-- One person may now approve both levels when nobody else can (see hiring.offer_approver_ids).
ALTER TABLE hiring.offer_approvals DROP CONSTRAINT offer_approvals_approver_uq;

CREATE OR REPLACE FUNCTION api.offer_snapshot(p_offer_id uuid)
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
        SELECT jsonb_agg(jsonb_build_object('id', s.id, 'full_name', s.full_name, 'email', s.email,
                                            'job_title', s.job_title) ORDER BY s.full_name)
          FROM next_level nl
         CROSS JOIN LATERAL hiring.offer_approver_ids(o.id, nl.lvl) AS x(approver_id)
          JOIN hiring.staff_members s ON s.id = x.approver_id
         WHERE nl.lvl IS NOT NULL), '[]'::jsonb),
    'application', api.application_snapshot(o.application_id))
  FROM o
$$;

CREATE OR REPLACE FUNCTION api.decide_offer_approval(p_offer_id uuid, p_level smallint, p_decision text, p_reason text, p_ctx jsonb)
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
  v_waived   boolean;
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
  v_waived := v_offer.created_by = v_staff.id::text
    OR EXISTS (SELECT 1 FROM hiring.offer_approvals oa WHERE oa.offer_id = v_offer.id AND oa.approver_id = v_staff.id);
  IF NOT EXISTS (SELECT 1 FROM hiring.offer_approver_ids(v_offer.id, p_level) AS x(approver_id)
                  WHERE x.approver_id = v_staff.id) THEN
    IF v_offer.created_by = v_staff.id::text THEN
      PERFORM ops.fail('NT403', 'SELF_APPROVAL_FORBIDDEN',
        'the person who drafted an offer cannot approve it while another approver is available');
    ELSIF v_waived THEN
      PERFORM ops.fail('NT403', 'SEGREGATION_OF_DUTIES',
        'the same person cannot approve both levels of an offer while another approver is available');
    END IF;
    PERFORM ops.fail('NT403', 'NOT_THE_DEPARTMENT_APPROVER',
      format('level %s of offer %s is approved by an approver of the %s department', p_level, v_offer.offer_code,
             v_offer.department));
  END IF;

  BEGIN
    INSERT INTO hiring.offer_approvals (offer_id, level, approver_id, decision, reason, correlation_id)
    VALUES (v_offer.id, p_level, v_staff.id, v_decision, nullif(btrim(p_reason), ''), v_app.correlation_id);
  EXCEPTION WHEN unique_violation THEN
    PERFORM ops.fail('NT409', 'LEVEL_ALREADY_DECIDED',
      format('level %s of offer %s was already decided', p_level, v_offer.offer_code));
  END;
  PERFORM ops.write_log(v_ctx, 'OFFER', v_offer.id::text, 'OFFER_' || v_decision, 'SUCCESS',
    jsonb_build_object('level', p_level, 'approver', v_staff.full_name, 'reason', p_reason,
                       'segregation_waived', v_waived));

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

-- =============================================================================================
-- Existing data
-- =============================================================================================
-- The old booking step generated placeholder links that were never real meetings.
UPDATE hiring.interviews i SET meeting_url = NULL WHERE i.meeting_url LIKE 'https://meet.novatech.example/%';

-- Open invitations get a deadline by the new rule, and their expiry timer moves to it.
UPDATE hiring.interviews i SET respond_by = hiring.interview_respond_by(i.interviewer_id, i.invited_at)
 WHERE i.status = 'INVITED';
UPDATE ops.scheduled_actions sa SET run_at = i.respond_by
  FROM hiring.interviews i
 WHERE sa.entity_id = i.id AND sa.action_type = 'INTERVIEW_INVITE_EXPIRY' AND sa.status = 'PENDING'
   AND i.respond_by IS NOT NULL;

-- migrate:down
-- Restore the previous function versions (20261001110000, 20260928100500).

CREATE OR REPLACE FUNCTION api.create_interview_invitation(p_application_id uuid, p_ctx jsonb)
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

CREATE OR REPLACE FUNCTION api.interview_slot_options(p_interview_id uuid, p_limit integer DEFAULT 10)
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

CREATE OR REPLACE FUNCTION api.interview_snapshot(p_interview_id uuid)
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

CREATE OR REPLACE FUNCTION api.confirm_interview_slot(p_interview_id uuid, p_slot_id uuid, p_ctx jsonb)
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

CREATE OR REPLACE FUNCTION api.offer_snapshot(p_offer_id uuid)
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

CREATE OR REPLACE FUNCTION api.decide_offer_approval(p_offer_id uuid, p_level smallint, p_decision text, p_reason text, p_ctx jsonb)
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

CREATE OR REPLACE FUNCTION api.withdraw_application(p_application_id uuid, p_reason text, p_ctx jsonb)
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

CREATE OR REPLACE FUNCTION api.close_negotiation(p_offer_id uuid, p_reason text, p_ctx jsonb)
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

CREATE OR REPLACE FUNCTION api.cancel_interview(p_interview_id uuid, p_reason text, p_ctx jsonb)
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

CREATE OR REPLACE FUNCTION api.mark_interview_no_show(p_interview_id uuid, p_ctx jsonb)
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

CREATE OR REPLACE FUNCTION api.create_offer(p_application_id uuid, p_offer jsonb, p_ctx jsonb)
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

CREATE OR REPLACE FUNCTION api.transition_application_status(
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

-- Fails if an offer was approved on both levels by the same person in the meantime.
ALTER TABLE hiring.offer_approvals ADD CONSTRAINT offer_approvals_approver_uq UNIQUE (offer_id, approver_id);

DROP FUNCTION api.set_interview_meeting(uuid, jsonb, jsonb);
DROP FUNCTION api.staff_permissions(uuid, text);
DROP FUNCTION hiring.offer_approver_ids(uuid, integer);
DROP FUNCTION hiring.assert_staff_may_manage(jsonb, uuid, uuid);
DROP FUNCTION hiring.staff_may_manage(text, uuid);
DROP FUNCTION hiring.interview_respond_by(uuid, timestamptz);
DROP FUNCTION hiring.ensure_interview_slots(uuid);
DROP TABLE hiring.interview_meeting_plans;
DROP TABLE hiring.interviewer_availability;
ALTER TABLE hiring.interviews
  DROP COLUMN respond_by, DROP COLUMN meeting_id, DROP COLUMN meeting_passcode, DROP COLUMN meeting_notes;
DELETE FROM hiring.settings WHERE key IN ('interview.availability_days', 'interview.min_choice_days');
