-- migrate:up

-- =============================================================================================
-- Candidates: a person. Identity = normalised email (unique); phone is a secondary signal used to
-- flag possible duplicates for human review (identities are never merged automatically).
-- =============================================================================================
CREATE TABLE hiring.candidates (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  candidate_code    text NOT NULL UNIQUE,
  full_name         text NOT NULL CHECK (btrim(full_name) <> ''),
  email             text CHECK (email = lower(btrim(email)) AND email LIKE '%_@_%'),
  phone_e164        text CHECK (phone_e164 ~ '^\+[1-9][0-9]{6,14}$'),
  city              text,
  linkedin_url      text,
  consent_at        timestamptz,
  anonymized_at     timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CHECK (email IS NOT NULL OR phone_e164 IS NOT NULL OR anonymized_at IS NOT NULL)
);
CREATE UNIQUE INDEX candidates_email_uq ON hiring.candidates (email) WHERE email IS NOT NULL;
CREATE INDEX candidates_phone_idx ON hiring.candidates (phone_e164) WHERE phone_e164 IS NOT NULL;
CREATE TRIGGER candidates_touch BEFORE UPDATE ON hiring.candidates
  FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();

-- =============================================================================================
-- Applications: a candidate applying for a position. Owns the status (state machine).
-- =============================================================================================
CREATE TABLE hiring.applications (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  application_code       text NOT NULL UNIQUE,
  candidate_id           uuid NOT NULL REFERENCES hiring.candidates(id),
  job_position_id        uuid NOT NULL REFERENCES hiring.job_positions(id),
  source_event_id        uuid NOT NULL UNIQUE REFERENCES ops.processed_events(id),
  correlation_id         text NOT NULL UNIQUE,
  source                 text NOT NULL CHECK (source ~ '^[A-Z][A-Z0-9_]*$'),
  status                 text NOT NULL DEFAULT 'NEW' REFERENCES hiring.application_statuses(code),
  status_changed_at      timestamptz NOT NULL DEFAULT now(),
  experience_years       numeric(4,1) CHECK (experience_years BETWEEN 0 AND 60),
  skills                 text[] NOT NULL DEFAULT '{}',
  expected_salary        bigint CHECK (expected_salary > 0),
  salary_currency        char(3) NOT NULL DEFAULT 'PKR',
  available_from         date,
  notice_period_days     smallint CHECK (notice_period_days BETWEEN 0 AND 365),
  current_company        text,
  current_title          text,
  cover_letter           text,
  cv_storage_key         text,
  cv_text                text,
  validation_issues      jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(validation_issues) = 'array'),
  review_reason          text,
  possible_duplicate_of  uuid REFERENCES hiring.candidates(id),
  application_score      numeric(5,2) CHECK (application_score BETWEEN 0 AND 100),
  ai_recommendation      text CHECK (ai_recommendation IN ('SHORTLIST','REVIEW','REJECT')),
  interview_score        numeric(5,2) CHECK (interview_score BETWEEN 0 AND 100),
  final_score            numeric(5,2) CHECK (final_score BETWEEN 0 AND 100),
  closed_at              timestamptz,
  version                integer NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now()
);
-- One ACTIVE application per candidate per position (a closed one does not block re-applying).
CREATE UNIQUE INDEX applications_one_active_per_position_uq
  ON hiring.applications (candidate_id, job_position_id)
  WHERE status NOT IN ('REJECTED','DECLINED','OFFER_EXPIRED','WITHDRAWN','ONBOARDED');
CREATE INDEX applications_status_idx ON hiring.applications (status, status_changed_at);
CREATE INDEX applications_position_status_idx ON hiring.applications (job_position_id, status);
CREATE INDEX applications_candidate_idx ON hiring.applications (candidate_id);
CREATE INDEX applications_created_idx ON hiring.applications (created_at);
CREATE INDEX applications_skills_gin ON hiring.applications USING gin (skills);
CREATE TRIGGER applications_touch BEFORE UPDATE ON hiring.applications
  FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();

