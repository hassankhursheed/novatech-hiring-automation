-- migrate:up

-- =============================================================================================
-- Application state machine (data, not code)
-- =============================================================================================
CREATE TABLE hiring.application_statuses (
  code             text PRIMARY KEY CHECK (code ~ '^[A-Z][A-Z_]*$'),
  stage            text NOT NULL CHECK (stage IN ('INTAKE','SCREENING','INTERVIEW','OFFER','ONBOARDING','CLOSED')),
  is_terminal      boolean NOT NULL DEFAULT false,
  awaits_human     boolean NOT NULL DEFAULT false,   -- sits in a human work queue
  on_enter_action  text CHECK (on_enter_action ~ '^[A-Z][A-Z0-9_]*$'), -- enqueued (outbox) when entering this status
  sort_order       smallint NOT NULL UNIQUE,
  description      text NOT NULL
);

COMMENT ON COLUMN hiring.application_statuses.on_enter_action IS
  'Entering this status enqueues this action in ops.scheduled_actions in the same transaction (transactional outbox). '
  'The n8n dispatcher (WF-00) routes it to the owning workflow.';

INSERT INTO hiring.application_statuses (code, stage, is_terminal, awaits_human, on_enter_action, sort_order, description) VALUES
  ('NEW',                    'INTAKE',     false, false, NULL,                        10,  'Submission persisted, not yet validated'),
  ('VALIDATING',             'INTAKE',     false, false, NULL,                        20,  'Validation and normalisation in progress'),
  ('VALIDATED',              'SCREENING',  false, false, 'SCREEN_APPLICATION',        30,  'Complete and normalised; waiting for scoring'),
  ('SCORED',                 'SCREENING',  false, false, NULL,                        40,  'Rule-based score recorded; waiting for screening decision'),
  ('SCREENING_REVIEW',       'SCREENING',  false, true,  'NOTIFY_REVIEW_QUEUE',       50,  'Needs a recruiter decision (incomplete data, borderline score, AI/rule disagreement)'),
  ('SHORTLISTED',            'INTERVIEW',  false, false, 'INVITE_TO_INTERVIEW',       60,  'Passed screening; interview invitation pending/sent'),
  ('INTERVIEW_SCHEDULED',    'INTERVIEW',  false, false, 'FINALIZE_INTERVIEW_BOOKING',70,  'Candidate confirmed an interview slot'),
  ('INTERVIEWED',            'INTERVIEW',  false, false, 'EVALUATE_INTERVIEW',        80,  'Interviewer feedback recorded; waiting for evaluation'),
  ('INTERVIEW_REVIEW',       'INTERVIEW',  false, true,  'NOTIFY_REVIEW_QUEUE',       90,  'Needs a hiring-manager decision after interview'),
  ('SELECTED',               'OFFER',      false, false, 'PREPARE_OFFER',             100, 'Selected for hire; offer to be prepared'),
  ('OFFER_PENDING_APPROVAL', 'OFFER',      false, true,  'REQUEST_OFFER_APPROVAL',    110, 'Offer drafted; waiting for approval(s)'),
  ('OFFERED',                'OFFER',      false, false, NULL,                        120, 'Approved offer sent; waiting for candidate response'),
  ('NEGOTIATION',            'OFFER',      false, true,  'NOTIFY_NEGOTIATION',        130, 'Candidate asked to negotiate; HR to revise or close'),
  ('ACCEPTED',               'ONBOARDING', false, false, 'START_ONBOARDING',          140, 'Candidate accepted the offer'),
  ('ONBOARDING',             'ONBOARDING', false, false, NULL,                        150, 'Employee record created; onboarding tasks in progress'),
  ('ONBOARDED',              'CLOSED',     true,  false, 'NOTIFY_ONBOARDING_COMPLETE',160, 'All onboarding tasks complete'),
  ('REJECTED',               'CLOSED',     true,  false, 'SEND_REJECTION_NOTICE',     170, 'Not progressing'),
  ('DECLINED',               'CLOSED',     true,  false, 'NOTIFY_OFFER_CLOSED',       180, 'Candidate declined the offer'),
  ('OFFER_EXPIRED',          'CLOSED',     true,  false, 'NOTIFY_OFFER_CLOSED',       190, 'Offer expired without a response'),
  ('WITHDRAWN',              'CLOSED',     true,  false, NULL,                        200, 'Candidate withdrew or application was cancelled');

