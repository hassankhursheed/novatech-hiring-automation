-- Removes applications and everything that belongs to them (candidate, history, scores, AI analyses, interviews,
-- offers, employee, onboarding tasks, emails, timers, logs, errors) from the business database.
--
--   scope=test (default)  only applicants whose email is on a reserved test domain (RFC 2606 / RFC 6761:
--                         example.com, example.net, example.org, *.example, *.test, *.invalid, *.localhost).
--                         Such an address cannot belong to a real person, so real applications are never touched.
--   scope=all             every application and every operational record: a clean start before go-live.
--
-- Configuration, staff, positions, scoring rules, onboarding templates and interview slots always stay; slots that
-- were booked by removed interviews are opened again. Application codes are never reused. The stored CV and offer
-- letter files are removed by scripts/purge-applications.sh afterwards (the database cannot reach them).
--
-- Run with scripts/purge-applications.sh (dry run first, a backup, then confirmation). Runs in ONE transaction as the
-- database owner; the append-only guards are lifted only inside that transaction, so a failure leaves everything as
-- it was. dry_run=true rolls the transaction back after reporting what would be removed.
\set ON_ERROR_STOP on
\if :{?scope}
\else
  \set scope test
\endif
\if :{?dry_run}
\else
  \set dry_run false
\endif

BEGIN;
SET LOCAL lock_timeout = '15s';

CREATE TEMP TABLE purge_scope ON COMMIT DROP AS SELECT :'scope'::text AS scope;
DO $$ BEGIN
  IF (SELECT scope FROM purge_scope) NOT IN ('test', 'all') THEN
    RAISE EXCEPTION 'scope must be test or all';
  END IF;
END $$;

CREATE FUNCTION pg_temp.is_test_email(p_email text) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
  SELECT lower(split_part(coalesce(p_email, ''), '@', 2))
         ~ '(^|\.)example\.(com|net|org)$|(^|\.)(example|test|invalid|localhost)$'
$$;

CREATE TEMP TABLE purge_candidate ON COMMIT DROP AS
  SELECT c.id FROM hiring.candidates c
   WHERE (SELECT scope FROM purge_scope) = 'all' OR pg_temp.is_test_email(c.email);

CREATE TEMP TABLE purge_app ON COMMIT DROP AS
  SELECT a.id, a.correlation_id, a.source_event_id FROM hiring.applications a
   WHERE a.candidate_id IN (SELECT id FROM purge_candidate);

CREATE TEMP TABLE purge_event ON COMMIT DROP AS
  SELECT e.id, e.correlation_id FROM ops.processed_events e
   WHERE (SELECT scope FROM purge_scope) = 'all'
      OR e.id IN (SELECT source_event_id FROM purge_app)
      OR pg_temp.is_test_email(e.payload->>'email');

CREATE TEMP TABLE purge_corr ON COMMIT DROP AS
  SELECT correlation_id FROM purge_app WHERE correlation_id IS NOT NULL
  UNION SELECT correlation_id FROM purge_event WHERE correlation_id IS NOT NULL;

CREATE TEMP TABLE purge_entity ON COMMIT DROP AS
  SELECT id::text AS id FROM purge_app
  UNION SELECT id::text FROM purge_candidate
  UNION SELECT id::text FROM purge_event
  UNION SELECT i.id::text FROM hiring.interviews i WHERE i.application_id IN (SELECT id FROM purge_app)
  UNION SELECT o.id::text FROM hiring.offers o WHERE o.application_id IN (SELECT id FROM purge_app)
  UNION SELECT e.id::text FROM hiring.employees e WHERE e.application_id IN (SELECT id FROM purge_app)
  UNION SELECT t.id::text FROM hiring.onboarding_tasks t
          JOIN hiring.employees e ON e.id = t.employee_id WHERE e.application_id IN (SELECT id FROM purge_app);

CREATE TEMP TABLE purge_error ON COMMIT DROP AS
  SELECT x.id FROM ops.automation_errors x
   WHERE (SELECT scope FROM purge_scope) = 'all'
      OR x.correlation_id IN (SELECT correlation_id FROM purge_corr)
      OR x.entity_id IN (SELECT id FROM purge_entity);