-- Status can only be written by hiring.transition_application(), which sets a transaction-local flag.
-- This blocks even ad-hoc UPDATEs from a SQL console from skipping the state machine.
CREATE FUNCTION hiring.guard_application_status()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'NEW' THEN
      PERFORM ops.fail('NT409', 'INVALID_INITIAL_STATUS', 'applications must be created in status NEW');
    END IF;
  ELSIF NEW.status IS DISTINCT FROM OLD.status
        AND current_setting('hiring.allow_status_write', true) IS DISTINCT FROM 'on' THEN
    PERFORM ops.fail('NT403', 'STATUS_CHANGE_FORBIDDEN',
      'application status can only change through api.* functions (state machine)');
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER applications_status_guard BEFORE INSERT OR UPDATE OF status ON hiring.applications
  FOR EACH ROW EXECUTE FUNCTION hiring.guard_application_status();

-- =============================================================================================
-- CandidateStatusHistory: append-only record of every status change and who/what caused it.
-- =============================================================================================
CREATE TABLE hiring.candidate_status_history (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  application_id    uuid NOT NULL REFERENCES hiring.applications(id),
  candidate_id      uuid NOT NULL REFERENCES hiring.candidates(id),
  from_status       text REFERENCES hiring.application_statuses(code),
  to_status         text NOT NULL REFERENCES hiring.application_statuses(code),
  reason            text,
  actor_type        text NOT NULL CHECK (actor_type IN ('SYSTEM','STAFF','CANDIDATE')),
  actor_id          text NOT NULL,
  workflow_name     text,
  workflow_version  text,
  execution_id      text,
  correlation_id    text NOT NULL,
  metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
  changed_at        timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX candidate_status_history_app_idx ON hiring.candidate_status_history (application_id, id);
CREATE INDEX candidate_status_history_changed_idx ON hiring.candidate_status_history (changed_at, to_status);
CREATE INDEX candidate_status_history_correlation_idx ON hiring.candidate_status_history (correlation_id);
CREATE TRIGGER candidate_status_history_append_only BEFORE UPDATE OR DELETE ON hiring.candidate_status_history
  FOR EACH ROW EXECUTE FUNCTION ops.forbid_mutation();

-- =============================================================================================
-- Screening results
-- =============================================================================================
CREATE TABLE hiring.application_scores (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id       uuid NOT NULL REFERENCES hiring.applications(id),
  scoring_version      integer NOT NULL CHECK (scoring_version >= 1),
  input_hash           text NOT NULL,   -- hash of the scored inputs; a replay with identical inputs is a no-op
  points_awarded       integer NOT NULL CHECK (points_awarded >= 0),
  points_possible      integer NOT NULL CHECK (points_possible > 0),
  score                numeric(5,2) NOT NULL CHECK (score BETWEEN 0 AND 100),
  route                text NOT NULL CHECK (route IN ('SHORTLIST','REVIEW','REJECT')),
  shortlist_min_score  numeric(5,2) NOT NULL,
  review_min_score     numeric(5,2) NOT NULL,
  breakdown            jsonb NOT NULL CHECK (jsonb_typeof(breakdown) = 'array'),
  correlation_id       text NOT NULL,
  created_at           timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT application_scores_input_uq UNIQUE (application_id, scoring_version, input_hash),
  CHECK (points_awarded <= points_possible)
);
COMMENT ON COLUMN hiring.application_scores.breakdown IS 'Per-rule explanation: rule_key, label, points, matched terms.';
CREATE INDEX application_scores_application_idx ON hiring.application_scores (application_id, created_at);

CREATE TABLE hiring.ai_analyses (
  id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id            uuid NOT NULL REFERENCES hiring.applications(id),
  status                    text NOT NULL CHECK (status IN ('COMPLETED','FALLBACK')),
  provider                  text,
  model                     text,
  prompt_version            text NOT NULL,
  input_hash                text NOT NULL,
  technical_strength        smallint CHECK (technical_strength BETWEEN 0 AND 10),
  experience_relevance      smallint CHECK (experience_relevance BETWEEN 0 AND 10),
  communication_indication  smallint CHECK (communication_indication BETWEEN 0 AND 10),
  missing_skills            text[] NOT NULL DEFAULT '{}',
  summary                   text CHECK (char_length(summary) <= 1000),
  recommendation            text CHECK (recommendation IN ('SHORTLIST','REVIEW','REJECT')),
  fallback_reason           text,
  attempts                  smallint NOT NULL DEFAULT 1 CHECK (attempts BETWEEN 0 AND 5),
  latency_ms                integer CHECK (latency_ms >= 0),
  trace_id                  text,
  correlation_id            text NOT NULL,
  created_at                timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ai_analyses_input_uq UNIQUE (application_id, prompt_version, input_hash),
  CHECK (
    (status = 'COMPLETED' AND recommendation IS NOT NULL AND technical_strength IS NOT NULL
       AND experience_relevance IS NOT NULL AND communication_indication IS NOT NULL AND summary IS NOT NULL)
    OR (status = 'FALLBACK' AND fallback_reason IS NOT NULL)
  )
);
COMMENT ON TABLE hiring.ai_analyses IS 'Advisory AI output. Never used as the actor of a decision.';
CREATE INDEX ai_analyses_application_idx ON hiring.ai_analyses (application_id, created_at);

-- =============================================================================================
-- Interviews
-- =============================================================================================
CREATE TABLE hiring.interview_slots (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  interviewer_id  uuid NOT NULL REFERENCES hiring.staff_members(id),
  starts_at       timestamptz NOT NULL,
  ends_at         timestamptz NOT NULL,
  status          text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','BOOKED','BLOCKED')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CHECK (ends_at > starts_at),
  -- An interviewer can never have two overlapping slots.
  CONSTRAINT interview_slots_no_overlap
    EXCLUDE USING gist (interviewer_id WITH =, tstzrange(starts_at, ends_at) WITH &&)
);
CREATE INDEX interview_slots_open_idx ON hiring.interview_slots (starts_at) WHERE status = 'OPEN';
CREATE TRIGGER interview_slots_touch BEFORE UPDATE ON hiring.interview_slots
  FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();

CREATE TABLE hiring.interviews (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  interview_code     text NOT NULL UNIQUE,
  application_id     uuid NOT NULL REFERENCES hiring.applications(id),
  round              smallint NOT NULL DEFAULT 1 CHECK (round BETWEEN 1 AND 10),
  interviewer_id     uuid NOT NULL REFERENCES hiring.staff_members(id),
  slot_id            uuid REFERENCES hiring.interview_slots(id),
  status             text NOT NULL DEFAULT 'INVITED'
                       CHECK (status IN ('INVITED','CONFIRMED','COMPLETED','NO_SHOW','CANCELLED','EXPIRED')),
  mode               text NOT NULL DEFAULT 'ONLINE' CHECK (mode IN ('ONLINE','ONSITE')),
  scheduled_start    timestamptz,
  scheduled_end      timestamptz,
  meeting_url        text,
  calendar_event_id  text,
  invited_at         timestamptz NOT NULL DEFAULT now(),
  confirmed_at       timestamptz,
  completed_at       timestamptz,
  cancelled_at       timestamptz,
  cancel_reason      text,
  correlation_id     text NOT NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT interviews_round_uq UNIQUE (application_id, round),
  CHECK (scheduled_end IS NULL OR scheduled_end > scheduled_start),
  CHECK (status NOT IN ('CONFIRMED','COMPLETED') OR (slot_id IS NOT NULL AND scheduled_start IS NOT NULL))
);
-- A slot can hold only one live booking.
CREATE UNIQUE INDEX interviews_one_booking_per_slot_uq
  ON hiring.interviews (slot_id) WHERE slot_id IS NOT NULL AND status IN ('CONFIRMED','COMPLETED');
CREATE INDEX interviews_status_idx ON hiring.interviews (status, scheduled_end);
CREATE INDEX interviews_interviewer_idx ON hiring.interviews (interviewer_id, status);
CREATE TRIGGER interviews_touch BEFORE UPDATE ON hiring.interviews
  FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();

CREATE TABLE hiring.interview_feedback (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  interview_id      uuid NOT NULL UNIQUE REFERENCES hiring.interviews(id),
  interviewer_id    uuid NOT NULL REFERENCES hiring.staff_members(id),
  technical_skills  smallint NOT NULL CHECK (technical_skills BETWEEN 1 AND 5),
  communication     smallint NOT NULL CHECK (communication BETWEEN 1 AND 5),
  problem_solving   smallint NOT NULL CHECK (problem_solving BETWEEN 1 AND 5),
  experience        smallint NOT NULL CHECK (experience BETWEEN 1 AND 5),
  team_fit          smallint NOT NULL CHECK (team_fit BETWEEN 1 AND 5),
  -- Percentage of the maximum (25 points): 5 criteria x 5.
  interview_score   numeric(5,2) GENERATED ALWAYS AS (
                      round((technical_skills + communication + problem_solving + experience + team_fit)::numeric
                            / 25 * 100, 2)) STORED,
  recommendation    text NOT NULL CHECK (recommendation IN ('STRONG_HIRE','HIRE','NO_HIRE','STRONG_NO_HIRE')),
  comments          text NOT NULL CHECK (btrim(comments) <> ''),
  correlation_id    text NOT NULL,
  submitted_at      timestamptz NOT NULL DEFAULT now()
);

-- =============================================================================================
-- Offers and approvals
-- =============================================================================================
CREATE TABLE hiring.offers (
  id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  offer_code                  text NOT NULL UNIQUE,
  application_id              uuid NOT NULL REFERENCES hiring.applications(id),
  revision                    smallint NOT NULL DEFAULT 1 CHECK (revision BETWEEN 1 AND 20),
  status                      text NOT NULL DEFAULT 'PENDING_APPROVAL' CHECK (status IN (
                                'PENDING_APPROVAL','APPROVED','REJECTED_BY_APPROVER','SENT','ACCEPTED',
                                'DECLINED','NEGOTIATION','EXPIRED','SUPERSEDED','WITHDRAWN')),
  job_position_id             uuid NOT NULL REFERENCES hiring.job_positions(id),
  department                  text NOT NULL,
  monthly_salary              bigint NOT NULL CHECK (monthly_salary > 0),
  currency                    char(3) NOT NULL,
  joining_date                date NOT NULL,
  probation_months            smallint NOT NULL CHECK (probation_months BETWEEN 0 AND 12),
  reporting_manager_id        uuid NOT NULL REFERENCES hiring.staff_members(id),
  required_approval_levels    smallint NOT NULL CHECK (required_approval_levels IN (1, 2)),
  approval_threshold_applied  bigint NOT NULL,
  document_storage_key        text,
  sent_at                     timestamptz,
  expires_at                  timestamptz,
  responded_at                timestamptz,
  candidate_response          text CHECK (candidate_response IN ('ACCEPT','DECLINE','NEGOTIATE')),
  candidate_message           text,
  created_by                  text NOT NULL,
  correlation_id              text NOT NULL,
  created_at                  timestamptz NOT NULL DEFAULT now(),
  updated_at                  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT offers_revision_uq UNIQUE (application_id, revision),
  CHECK (status NOT IN ('SENT','ACCEPTED','DECLINED','NEGOTIATION','EXPIRED')
         OR (sent_at IS NOT NULL AND expires_at IS NOT NULL AND expires_at > sent_at))
);
-- At most one open offer per application: protects offer creation from repeated execution.
CREATE UNIQUE INDEX offers_one_open_per_application_uq
  ON hiring.offers (application_id) WHERE status IN ('PENDING_APPROVAL','APPROVED','SENT','NEGOTIATION');
CREATE INDEX offers_status_idx ON hiring.offers (status, expires_at);
CREATE TRIGGER offers_touch BEFORE UPDATE ON hiring.offers
  FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();

CREATE TABLE hiring.offer_approvals (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  offer_id        uuid NOT NULL REFERENCES hiring.offers(id),
  level           smallint NOT NULL CHECK (level IN (1, 2)),
  approver_id     uuid NOT NULL REFERENCES hiring.staff_members(id),
  decision        text NOT NULL CHECK (decision IN ('APPROVED','REJECTED')),
  reason          text,
  correlation_id  text NOT NULL,
  decided_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT offer_approvals_level_uq UNIQUE (offer_id, level),
  -- Segregation of duties: one person cannot approve both levels.
  CONSTRAINT offer_approvals_approver_uq UNIQUE (offer_id, approver_id),
  CHECK (decision = 'APPROVED' OR (reason IS NOT NULL AND btrim(reason) <> ''))
);
CREATE TRIGGER offer_approvals_append_only BEFORE UPDATE OR DELETE ON hiring.offer_approvals
  FOR EACH ROW EXECUTE FUNCTION ops.forbid_mutation();

-- =============================================================================================
-- Employees and onboarding
-- =============================================================================================
CREATE TABLE hiring.employees (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  employee_code           text NOT NULL UNIQUE CHECK (employee_code ~ '^[A-Z]{2,5}-[0-9]{4}-[0-9]{3,}$'),
  candidate_id            uuid NOT NULL UNIQUE REFERENCES hiring.candidates(id),
  application_id          uuid NOT NULL UNIQUE REFERENCES hiring.applications(id),
  offer_id                uuid NOT NULL UNIQUE REFERENCES hiring.offers(id),   -- employee created exactly once per offer
  full_name               text NOT NULL,
  personal_email          text,
  company_email           text NOT NULL UNIQUE,
  job_position_id         uuid NOT NULL REFERENCES hiring.job_positions(id),
  department              text NOT NULL,
  reporting_manager_id    uuid NOT NULL REFERENCES hiring.staff_members(id),
  joining_date            date NOT NULL,
  probation_end_date      date,
  status                  text NOT NULL DEFAULT 'ONBOARDING' CHECK (status IN ('ONBOARDING','ACTIVE','TERMINATED')),
  account_provisioned_at  timestamptz,
  onboarded_at            timestamptz,
  correlation_id          text NOT NULL,
  created_at              timestamptz NOT NULL DEFAULT now(),
  updated_at              timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER employees_touch BEFORE UPDATE ON hiring.employees
  FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();

CREATE TABLE hiring.onboarding_tasks (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  employee_id       uuid NOT NULL REFERENCES hiring.employees(id),
  template_id       uuid REFERENCES hiring.onboarding_task_templates(id),
  task_key          text NOT NULL,
  title             text NOT NULL,
  owner_role        text NOT NULL CHECK (owner_role IN ('HR','IT','HIRING_MANAGER','EMPLOYEE')),
  assignee_id       uuid REFERENCES hiring.staff_members(id),
  due_date          date NOT NULL,
  status            text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','IN_PROGRESS','DONE','CANCELLED')),
  completed_at      timestamptz,
  completed_by      text,
  last_reminder_at  timestamptz,
  reminder_count    smallint NOT NULL DEFAULT 0 CHECK (reminder_count >= 0),
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT onboarding_tasks_employee_task_uq UNIQUE (employee_id, task_key),
  CHECK ((status = 'DONE') = (completed_at IS NOT NULL))
);
CREATE INDEX onboarding_tasks_open_due_idx ON hiring.onboarding_tasks (due_date)
  WHERE status IN ('PENDING','IN_PROGRESS');
CREATE TRIGGER onboarding_tasks_touch BEFORE UPDATE ON hiring.onboarding_tasks
  FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();

-- migrate:down
DROP TABLE IF EXISTS hiring.onboarding_tasks;
DROP TABLE IF EXISTS hiring.employees;
DROP TABLE IF EXISTS hiring.offer_approvals;
DROP TABLE IF EXISTS hiring.offers;
DROP TABLE IF EXISTS hiring.interview_feedback;
DROP TABLE IF EXISTS hiring.interviews;
DROP TABLE IF EXISTS hiring.interview_slots;
DROP TABLE IF EXISTS hiring.ai_analyses;
DROP TABLE IF EXISTS hiring.application_scores;
DROP TABLE IF EXISTS hiring.candidate_status_history;
DROP TABLE IF EXISTS hiring.applications;
DROP TABLE IF EXISTS hiring.candidates;
DROP FUNCTION IF EXISTS hiring.guard_application_status();
