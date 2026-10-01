-- migrate:up

-- =============================================================================================
-- Daily management metrics for one business date (company timezone).
-- Flow metrics count what happened during the day; stock metrics describe the queue right now.
-- =============================================================================================
CREATE FUNCTION reporting.daily_metrics(p_date date DEFAULT NULL)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
  WITH params AS (
    SELECT tz, d, (d::timestamp AT TIME ZONE tz) AS t0, ((d + 1)::timestamp AT TIME ZONE tz) AS t1
      FROM (SELECT hiring.company_timezone() AS tz) z,
           LATERAL (SELECT coalesce(p_date, (now() AT TIME ZONE z.tz)::date) AS d) dd
  ),
  events AS (
    SELECT count(*) FILTER (WHERE e.event_type = 'APPLICATION_SUBMITTED')                        AS received,
           count(*) FILTER (WHERE e.outcome = 'INVALID')                                          AS invalid,
           count(*) FILTER (WHERE e.outcome = 'DUPLICATE')                                        AS duplicates,
           count(*) FILTER (WHERE e.outcome = 'NEEDS_REVIEW')                                     AS needs_review_at_intake
      FROM ops.processed_events e, params p
     WHERE e.first_received_at >= p.t0 AND e.first_received_at < p.t1
  ),
  transitions AS (
    SELECT count(*) FILTER (WHERE h.to_status = 'SHORTLISTED')                                   AS shortlisted,
           count(*) FILTER (WHERE h.to_status IN ('SCREENING_REVIEW', 'INTERVIEW_REVIEW'))       AS manual_review,
           count(*) FILTER (WHERE h.to_status = 'REJECTED')                                      AS rejected,
           count(*) FILTER (WHERE h.to_status = 'SELECTED')                                      AS selected,
           count(*) FILTER (WHERE h.to_status = 'OFFERED')                                       AS offers_sent,
           count(*) FILTER (WHERE h.to_status = 'ACCEPTED')                                      AS offers_accepted,
           count(*) FILTER (WHERE h.to_status = 'DECLINED')                                      AS offers_declined,
           count(*) FILTER (WHERE h.to_status = 'OFFER_EXPIRED')                                 AS offers_expired,
           count(*) FILTER (WHERE h.to_status = 'ONBOARDED')                                     AS onboarded
      FROM hiring.candidate_status_history h, params p
     WHERE h.changed_at >= p.t0 AND h.changed_at < p.t1
  ),
  logs AS (
    SELECT count(*) FILTER (WHERE l.action = 'EVENT_REPLAY_DETECTED') AS replays_ignored,
           count(*) FILTER (WHERE l.action = 'RETRY_ATTEMPT')         AS retry_attempts,
           count(*) FILTER (WHERE l.action = 'RETRY_RECOVERED')       AS retries_recovered,
           count(*) FILTER (WHERE l.action = 'DUPLICATE_NOTIFICATION_SUPPRESSED') AS notifications_suppressed
      FROM ops.automation_logs l, params p
     WHERE l.occurred_at >= p.t0 AND l.occurred_at < p.t1
  ),
  execs AS (
    SELECT count(*)                                         AS executions,
           count(*) FILTER (WHERE w.status = 'SUCCEEDED')   AS succeeded,
           count(*) FILTER (WHERE w.status = 'FAILED')      AS failed,
           round(avg(w.duration_ms))                        AS avg_duration_ms
      FROM ops.workflow_executions w, params p
     WHERE w.started_at >= p.t0 AND w.started_at < p.t1
  ),
  errs AS (
    SELECT count(*) AS errors_today FROM ops.automation_errors x, params p
     WHERE x.created_at >= p.t0 AND x.created_at < p.t1
  ),
  screening_time AS (
    SELECT round(avg(extract(epoch FROM (h.changed_at - a.created_at)) / 60)::numeric, 1) AS avg_minutes
      FROM hiring.candidate_status_history h
      JOIN hiring.applications a ON a.id = h.application_id, params p
     WHERE h.from_status = 'SCORED' AND h.changed_at >= p.t0 AND h.changed_at < p.t1
  )
  SELECT jsonb_build_object(
    'report_date',                     p.d,
    'timezone',                        p.tz,
    'generated_at',                    now(),
    -- flow (during the day)
    'applications_received',           ev.received,
    'invalid_applications',            ev.invalid,
    'duplicates_prevented',            ev.duplicates + lg.replays_ignored,
    'duplicate_submissions',           ev.duplicates,
    'event_replays_ignored',           lg.replays_ignored,
    'shortlisted',                     tr.shortlisted,
    'manual_review',                   tr.manual_review,
    'rejected',                        tr.rejected,
    'selected',                        tr.selected,
    'offers_sent',                     tr.offers_sent,
    'offers_accepted',                 tr.offers_accepted,
    'offers_declined',                 tr.offers_declined,
    'offers_expired',                  tr.offers_expired,
    'employees_onboarded',             tr.onboarded,
    'workflow_executions',             ex.executions,
    'workflow_executions_succeeded',   ex.succeeded,
    'workflow_executions_failed',      ex.failed,
    'workflow_failures',               er.errors_today,
    'retry_attempts',                  lg.retry_attempts,
    'retries_recovered',               lg.retries_recovered,
    'duplicate_notifications_suppressed', lg.notifications_suppressed,
    'avg_execution_ms',                ex.avg_duration_ms,
    'avg_minutes_to_screening_decision', st.avg_minutes,
    -- stock (right now)
    'pending_screening_reviews',  (SELECT count(*) FROM hiring.applications a WHERE a.status = 'SCREENING_REVIEW'),
    'pending_interview_reviews',  (SELECT count(*) FROM hiring.applications a WHERE a.status = 'INTERVIEW_REVIEW'),
    'interviews_pending',         (SELECT count(*) FROM hiring.interviews i WHERE i.status IN ('INVITED', 'CONFIRMED')),
    'interview_feedback_pending', (SELECT count(*) FROM hiring.interviews i
                                    WHERE i.status = 'CONFIRMED' AND i.scheduled_end < now()
                                      AND NOT EXISTS (SELECT 1 FROM hiring.interview_feedback f WHERE f.interview_id = i.id)),
    'offers_pending_approval',    (SELECT count(*) FROM hiring.offers o WHERE o.status = 'PENDING_APPROVAL'),
    'offers_awaiting_response',   (SELECT count(*) FROM hiring.offers o WHERE o.status = 'SENT'),
    'employees_onboarding',       (SELECT count(*) FROM hiring.employees e WHERE e.status = 'ONBOARDING'),
    'overdue_onboarding_tasks',   (SELECT count(*) FROM hiring.onboarding_tasks t
                                    WHERE t.status IN ('PENDING', 'IN_PROGRESS')
                                      AND t.due_date < (now() AT TIME ZONE p.tz)::date),
    'manual_intervention_required', (SELECT count(*) FROM ops.automation_errors x WHERE x.status IN ('OPEN', 'REPLAYING')),
    'scheduled_actions_pending',  (SELECT count(*) FROM ops.scheduled_actions s WHERE s.status = 'PENDING'),
    'scheduled_actions_failed',   (SELECT count(*) FROM ops.scheduled_actions s WHERE s.status = 'FAILED')
  )
  FROM params p, events ev, transitions tr, logs lg, execs ex, errs er, screening_time st