CREATE TABLE hiring.status_transitions (
  from_status          text NOT NULL REFERENCES hiring.application_statuses(code),
  to_status            text NOT NULL REFERENCES hiring.application_statuses(code),
  allowed_actor_types  text[] NOT NULL,
  managed_by           text,        -- when set, only this api function may perform the transition
  description          text NOT NULL,
  PRIMARY KEY (from_status, to_status),
  CHECK (from_status <> to_status),
  CHECK (cardinality(allowed_actor_types) > 0
         AND allowed_actor_types <@ ARRAY['SYSTEM','STAFF','CANDIDATE']::text[])
);

COMMENT ON TABLE hiring.status_transitions IS
  'Allowed application status transitions. AI is never an allowed actor: AI output is advisory only.';
COMMENT ON COLUMN hiring.status_transitions.managed_by IS
  'Transitions with side effects (offers, bookings, employees) can only be made by the named api function, '
  'so related records always change together with the status.';

INSERT INTO hiring.status_transitions (from_status, to_status, allowed_actor_types, managed_by, description) VALUES
  -- intake
  ('NEW',                    'VALIDATING',             '{SYSTEM}',           'submit_application',          'Validation started'),
  ('VALIDATING',             'VALIDATED',              '{SYSTEM}',           'submit_application',          'Submission is complete and valid'),
  ('VALIDATING',             'SCREENING_REVIEW',       '{SYSTEM}',           'submit_application',          'Incomplete or suspicious submission needs review'),
  -- screening
  ('VALIDATED',              'SCORED',                 '{SYSTEM}',           'record_application_score',    'Rule-based score recorded'),
  ('VALIDATED',              'SCREENING_REVIEW',       '{SYSTEM}',           NULL,                          'Automatic screening impossible (e.g. no active rules)'),
  ('SCORED',                 'SHORTLISTED',            '{SYSTEM}',           'apply_screening_decision',    'Screening policy shortlisted the candidate'),
  ('SCORED',                 'SCREENING_REVIEW',       '{SYSTEM}',           'apply_screening_decision',    'Screening policy requires a human decision'),
  ('SCORED',                 'REJECTED',               '{SYSTEM}',           'apply_screening_decision',    'Screening policy rejected the candidate'),
  ('SCREENING_REVIEW',       'VALIDATED',              '{STAFF}',            NULL,                          'Recruiter corrected the data; re-run screening'),
  ('SCREENING_REVIEW',       'SHORTLISTED',            '{STAFF}',            NULL,                          'Recruiter shortlisted the candidate'),
  ('SCREENING_REVIEW',       'REJECTED',               '{STAFF}',            NULL,                          'Recruiter rejected the candidate'),
  -- interview
  ('SHORTLISTED',            'INTERVIEW_SCHEDULED',    '{CANDIDATE,STAFF}',  'confirm_interview_slot',      'Interview slot confirmed'),
  ('SHORTLISTED',            'SCREENING_REVIEW',       '{SYSTEM}',           'expire_interview_invitation', 'Invitation expired without confirmation'),
  ('SHORTLISTED',            'REJECTED',               '{STAFF}',            NULL,                          'Recruiter closed the application'),
  ('INTERVIEW_SCHEDULED',    'INTERVIEWED',            '{STAFF,SYSTEM}',     'submit_interview_feedback',   'Interviewer feedback submitted'),
  ('INTERVIEW_SCHEDULED',    'SHORTLISTED',            '{CANDIDATE,STAFF}',  'cancel_interview',            'Interview cancelled; slot released for rescheduling'),
  ('INTERVIEW_SCHEDULED',    'INTERVIEW_REVIEW',       '{SYSTEM,STAFF}',     'mark_interview_no_show',      'Candidate did not attend'),
  ('INTERVIEWED',            'SELECTED',               '{SYSTEM,STAFF}',     'apply_interview_decision',    'Combined score meets the selection threshold'),
  ('INTERVIEWED',            'REJECTED',               '{SYSTEM,STAFF}',     'apply_interview_decision',    'Combined score below the review threshold'),
  ('INTERVIEWED',            'INTERVIEW_REVIEW',       '{SYSTEM}',           'apply_interview_decision',    'Borderline score or interviewer/score disagreement'),
  ('INTERVIEW_REVIEW',       'SELECTED',               '{STAFF}',            NULL,                          'Hiring manager selected the candidate'),
  ('INTERVIEW_REVIEW',       'REJECTED',               '{STAFF}',            NULL,                          'Hiring manager rejected the candidate'),
  ('INTERVIEW_REVIEW',       'SHORTLISTED',            '{STAFF}',            NULL,                          'Another interview round requested'),
  -- offer
  ('SELECTED',               'OFFER_PENDING_APPROVAL', '{SYSTEM,STAFF}',     'create_offer',                'Offer drafted and sent for approval'),
  ('SELECTED',               'REJECTED',               '{STAFF}',            NULL,                          'Position filled or closed'),
  ('OFFER_PENDING_APPROVAL', 'OFFERED',                '{SYSTEM}',           'mark_offer_sent',             'All approvals granted and offer sent'),
  ('OFFER_PENDING_APPROVAL', 'SELECTED',               '{STAFF}',            'decide_offer_approval',       'Approver rejected the offer; HR to revise'),
  ('OFFERED',                'ACCEPTED',               '{CANDIDATE}',        'respond_to_offer',            'Candidate accepted'),
  ('OFFERED',                'DECLINED',               '{CANDIDATE}',        'respond_to_offer',            'Candidate declined'),
  ('OFFERED',                'NEGOTIATION',            '{CANDIDATE}',        'respond_to_offer',            'Candidate requested negotiation'),
  ('OFFERED',                'OFFER_EXPIRED',          '{SYSTEM}',           'expire_offer',                'Offer validity elapsed without response'),
  ('NEGOTIATION',            'OFFER_PENDING_APPROVAL', '{STAFF}',            'create_offer',                'Revised offer drafted for approval'),
  ('NEGOTIATION',            'DECLINED',               '{CANDIDATE,STAFF}',  'close_negotiation',           'Negotiation ended without agreement'),
  -- onboarding
  ('ACCEPTED',               'ONBOARDING',             '{SYSTEM}',           'create_employee_from_offer',  'Employee record created'),
  ('ONBOARDING',             'ONBOARDED',              '{SYSTEM,STAFF}',     'complete_onboarding_task',    'All onboarding tasks completed');

