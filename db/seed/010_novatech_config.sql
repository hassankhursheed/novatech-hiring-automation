-- NovaTech Solutions demo configuration (fictional people and data).
-- Idempotent: safe to run repeatedly. A real company replaces this file with its own configuration.
BEGIN;

-- ---------------------------------------------------------------------------------------------
-- Staff (fixed ids so docs, tests and portal fixtures can reference them)
-- ---------------------------------------------------------------------------------------------
INSERT INTO hiring.staff_members (id, full_name, email, department, job_title, roles) VALUES
  ('00000000-0000-4000-8000-000000000001', 'Sana Malik',     'sana.malik@novatech.example',     'People', 'HR Manager',            '{HR_ADMIN,RECRUITER}'),
  ('00000000-0000-4000-8000-000000000002', 'Bilal Hussain',  'bilal.hussain@novatech.example',  'People', 'Talent Acquisition',    '{RECRUITER}'),
  ('00000000-0000-4000-8000-000000000003', 'Ayesha Khan',    'ayesha.khan@novatech.example',    'Engineering', 'Engineering Manager', '{HIRING_MANAGER,INTERVIEWER,APPROVER_L1}'),
  ('00000000-0000-4000-8000-000000000004', 'Usman Tariq',    'usman.tariq@novatech.example',    'Engineering', 'Senior Python Engineer', '{INTERVIEWER}'),
  ('00000000-0000-4000-8000-000000000005', 'Fatima Zahra',   'fatima.zahra@novatech.example',   'Engineering', 'QA Lead',             '{INTERVIEWER}'),
  ('00000000-0000-4000-8000-000000000006', 'Hamza Qureshi',  'hamza.qureshi@novatech.example',  'Sales', 'Sales Director',            '{HIRING_MANAGER,INTERVIEWER,APPROVER_L1}'),
  ('00000000-0000-4000-8000-000000000007', 'Omar Farooq',    'omar.farooq@novatech.example',    'Finance', 'Chief Financial Officer', '{APPROVER_L2}'),
  ('00000000-0000-4000-8000-000000000008', 'Zainab Ali',     'zainab.ali@novatech.example',     'IT', 'IT Administrator',             '{IT_ADMIN}')
ON CONFLICT (id) DO UPDATE
  SET full_name = EXCLUDED.full_name, email = EXCLUDED.email, department = EXCLUDED.department,
      job_title = EXCLUDED.job_title, roles = EXCLUDED.roles;

-- ---------------------------------------------------------------------------------------------
-- Positions
-- ---------------------------------------------------------------------------------------------
INSERT INTO hiring.job_positions (id, code, title, department, min_experience_years, salary_min, salary_max,
                                  currency, hiring_manager_id, default_interviewer_id, description) VALUES
  ('10000000-0000-4000-8000-000000000001', 'PY_DEV', 'Python Developer', 'Engineering', 1, 120000, 350000, 'PKR',
   '00000000-0000-4000-8000-000000000003', '00000000-0000-4000-8000-000000000004',
   'Backend engineer building APIs and data services with Python, FastAPI/Django and PostgreSQL.'),
  ('10000000-0000-4000-8000-000000000002', 'BDE', 'Business Development Executive', 'Sales', 1, 70000, 200000, 'PKR',
   '00000000-0000-4000-8000-000000000006', '00000000-0000-4000-8000-000000000006',
   'Generates and qualifies B2B leads for NovaTech software services and manages the sales pipeline.'),
  ('10000000-0000-4000-8000-000000000003', 'QA_ENG', 'QA Engineer', 'Engineering', 1, 90000, 250000, 'PKR',
   '00000000-0000-4000-8000-000000000003', '00000000-0000-4000-8000-000000000005',
   'Owns manual and automated testing of web applications and APIs.')
ON CONFLICT (id) DO UPDATE
  SET code = EXCLUDED.code, title = EXCLUDED.title, department = EXCLUDED.department,
      min_experience_years = EXCLUDED.min_experience_years, salary_min = EXCLUDED.salary_min,
      salary_max = EXCLUDED.salary_max, hiring_manager_id = EXCLUDED.hiring_manager_id,
      default_interviewer_id = EXCLUDED.default_interviewer_id, description = EXCLUDED.description;

-- ---------------------------------------------------------------------------------------------
-- Scoring: thresholds per position (score is normalised to 0-100)
-- ---------------------------------------------------------------------------------------------
INSERT INTO hiring.scoring_configs (job_position_id, shortlist_min_score, review_min_score, updated_by) VALUES
  ('10000000-0000-4000-8000-000000000001', 80, 60, 'seed'),
  ('10000000-0000-4000-8000-000000000002', 75, 55, 'seed'),
  ('10000000-0000-4000-8000-000000000003', 80, 60, 'seed')
ON CONFLICT (job_position_id) DO NOTHING;