ALTER TABLE hiring.candidate_status_history DISABLE TRIGGER candidate_status_history_append_only;
ALTER TABLE hiring.offer_approvals DISABLE TRIGGER offer_approvals_append_only;
ALTER TABLE ops.automation_logs DISABLE TRIGGER automation_logs_append_only;

CREATE TEMP TABLE purge_report (step text, removed bigint) ON COMMIT DROP;

WITH d AS (DELETE FROM ops.notifications n
            WHERE (SELECT scope FROM purge_scope) = 'all'
               OR n.application_id IN (SELECT id FROM purge_app)
               OR n.correlation_id IN (SELECT correlation_id FROM purge_corr)
               OR n.entity_id IN (SELECT id FROM purge_entity) RETURNING 1)
-- Not by recipient: staff addresses of a demo configuration (*.example) also receive mail about real applications.
INSERT INTO purge_report SELECT 'emails', count(*) FROM d;

WITH d AS (DELETE FROM ops.scheduled_actions s
            WHERE (SELECT scope FROM purge_scope) = 'all'
               OR s.application_id IN (SELECT id FROM purge_app)
               OR s.entity_id::text IN (SELECT id FROM purge_entity)
               OR s.correlation_id IN (SELECT correlation_id FROM purge_corr) RETURNING 1)
INSERT INTO purge_report SELECT 'timers and outbox actions', count(*) FROM d;

WITH d AS (DELETE FROM ops.consumed_link_tokens t
            WHERE (SELECT scope FROM purge_scope) = 'all' AND t.purpose NOT LIKE 'STAFF%'
               OR t.subject_id IN (SELECT id FROM purge_entity) RETURNING 1)
INSERT INTO purge_report SELECT 'used email links', count(*) FROM d;

WITH d AS (DELETE FROM ops.workflow_executions w
            WHERE (SELECT scope FROM purge_scope) = 'all'
               OR w.correlation_id IN (SELECT correlation_id FROM purge_corr)
               OR w.entity_id IN (SELECT id FROM purge_entity)
               OR w.error_id IN (SELECT id FROM purge_error) RETURNING 1)
INSERT INTO purge_report SELECT 'workflow runs', count(*) FROM d;

WITH d AS (DELETE FROM ops.automation_errors x WHERE x.id IN (SELECT id FROM purge_error) RETURNING 1)
INSERT INTO purge_report SELECT 'automation errors', count(*) FROM d;

WITH d AS (DELETE FROM ops.automation_logs l
            WHERE ((SELECT scope FROM purge_scope) = 'all' AND l.entity_type IS DISTINCT FROM 'STAFF')
               OR l.correlation_id IN (SELECT correlation_id FROM purge_corr)
               OR l.entity_id IN (SELECT id FROM purge_entity) RETURNING 1)
INSERT INTO purge_report SELECT 'audit log entries', count(*) FROM d;

WITH d AS (DELETE FROM ops.test_directives t
            WHERE (SELECT scope FROM purge_scope) = 'all'
               OR t.correlation_id IN (SELECT correlation_id FROM purge_corr) RETURNING 1)
INSERT INTO purge_report SELECT 'test directives', count(*) FROM d;

WITH d AS (DELETE FROM hiring.onboarding_tasks t
            WHERE t.employee_id IN (SELECT e.id FROM hiring.employees e
                                     WHERE e.application_id IN (SELECT id FROM purge_app)) RETURNING 1)
INSERT INTO purge_report SELECT 'onboarding tasks', count(*) FROM d;

WITH d AS (DELETE FROM hiring.employees e WHERE e.application_id IN (SELECT id FROM purge_app) RETURNING 1)
INSERT INTO purge_report SELECT 'employees', count(*) FROM d;

WITH d AS (DELETE FROM hiring.offer_approvals x
            WHERE x.offer_id IN (SELECT o.id FROM hiring.offers o WHERE o.application_id IN (SELECT id FROM purge_app))
           RETURNING 1)
INSERT INTO purge_report SELECT 'offer approvals', count(*) FROM d;

WITH d AS (DELETE FROM hiring.offers o WHERE o.application_id IN (SELECT id FROM purge_app) RETURNING 1)
INSERT INTO purge_report SELECT 'offers', count(*) FROM d;