$$;

CREATE VIEW reporting.v_ops_overview AS
SELECT * FROM jsonb_to_record(reporting.daily_metrics(NULL)) AS m(
  report_date date, timezone text, generated_at timestamptz,
  applications_received int, invalid_applications int, duplicates_prevented int, shortlisted int, manual_review int,
  rejected int, offers_accepted int, workflow_executions int, workflow_executions_succeeded int,
  workflow_executions_failed int, workflow_failures int, retry_attempts int, retries_recovered int,
  avg_execution_ms numeric, avg_minutes_to_screening_decision numeric,
  pending_screening_reviews int, pending_interview_reviews int, interviews_pending int, interview_feedback_pending int,
  offers_pending_approval int, offers_awaiting_response int, employees_onboarding int, overdue_onboarding_tasks int,
  manual_intervention_required int, scheduled_actions_pending int, scheduled_actions_failed int);
COMMENT ON VIEW reporting.v_ops_overview IS 'One-row operational health summary for today (company timezone).';

CREATE VIEW reporting.v_pipeline_by_status AS
SELECT s.code AS status, s.stage, s.sort_order, s.awaits_human,
       count(a.id)               AS applications,
       min(a.status_changed_at)  AS oldest_since
  FROM hiring.application_statuses s
  LEFT JOIN hiring.applications a ON a.status = s.code
 GROUP BY s.code, s.stage, s.sort_order, s.awaits_human
 ORDER BY s.sort_order;

CREATE VIEW reporting.v_manual_review_queue AS
SELECT a.id AS application_id, a.application_code, a.correlation_id, a.status,
       c.candidate_code, c.full_name, c.email, p.title AS position, a.review_reason,
       a.application_score, a.ai_recommendation, a.final_score, a.validation_issues,
       a.possible_duplicate_of IS NOT NULL AS possible_duplicate,
       a.status_changed_at AS waiting_since,
       round(extract(epoch FROM (now() - a.status_changed_at)) / 3600, 1) AS waiting_hours
  FROM hiring.applications a
  JOIN hiring.candidates c ON c.id = a.candidate_id
  JOIN hiring.job_positions p ON p.id = a.job_position_id
 WHERE a.status IN ('SCREENING_REVIEW', 'INTERVIEW_REVIEW')
 ORDER BY a.status_changed_at;