-- Rules: points per criterion. Python Developer mirrors the brief (sums to 100).
INSERT INTO hiring.scoring_rules (job_position_id, rule_key, label, rule_type, match_terms, min_value, points, sort_order)
VALUES
  -- Python Developer
  ('10000000-0000-4000-8000-000000000001', 'python',        'Python',                     'SKILL_ANY', '{python}',                         NULL, 20, 10),
  ('10000000-0000-4000-8000-000000000001', 'web_framework', 'FastAPI/Django',             'SKILL_ANY', '{fastapi,django}',                 NULL, 15, 20),
  ('10000000-0000-4000-8000-000000000001', 'sql',           'SQL',                        'SKILL_ANY', '{sql,postgresql,mysql,sql server}',NULL, 10, 30),
  ('10000000-0000-4000-8000-000000000001', 'rest_apis',     'REST APIs',                  'SKILL_ANY', '{rest apis}',                      NULL, 10, 40),
  ('10000000-0000-4000-8000-000000000001', 'git',           'Git',                        'SKILL_ANY', '{git}',                            NULL,  5, 50),
  ('10000000-0000-4000-8000-000000000001', 'docker',        'Docker',                     'SKILL_ANY', '{docker}',                         NULL, 10, 60),
  ('10000000-0000-4000-8000-000000000001', 'cloud',         'AWS/Azure',                  'SKILL_ANY', '{aws,azure,gcp}',                  NULL, 10, 70),
  ('10000000-0000-4000-8000-000000000001', 'experience',    '3+ years experience',        'MIN_EXPERIENCE_YEARS', '{}',                   3, 10, 80),
  ('10000000-0000-4000-8000-000000000001', 'domain',        'Relevant domain experience', 'KEYWORD_ANY', '{fintech,saas,e-commerce,payments,healthtech,logistics}', NULL, 10, 90),
  -- Business Development Executive
  ('10000000-0000-4000-8000-000000000002', 'b2b_sales',       'B2B sales',                'SKILL_ANY', '{b2b sales,sales}',                 NULL, 20, 10),
  ('10000000-0000-4000-8000-000000000002', 'lead_generation', 'Lead generation',          'SKILL_ANY', '{lead generation,prospecting,cold calling}', NULL, 15, 20),
  ('10000000-0000-4000-8000-000000000002', 'negotiation',     'Negotiation',              'SKILL_ANY', '{negotiation}',                     NULL, 15, 30),
  ('10000000-0000-4000-8000-000000000002', 'crm',             'CRM tools',                'SKILL_ANY', '{crm,salesforce,hubspot,zoho crm}', NULL, 10, 40),
  ('10000000-0000-4000-8000-000000000002', 'communication',   'Communication/presentation','SKILL_ANY', '{communication,presentation}',     NULL, 10, 50),
  ('10000000-0000-4000-8000-000000000002', 'market_research', 'Market research',          'SKILL_ANY', '{market research}',                 NULL, 10, 60),
  ('10000000-0000-4000-8000-000000000002', 'experience',      '2+ years experience',      'MIN_EXPERIENCE_YEARS', '{}',                    2, 10, 70),
  ('10000000-0000-4000-8000-000000000002', 'domain',          'Software/IT services sales','KEYWORD_ANY', '{saas,software,it services,technology}', NULL, 10, 80),
  -- QA Engineer
  ('10000000-0000-4000-8000-000000000003', 'manual_testing', 'Manual testing',            'SKILL_ANY', '{manual testing}',                  NULL, 15, 10),
  ('10000000-0000-4000-8000-000000000003', 'automation',     'Test automation',           'SKILL_ANY', '{selenium,cypress,playwright}',     NULL, 20, 20),
  ('10000000-0000-4000-8000-000000000003', 'test_design',    'Test design/planning',      'SKILL_ANY', '{test cases,test planning}',        NULL, 10, 30),
  ('10000000-0000-4000-8000-000000000003', 'api_testing',    'API testing',               'SKILL_ANY', '{api testing,postman}',             NULL, 10, 40),
  ('10000000-0000-4000-8000-000000000003', 'sql',            'SQL',                       'SKILL_ANY', '{sql,postgresql,mysql}',            NULL, 10, 50),
  ('10000000-0000-4000-8000-000000000003', 'bug_tracking',   'Bug tracking (Jira)',       'SKILL_ANY', '{jira}',                            NULL,  5, 60),
  ('10000000-0000-4000-8000-000000000003', 'ci_cd',          'CI/CD',                     'SKILL_ANY', '{ci/cd,jenkins,github actions}',    NULL, 10, 70),
  ('10000000-0000-4000-8000-000000000003', 'experience',     '2+ years experience',       'MIN_EXPERIENCE_YEARS', '{}',                    2, 10, 80),
  ('10000000-0000-4000-8000-000000000003', 'domain',         'Agile/SaaS delivery',       'KEYWORD_ANY', '{agile,scrum,saas}',              NULL, 10, 90)
ON CONFLICT (job_position_id, rule_key) DO NOTHING;