-- Withdrawal is possible from every active, post-intake status.
INSERT INTO hiring.status_transitions (from_status, to_status, allowed_actor_types, managed_by, description)
SELECT s.code, 'WITHDRAWN', '{CANDIDATE,STAFF}', 'withdraw_application', 'Candidate withdrew or HR cancelled'
FROM hiring.application_statuses s
WHERE NOT s.is_terminal AND s.code NOT IN ('NEW','VALIDATING','ONBOARDING');

-- =============================================================================================
-- Typed configuration (edited by HR admins; validated on write)
-- =============================================================================================
CREATE TABLE hiring.settings (
  key          text PRIMARY KEY CHECK (key ~ '^[a-z0-9_]+(\.[a-z0-9_]+)+$'),
  value        jsonb NOT NULL,
  value_type   text NOT NULL CHECK (value_type IN ('interval','number','boolean','text')),
  description  text NOT NULL,
  updated_at   timestamptz NOT NULL DEFAULT now(),
  updated_by   text NOT NULL DEFAULT 'migration'
);

CREATE FUNCTION hiring.validate_setting()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  v_interval interval;
BEGIN
  IF NEW.value_type = 'interval' THEN
    IF jsonb_typeof(NEW.value) <> 'string' THEN
      PERFORM ops.fail('NT400', 'INVALID_SETTING', NEW.key || ' must be a JSON string such as "2 days"');
    END IF;
    BEGIN
      v_interval := (NEW.value #>> '{}')::interval;
    EXCEPTION WHEN others THEN
      PERFORM ops.fail('NT400', 'INVALID_SETTING', NEW.key || ' is not a valid interval: ' || (NEW.value #>> '{}'));
    END;
    IF v_interval <= interval '0' THEN
      PERFORM ops.fail('NT400', 'INVALID_SETTING', NEW.key || ' must be a positive interval');
    END IF;
  ELSIF NEW.value_type = 'number' AND jsonb_typeof(NEW.value) <> 'number' THEN
    PERFORM ops.fail('NT400', 'INVALID_SETTING', NEW.key || ' must be a JSON number');
  ELSIF NEW.value_type = 'boolean' AND jsonb_typeof(NEW.value) <> 'boolean' THEN
    PERFORM ops.fail('NT400', 'INVALID_SETTING', NEW.key || ' must be true or false');
  ELSIF NEW.value_type = 'text' AND (jsonb_typeof(NEW.value) <> 'string' OR btrim(NEW.value #>> '{}') = '') THEN
    PERFORM ops.fail('NT400', 'INVALID_SETTING', NEW.key || ' must be a non-empty JSON string');
  END IF;
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

CREATE TRIGGER settings_validate BEFORE INSERT OR UPDATE ON hiring.settings
  FOR EACH ROW EXECUTE FUNCTION hiring.validate_setting();

CREATE FUNCTION hiring.setting_value(p_key text, p_type text)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
  v_value jsonb;
BEGIN
  SELECT s.value INTO v_value FROM hiring.settings s WHERE s.key = p_key AND s.value_type = p_type;
  IF NOT FOUND THEN
    PERFORM ops.fail('NT422', 'SETTING_MISSING', p_type || ' setting "' || p_key || '" is not configured');
  END IF;
  RETURN v_value;
END;
$$;

CREATE FUNCTION hiring.setting_interval(p_key text) RETURNS interval
LANGUAGE sql STABLE AS $$ SELECT (hiring.setting_value(p_key, 'interval') #>> '{}')::interval $$;

CREATE FUNCTION hiring.setting_number(p_key text) RETURNS numeric
LANGUAGE sql STABLE AS $$ SELECT (hiring.setting_value(p_key, 'number') #>> '{}')::numeric $$;

CREATE FUNCTION hiring.setting_bool(p_key text) RETURNS boolean
LANGUAGE sql STABLE AS $$ SELECT (hiring.setting_value(p_key, 'boolean') #>> '{}')::boolean $$;

CREATE FUNCTION hiring.setting_text(p_key text) RETURNS text
LANGUAGE sql STABLE AS $$ SELECT hiring.setting_value(p_key, 'text') #>> '{}' $$;

CREATE FUNCTION hiring.company_timezone() RETURNS text
LANGUAGE sql STABLE AS $$ SELECT hiring.setting_text('company.timezone') $$;

-- Defaults every installation needs. Company-specific values are changed via UPDATE (validated by the trigger).
INSERT INTO hiring.settings (key, value, value_type, description) VALUES
  ('company.name',                        '"NovaTech Solutions"', 'text',     'Company name used in communications'),
  ('company.timezone',                    '"Asia/Karachi"',       'text',     'IANA timezone for business dates, reports and codes'),
  ('company.currency',                    '"PKR"',                'text',     'ISO-4217 currency for salaries'),
  ('company.email_domain',                '"novatech.example"',   'text',     'Domain used for (simulated) employee account creation'),
  ('company.employee_code_prefix',        '"NT"',                 'text',     'Prefix of employee IDs, e.g. NT-2026-001'),
  ('screening.auto_reject_enabled',       'true',                 'boolean',  'If false, low scores go to manual review instead of automatic rejection'),
  ('screening.ai_enabled',                'true',                 'boolean',  'If false, screening runs on rules only'),
  ('interview.invite_reminder_after',     '"2 days"',             'interval', 'Remind the candidate if the invitation is still unconfirmed after this long'),
  ('interview.invite_expires_after',      '"4 days"',             'interval', 'Unconfirmed invitations expire and go back to screening review'),
  ('interview.feedback_reminder_after',   '"1 day"',              'interval', 'Remind the interviewer if feedback is missing this long after the interview ends'),
  ('interview.feedback_escalate_after',   '"2 days"',             'interval', 'Escalate missing feedback to HR this long after the interview ends'),
  ('evaluation.application_weight',       '0.30',                 'number',   'Weight of the screening score in the final score'),
  ('evaluation.interview_weight',         '0.70',                 'number',   'Weight of the interview score in the final score'),
  ('evaluation.select_min_score',         '75',                   'number',   'Final score at or above which a candidate is selected'),
  ('evaluation.review_min_score',         '60',                   'number',   'Final score at or above which a candidate goes to hiring-manager review'),
  ('offer.second_approval_threshold',     '250000',               'number',   'Monthly salary above which a second approval level is required'),
  ('offer.validity',                      '"5 days"',             'interval', 'How long a sent offer stays open'),
  ('offer.first_reminder_after',          '"2 days"',             'interval', 'First reminder after the offer is sent'),
  ('offer.final_reminder_after',          '"4 days"',             'interval', 'Final reminder after the offer is sent'),
  ('offer.default_probation_months',      '3',                    'number',   'Default probation period'),
  ('onboarding.overdue_reminder_every',   '"1 day"',              'interval', 'Minimum gap between reminders for an overdue onboarding task'),
  ('privacy.rejected_retention',          '"180 days"',           'interval', 'Personal data of rejected candidates is anonymised after this period');

-- =============================================================================================
-- Staff (HR users, interviewers, managers, approvers)
-- =============================================================================================
CREATE TABLE hiring.staff_members (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  full_name         text NOT NULL CHECK (btrim(full_name) <> ''),
  email             text NOT NULL CHECK (email = lower(btrim(email)) AND email LIKE '%_@_%'),
  department        text,
  job_title         text,
  roles             text[] NOT NULL DEFAULT '{}' CHECK (roles <@ ARRAY[
                      'HR_ADMIN','RECRUITER','HIRING_MANAGER','INTERVIEWER','APPROVER_L1','APPROVER_L2','IT_ADMIN']::text[]),
  telegram_chat_id  text,
  auth_subject      text UNIQUE,   -- identity-provider subject once the portal login is linked
  is_active         boolean NOT NULL DEFAULT true,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT staff_members_email_uq UNIQUE (email)
);
CREATE INDEX staff_members_roles_gin ON hiring.staff_members USING gin (roles);
CREATE TRIGGER staff_members_touch BEFORE UPDATE ON hiring.staff_members
  FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();

-- =============================================================================================
-- Job positions
-- =============================================================================================
CREATE TABLE hiring.job_positions (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code                  text NOT NULL UNIQUE CHECK (code ~ '^[A-Z][A-Z0-9_]{1,31}$'),
  title                 text NOT NULL UNIQUE,
  department            text NOT NULL,
  employment_type       text NOT NULL DEFAULT 'FULL_TIME'
                          CHECK (employment_type IN ('FULL_TIME','PART_TIME','CONTRACT','INTERNSHIP')),
  min_experience_years  numeric(4,1) NOT NULL DEFAULT 0 CHECK (min_experience_years >= 0),
  salary_min            bigint CHECK (salary_min > 0),
  salary_max            bigint,
  currency              char(3) NOT NULL DEFAULT 'PKR',
  hiring_manager_id     uuid REFERENCES hiring.staff_members(id),
  default_interviewer_id uuid REFERENCES hiring.staff_members(id),
  is_open               boolean NOT NULL DEFAULT true,
  description           text,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  CHECK (salary_max IS NULL OR salary_min IS NULL OR salary_max >= salary_min)
);
CREATE TRIGGER job_positions_touch BEFORE UPDATE ON hiring.job_positions
  FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();

-- =============================================================================================
-- Skill vocabulary: aliases map free-text skills to canonical names used by scoring rules
-- =============================================================================================
CREATE TABLE hiring.skill_aliases (
  alias      text PRIMARY KEY CHECK (alias = lower(btrim(alias)) AND alias <> ''),
  canonical  text NOT NULL CHECK (canonical = lower(btrim(canonical)) AND canonical <> '')
);

-- =============================================================================================
-- Data-driven scoring. Any change to rules or thresholds bumps scoring_configs.version, and every
-- stored score records the version it was computed with (reproducible, auditable decisions).
-- =============================================================================================
CREATE TABLE hiring.scoring_configs (
  job_position_id      uuid PRIMARY KEY REFERENCES hiring.job_positions(id) ON DELETE CASCADE,
  shortlist_min_score  numeric(5,2) NOT NULL CHECK (shortlist_min_score BETWEEN 0 AND 100),
  review_min_score     numeric(5,2) NOT NULL CHECK (review_min_score BETWEEN 0 AND 100),
  version              integer NOT NULL DEFAULT 1 CHECK (version >= 1),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  updated_by           text NOT NULL DEFAULT 'migration',
  CHECK (review_min_score <= shortlist_min_score)
);

CREATE TABLE hiring.scoring_rules (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_position_id  uuid NOT NULL REFERENCES hiring.scoring_configs(job_position_id) ON DELETE CASCADE,
  rule_key         text NOT NULL CHECK (rule_key ~ '^[a-z][a-z0-9_]*$'),
  label            text NOT NULL,
  rule_type        text NOT NULL CHECK (rule_type IN ('SKILL_ANY','SKILL_ALL','MIN_EXPERIENCE_YEARS','KEYWORD_ANY')),
  match_terms      text[] NOT NULL DEFAULT '{}',
  min_value        numeric(6,2),
  points           integer NOT NULL CHECK (points BETWEEN 1 AND 100),
  is_active        boolean NOT NULL DEFAULT true,
  sort_order       smallint NOT NULL DEFAULT 100,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (job_position_id, rule_key),
  CHECK (
    (rule_type = 'MIN_EXPERIENCE_YEARS' AND min_value IS NOT NULL AND min_value >= 0)
    OR (rule_type <> 'MIN_EXPERIENCE_YEARS' AND cardinality(match_terms) > 0)
  )
);
COMMENT ON TABLE hiring.scoring_rules IS
  'SKILL_ANY: any listed skill present. SKILL_ALL: all present. MIN_EXPERIENCE_YEARS: experience >= min_value. '
  'KEYWORD_ANY: any term found in CV text / cover letter / current title (relevant domain experience).';

CREATE FUNCTION hiring.normalize_scoring_rule()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.match_terms := ARRAY(
    SELECT DISTINCT lower(btrim(t)) FROM unnest(NEW.match_terms) AS t WHERE btrim(t) <> '' ORDER BY 1);
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;
CREATE TRIGGER scoring_rules_normalize BEFORE INSERT OR UPDATE ON hiring.scoring_rules
  FOR EACH ROW EXECUTE FUNCTION hiring.normalize_scoring_rule();

CREATE FUNCTION hiring.bump_scoring_version()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  UPDATE hiring.scoring_configs
     SET version = version + 1, updated_at = now()
   WHERE job_position_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.job_position_id ELSE NEW.job_position_id END;
  RETURN NULL;
END;
$$;
CREATE TRIGGER scoring_rules_bump_version AFTER INSERT OR UPDATE OR DELETE ON hiring.scoring_rules
  FOR EACH ROW EXECUTE FUNCTION hiring.bump_scoring_version();

CREATE FUNCTION hiring.bump_threshold_version()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF (NEW.shortlist_min_score, NEW.review_min_score) IS DISTINCT FROM (OLD.shortlist_min_score, OLD.review_min_score)
     AND NEW.version = OLD.version THEN
    NEW.version := OLD.version + 1;
  END IF;
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;
CREATE TRIGGER scoring_configs_bump_version BEFORE UPDATE ON hiring.scoring_configs
  FOR EACH ROW EXECUTE FUNCTION hiring.bump_threshold_version();

-- =============================================================================================
-- Onboarding task templates
-- =============================================================================================
CREATE TABLE hiring.onboarding_task_templates (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  task_key         text NOT NULL UNIQUE CHECK (task_key ~ '^[a-z][a-z0-9_]*$'),
  title            text NOT NULL,
  description      text,
  owner_role       text NOT NULL CHECK (owner_role IN ('HR','IT','HIRING_MANAGER','EMPLOYEE')),
  due_offset_days  integer NOT NULL CHECK (due_offset_days BETWEEN -60 AND 180),  -- relative to joining date
  department       text,           -- NULL = applies to every department
  is_active        boolean NOT NULL DEFAULT true,
  sort_order       smallint NOT NULL DEFAULT 100,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER onboarding_task_templates_touch BEFORE UPDATE ON hiring.onboarding_task_templates
  FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();

-- migrate:down
DROP TABLE IF EXISTS hiring.onboarding_task_templates;
DROP TABLE IF EXISTS hiring.scoring_rules;
DROP TABLE IF EXISTS hiring.scoring_configs;
DROP TABLE IF EXISTS hiring.skill_aliases;
DROP TABLE IF EXISTS hiring.job_positions;
DROP TABLE IF EXISTS hiring.staff_members;
DROP TABLE IF EXISTS hiring.settings;
DROP TABLE IF EXISTS hiring.status_transitions;
DROP TABLE IF EXISTS hiring.application_statuses;
DROP FUNCTION IF EXISTS hiring.company_timezone, hiring.setting_text, hiring.setting_bool, hiring.setting_number,
  hiring.setting_interval, hiring.setting_value, hiring.validate_setting, hiring.normalize_scoring_rule,
  hiring.bump_scoring_version, hiring.bump_threshold_version;