CREATE VIEW reporting.v_pending_offer_approvals AS
SELECT o.id AS offer_id, o.offer_code, o.revision, a.application_code, c.full_name, p.title AS position,
       o.monthly_salary, o.currency, o.required_approval_levels,
       (SELECT count(*) FROM hiring.offer_approvals oa WHERE oa.offer_id = o.id AND oa.decision = 'APPROVED') AS approvals_granted,
       (SELECT min(lvl) FROM generate_series(1, o.required_approval_levels) AS lvl
         WHERE NOT EXISTS (SELECT 1 FROM hiring.offer_approvals oa WHERE oa.offer_id = o.id AND oa.level = lvl)) AS next_level,
       o.created_at AS waiting_since,
       round(extract(epoch FROM (now() - o.created_at)) / 3600, 1) AS waiting_hours
  FROM hiring.offers o
  JOIN hiring.applications a ON a.id = o.application_id
  JOIN hiring.candidates c ON c.id = a.candidate_id
  JOIN hiring.job_positions p ON p.id = o.job_position_id
 WHERE o.status = 'PENDING_APPROVAL'
 ORDER BY o.created_at;

CREATE VIEW reporting.v_pending_interview_feedback AS
SELECT i.id AS interview_id, i.interview_code, a.application_code, c.full_name AS candidate_name,
       s.full_name AS interviewer_name, s.email AS interviewer_email, i.scheduled_end,
       round(extract(epoch FROM (now() - i.scheduled_end)) / 3600, 1) AS hours_since_interview
  FROM hiring.interviews i
  JOIN hiring.applications a ON a.id = i.application_id
  JOIN hiring.candidates c ON c.id = a.candidate_id
  JOIN hiring.staff_members s ON s.id = i.interviewer_id
 WHERE i.status = 'CONFIRMED' AND i.scheduled_end < now()
   AND NOT EXISTS (SELECT 1 FROM hiring.interview_feedback f WHERE f.interview_id = i.id)
 ORDER BY i.scheduled_end;

CREATE VIEW reporting.v_open_offers AS
SELECT o.id AS offer_id, o.offer_code, o.status, a.application_code, c.full_name, p.title AS position,
       o.monthly_salary, o.currency, o.sent_at, o.expires_at,
       round(extract(epoch FROM (o.expires_at - now())) / 3600, 1) AS hours_to_expiry
  FROM hiring.offers o
  JOIN hiring.applications a ON a.id = o.application_id
  JOIN hiring.candidates c ON c.id = a.candidate_id
  JOIN hiring.job_positions p ON p.id = o.job_position_id
 WHERE o.status IN ('SENT', 'NEGOTIATION', 'EXPIRED')
 ORDER BY o.expires_at;

CREATE VIEW reporting.v_overdue_onboarding_tasks AS
SELECT t.id AS task_id, e.employee_code, e.full_name AS employee_name, t.task_key, t.title, t.owner_role,
       s.full_name AS assignee_name, s.email AS assignee_email, t.due_date, t.status, t.reminder_count,
       t.last_reminder_at,
       (now() AT TIME ZONE hiring.company_timezone())::date - t.due_date AS days_overdue
  FROM hiring.onboarding_tasks t
  JOIN hiring.employees e ON e.id = t.employee_id
  LEFT JOIN hiring.staff_members s ON s.id = t.assignee_id
 WHERE t.status IN ('PENDING', 'IN_PROGRESS')
   AND t.due_date < (now() AT TIME ZONE hiring.company_timezone())::date
 ORDER BY t.due_date;

CREATE VIEW reporting.v_error_queue AS
SELECT x.id AS error_id, x.status, x.error_class, x.error_code, x.error_message, x.http_status,
       x.workflow_name, x.node_name, x.entity_type, x.entity_id, x.correlation_id, x.retry_count,
       x.occurrence_count, x.replay_workflow, x.replay_count, x.created_at, x.updated_at
  FROM ops.automation_errors x
 WHERE x.status IN ('OPEN', 'REPLAYING')
 ORDER BY x.created_at;

CREATE VIEW reporting.v_scheduled_actions AS
SELECT s.id, s.action_type, s.status, s.run_at, s.attempts, s.max_attempts, s.entity_type, s.entity_id,
       a.application_code, s.correlation_id, s.last_error, s.cancel_reason, s.created_at
  FROM ops.scheduled_actions s
  LEFT JOIN hiring.applications a ON a.id = s.application_id
 WHERE s.status IN ('PENDING', 'RUNNING', 'FAILED')
 ORDER BY s.run_at;