CREATE TEMP TABLE purge_slot ON COMMIT DROP AS
  SELECT i.slot_id AS id FROM hiring.interviews i
   WHERE i.application_id IN (SELECT id FROM purge_app) AND i.slot_id IS NOT NULL;

WITH d AS (DELETE FROM hiring.interview_feedback f
            WHERE f.interview_id IN (SELECT i.id FROM hiring.interviews i
                                      WHERE i.application_id IN (SELECT id FROM purge_app)) RETURNING 1)
INSERT INTO purge_report SELECT 'interview scorecards', count(*) FROM d;

WITH d AS (DELETE FROM hiring.interview_assessments x WHERE x.application_id IN (SELECT id FROM purge_app) RETURNING 1)
INSERT INTO purge_report SELECT 'AI interview assessments', count(*) FROM d;

WITH d AS (DELETE FROM hiring.interviews i WHERE i.application_id IN (SELECT id FROM purge_app) RETURNING 1)
INSERT INTO purge_report SELECT 'interviews', count(*) FROM d;

WITH d AS (DELETE FROM hiring.interview_meeting_plans m WHERE m.application_id IN (SELECT id FROM purge_app) RETURNING 1)
INSERT INTO purge_report SELECT 'interview meeting details', count(*) FROM d;

WITH u AS (UPDATE hiring.interview_slots s SET status = 'OPEN'
            WHERE s.id IN (SELECT id FROM purge_slot) AND s.status = 'BOOKED'
              AND NOT EXISTS (SELECT 1 FROM hiring.interviews i WHERE i.slot_id = s.id) RETURNING 1)
INSERT INTO purge_report SELECT 'interview slots opened again', count(*) FROM u;

WITH d AS (DELETE FROM hiring.ai_analyses x WHERE x.application_id IN (SELECT id FROM purge_app) RETURNING 1)
INSERT INTO purge_report SELECT 'AI analyses', count(*) FROM d;

WITH d AS (DELETE FROM hiring.application_scores x WHERE x.application_id IN (SELECT id FROM purge_app) RETURNING 1)
INSERT INTO purge_report SELECT 'screening scores', count(*) FROM d;

WITH d AS (DELETE FROM hiring.candidate_status_history h WHERE h.application_id IN (SELECT id FROM purge_app) RETURNING 1)
INSERT INTO purge_report SELECT 'status history entries', count(*) FROM d;

UPDATE hiring.applications a SET possible_duplicate_of = NULL
 WHERE a.possible_duplicate_of IN (SELECT id FROM purge_candidate) AND a.id NOT IN (SELECT id FROM purge_app);

WITH d AS (DELETE FROM hiring.applications a WHERE a.id IN (SELECT id FROM purge_app) RETURNING 1)
INSERT INTO purge_report SELECT 'applications', count(*) FROM d;

WITH d AS (DELETE FROM ops.processed_events e
            WHERE e.id IN (SELECT id FROM purge_event)
              AND NOT EXISTS (SELECT 1 FROM hiring.applications a WHERE a.source_event_id = e.id) RETURNING 1)
INSERT INTO purge_report SELECT 'intake events', count(*) FROM d;

WITH d AS (DELETE FROM hiring.candidates c
            WHERE c.id IN (SELECT id FROM purge_candidate)
              AND NOT EXISTS (SELECT 1 FROM hiring.applications a WHERE a.candidate_id = c.id) RETURNING 1)
INSERT INTO purge_report SELECT 'candidates', count(*) FROM d;

-- Daily reports are aggregates over the removed data; WF-08 writes a fresh one on its next run.
WITH d AS (DELETE FROM ops.daily_reports RETURNING 1)
INSERT INTO purge_report SELECT 'daily reports', count(*) FROM d;

ALTER TABLE hiring.candidate_status_history ENABLE TRIGGER candidate_status_history_append_only;
ALTER TABLE hiring.offer_approvals ENABLE TRIGGER offer_approvals_append_only;
ALTER TABLE ops.automation_logs ENABLE TRIGGER automation_logs_append_only;

\echo
\echo Removed (scope :scope):
SELECT step AS "what", removed AS "rows" FROM purge_report WHERE removed > 0;
SELECT count(*) AS "applications kept" FROM hiring.applications;

\if :dry_run
ROLLBACK;
\echo Dry run: the transaction was rolled back, nothing was changed.
\else
COMMIT;
\endif
