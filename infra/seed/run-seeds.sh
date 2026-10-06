#!/bin/sh
# Applies configuration seeds after migrations. Every step is idempotent and runs on every start.
#   SEED_DEMO_DATA=true    -> load the NovaTech configuration (db/seed/0*.sql: positions, scoring rules, sample staff)
#   DEMO_FAST_TIMERS=true  -> compress reminder/expiry delays to minutes (demo only)
#   /config/staff.csv      -> the real staff (config/README.md); replaces the sample staff by id
#   MAIL_FROM_ADDRESS=...  -> the mailbox that sends candidate email (company.careers_email); must match the SMTP login
#   OPS_ALERT_EMAIL=...    -> where automation error digests go (ops.alert_email)
#   APP_ENV, FAULT_INJECTION_ENABLED -> dev.fault_injection_enabled is only ever on outside production
set -eu

: "${DATABASE_URL:?DATABASE_URL is required}"

if [ "${SEED_DEMO_DATA:-true}" = "true" ]; then
  for file in /seed/0*.sql; do
    echo "seed: applying $(basename "$file")"
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 --quiet --file "$file"
  done
  if [ "${DEMO_FAST_TIMERS:-false}" = "true" ]; then
    echo "seed: DEMO_FAST_TIMERS=true, compressing delays to minutes"
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 --quiet --file /seed/optional/fast_timers.sql
  fi
else
  echo "seed: SEED_DEMO_DATA is not true, skipping demo configuration"
fi

if [ -f /config/staff.csv ]; then
  echo "seed: staff from config/staff.csv"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 --quiet <<'EOSQL'
BEGIN;
CREATE TEMP TABLE staff_in (id uuid, full_name text, email text, department text, job_title text, roles text,
                            active text) ON COMMIT DROP;
\copy staff_in FROM '/config/staff.csv' WITH (FORMAT csv, HEADER true)
INSERT INTO hiring.staff_members AS s (id, full_name, email, department, job_title, roles, is_active)
SELECT id, btrim(full_name), lower(btrim(email)), nullif(btrim(department), ''), nullif(btrim(job_title), ''),
       string_to_array(upper(regexp_replace(roles, '\s', '', 'g')), '|'),
       coalesce(lower(btrim(active)), 'true') <> 'false'
  FROM staff_in
ON CONFLICT (id) DO UPDATE
  SET full_name = EXCLUDED.full_name, email = EXCLUDED.email, department = EXCLUDED.department,
      job_title = EXCLUDED.job_title, roles = EXCLUDED.roles, is_active = EXCLUDED.is_active;
COMMIT;
EOSQL
fi

set_text() {  # $1 = setting key, $2 = value, $3 = source shown in updated_by
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 --quiet -v key="$1" -v value="$2" -v source="$3" <<'EOSQL'
UPDATE hiring.settings
   SET value = to_jsonb(:'value'::text), updated_at = now(), updated_by = 'seed: ' || :'source'
 WHERE key = :'key' AND value IS DISTINCT FROM to_jsonb(:'value'::text);
EOSQL
}

if [ -n "${MAIL_FROM_ADDRESS:-}" ]; then
  echo "seed: sender address set from MAIL_FROM_ADDRESS"
  set_text company.careers_email "$MAIL_FROM_ADDRESS" MAIL_FROM_ADDRESS
fi
if [ -n "${OPS_ALERT_EMAIL:-}" ]; then
  echo "seed: alert address set from OPS_ALERT_EMAIL"
  set_text ops.alert_email "$OPS_ALERT_EMAIL" OPS_ALERT_EMAIL
fi

# Fault injection lets the intake carry test failures to later steps; it is never on in production.
fault=false
if [ "${APP_ENV:-development}" != "production" ] && [ "${FAULT_INJECTION_ENABLED:-false}" = "true" ]; then fault=true; fi
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 --quiet -v fault="$fault" <<'EOSQL'
UPDATE hiring.settings
   SET value = to_jsonb(:'fault'::boolean), updated_at = now(), updated_by = 'seed: APP_ENV/FAULT_INJECTION_ENABLED'
 WHERE key = 'dev.fault_injection_enabled' AND value IS DISTINCT FROM to_jsonb(:'fault'::boolean);
EOSQL

echo "seed: done"