CREATE VIEW reporting.v_workflow_health_today AS
SELECT w.workflow_name,
       count(*)                                       AS runs,
       count(*) FILTER (WHERE w.status = 'SUCCEEDED') AS succeeded,
       count(*) FILTER (WHERE w.status = 'FAILED')    AS failed,
       count(*) FILTER (WHERE w.status = 'RUNNING')   AS running,
       round(avg(w.duration_ms))                      AS avg_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY w.duration_ms) AS p95_ms,
       max(w.started_at)                              AS last_run_at
  FROM ops.workflow_executions w
 WHERE w.started_at >= ((now() AT TIME ZONE hiring.company_timezone())::date::timestamp AT TIME ZONE hiring.company_timezone())
 GROUP BY w.workflow_name
 ORDER BY w.workflow_name;

-- End-to-end trace: everything that happened for one correlation id, in order.
CREATE FUNCTION reporting.trace(p_correlation_id text)
RETURNS TABLE (occurred_at timestamptz, source text, workflow_name text, execution_id text, entity_type text,
               entity_id text, action text, outcome text, from_status text, to_status text, actor text,
               retry_count integer, error_code text, error_message text, details jsonb)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT l.occurred_at, 'automation_log', l.workflow_name, l.execution_id, l.entity_type, l.entity_id, l.action,
         l.outcome, l.from_status, l.to_status, l.actor_type || ':' || l.actor_id, l.retry_count, l.error_code,
         l.error_message, l.details
    FROM ops.automation_logs l
   WHERE l.correlation_id = p_correlation_id
  UNION ALL
  SELECT x.created_at, 'error_queue', x.workflow_name, x.execution_id, x.entity_type, x.entity_id, 'ERROR_' || x.status,
         'FAILURE', NULL, NULL, NULL, x.retry_count, x.error_code, x.error_message,
         jsonb_build_object('error_id', x.id, 'occurrences', x.occurrence_count, 'replays', x.replay_count)
    FROM ops.automation_errors x
   WHERE x.correlation_id = p_correlation_id
  ORDER BY 1
$$;

-- =============================================================================================
-- Daily report persistence (one row per date => the report is sent at most once).
-- =============================================================================================
CREATE FUNCTION api.save_daily_report(
  p_report_date    date,
  p_metrics        jsonb,
  p_summary        text,
  p_summary_source text,
  p_ctx            jsonb)
RETURNS TABLE (report_date date, created boolean, delivered_at timestamptz)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_ctx jsonb := ops.require_ctx(p_ctx);
  v_row ops.daily_reports%ROWTYPE;
BEGIN
  INSERT INTO ops.daily_reports (report_date, metrics, summary, summary_source, correlation_id)
  VALUES (p_report_date, p_metrics, p_summary, upper(p_summary_source), nullif(v_ctx->>'correlation_id', ''))
  ON CONFLICT (report_date) DO NOTHING
  RETURNING * INTO v_row;

  IF FOUND THEN
    PERFORM ops.write_log(v_ctx, 'REPORT', p_report_date::text, 'DAILY_REPORT_GENERATED', 'SUCCESS',
      jsonb_build_object('summary_source', upper(p_summary_source)));
    RETURN QUERY SELECT v_row.report_date, true, v_row.delivered_at;
    RETURN;
  END IF;
  SELECT * INTO v_row FROM ops.daily_reports d WHERE d.report_date = p_report_date;
  RETURN QUERY SELECT v_row.report_date, false, v_row.delivered_at;
END;
$$;

CREATE FUNCTION api.mark_daily_report_delivered(p_report_date date, p_ctx jsonb)
RETURNS timestamptz
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_ctx jsonb := ops.require_ctx(p_ctx);
  v_at  timestamptz;
BEGIN
  UPDATE ops.daily_reports d SET delivered_at = coalesce(d.delivered_at, now())
   WHERE d.report_date = p_report_date
  RETURNING d.delivered_at INTO v_at;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT404', 'REPORT_NOT_FOUND', format('no report saved for %s', p_report_date));
  END IF;
  PERFORM ops.write_log(v_ctx, 'REPORT', p_report_date::text, 'DAILY_REPORT_DELIVERED', 'SUCCESS');
  RETURN v_at;
END;
$$;

-- migrate:down
DROP FUNCTION IF EXISTS api.mark_daily_report_delivered, api.save_daily_report, reporting.trace;
DROP VIEW IF EXISTS reporting.v_workflow_health_today, reporting.v_scheduled_actions, reporting.v_error_queue,
  reporting.v_overdue_onboarding_tasks, reporting.v_open_offers, reporting.v_pending_interview_feedback,
  reporting.v_pending_offer_approvals, reporting.v_manual_review_queue, reporting.v_pipeline_by_status,
  reporting.v_ops_overview;
DROP FUNCTION IF EXISTS reporting.daily_metrics;