-- ---------------------------------------------------------------------------------------------
-- Skill vocabulary (alias -> canonical). Canonical names are what scoring rules match on.
-- ---------------------------------------------------------------------------------------------
INSERT INTO hiring.skill_aliases (alias, canonical) VALUES
  ('py', 'python'), ('python3', 'python'), ('python 3', 'python'),
  ('fast api', 'fastapi'), ('django rest framework', 'django'), ('drf', 'django'),
  ('postgres', 'postgresql'), ('psql', 'postgresql'), ('postgre sql', 'postgresql'), ('ms sql', 'sql server'),
  ('mssql', 'sql server'), ('structured query language', 'sql'),
  ('rest', 'rest apis'), ('rest api', 'rest apis'), ('restful', 'rest apis'), ('restful apis', 'rest apis'),
  ('restful api', 'rest apis'), ('rest services', 'rest apis'),
  ('github', 'git'), ('gitlab', 'git'), ('version control', 'git'),
  ('docker compose', 'docker'), ('containers', 'docker'),
  ('amazon web services', 'aws'), ('microsoft azure', 'azure'), ('google cloud', 'gcp'), ('google cloud platform', 'gcp'),
  ('business development', 'b2b sales'), ('b2b', 'b2b sales'), ('enterprise sales', 'b2b sales'),
  ('lead gen', 'lead generation'), ('outbound prospecting', 'prospecting'),
  ('negotiations', 'negotiation'), ('deal closing', 'negotiation'),
  ('salesforce crm', 'salesforce'), ('hubspot crm', 'hubspot'),
  ('presentations', 'presentation'), ('public speaking', 'presentation'), ('communication skills', 'communication'),
  ('manual qa', 'manual testing'), ('functional testing', 'manual testing'),
  ('selenium webdriver', 'selenium'), ('playwright test', 'playwright'),
  ('test case design', 'test cases'), ('test plans', 'test planning'),
  ('rest assured', 'api testing'), ('postman api', 'postman'),
  ('jira software', 'jira'), ('ci cd', 'ci/cd'), ('cicd', 'ci/cd'), ('continuous integration', 'ci/cd'),
  ('gh actions', 'github actions')
ON CONFLICT (alias) DO UPDATE SET canonical = EXCLUDED.canonical;

-- ---------------------------------------------------------------------------------------------
-- Onboarding task templates (due dates relative to the joining date)
-- ---------------------------------------------------------------------------------------------
INSERT INTO hiring.onboarding_task_templates (task_key, title, description, owner_role, due_offset_days, sort_order) VALUES
  ('collect_documents',  'Collect signed contract and documents', 'Signed offer, CNIC copy, degrees, bank details.', 'HR',             -3, 10),
  ('create_accounts',    'Create company email and system accounts', 'Email, chat, HRIS and repository access.',   'IT',             -2, 20),
  ('prepare_equipment',  'Prepare laptop and equipment',           'Laptop imaging, accessories, access card.',    'IT',             -1, 30),
  ('welcome_pack',       'Send welcome email and first-day agenda','Arrival time, dress code, first-day schedule.', 'HR',              0, 40),
  ('orientation',        'Company orientation session',           'Policies, benefits, culture and tools.',       'HR',              0, 50),
  ('assign_buddy',       'Assign onboarding buddy',               'A teammate for the first month.',              'HIRING_MANAGER',  0, 60),
  ('team_introduction',  'Team introduction meeting',             'Meet the team and key stakeholders.',          'HIRING_MANAGER',  1, 70),
  ('security_training',  'Complete security and compliance training','Mandatory information-security course.',    'EMPLOYEE',        5, 80),
  ('first_week_checkin', 'First-week check-in',                   '1:1 with the reporting manager.',              'HIRING_MANAGER',  7, 90),
  ('probation_goals',    'Agree probation goals',                 'Documented goals for the probation period.',   'HIRING_MANAGER', 14, 100)
ON CONFLICT (task_key) DO UPDATE
  SET title = EXCLUDED.title, description = EXCLUDED.description, owner_role = EXCLUDED.owner_role,
      due_offset_days = EXCLUDED.due_offset_days, sort_order = EXCLUDED.sort_order;

-- ---------------------------------------------------------------------------------------------
-- Interview availability: weekly hours per interviewer (Monday-Friday, 45-minute interviews, company timezone).
-- Slots for the next interview.availability_days days are generated from it here and whenever an invitation
-- is sent. Overlaps are impossible (exclusion constraint); re-running only adds missing slots.
-- ---------------------------------------------------------------------------------------------
INSERT INTO hiring.interviewer_availability (interviewer_id, isodow, start_time, duration_minutes)
SELECT i.interviewer_id, d.isodow, t.slot_time, 45
  FROM (VALUES ('00000000-0000-4000-8000-000000000003'::uuid), ('00000000-0000-4000-8000-000000000004'::uuid),
               ('00000000-0000-4000-8000-000000000005'::uuid), ('00000000-0000-4000-8000-000000000006'::uuid))
       AS i(interviewer_id)
 CROSS JOIN generate_series(1, 5) AS d(isodow)
 CROSS JOIN (VALUES (time '11:00'), (time '12:00'), (time '14:30'), (time '15:30'), (time '16:30')) AS t(slot_time)
ON CONFLICT DO NOTHING;

DO $$ BEGIN PERFORM hiring.ensure_interview_slots(NULL); END $$;

COMMIT;
