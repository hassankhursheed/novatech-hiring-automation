#!/bin/sh
# Applies configuration seeds after migrations. Every seed file is idempotent.
#   SEED_DEMO_DATA=true    -> load the NovaTech demo configuration (db/seed/0*.sql)
#   DEMO_FAST_TIMERS=true  -> compress reminder/expiry delays to minutes (demo only)
#   MAIL_FROM_ADDRESS=...  -> the mailbox that sends candidate email (company.careers_email); must match the SMTP login
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

if [ -n "${MAIL_FROM_ADDRESS:-}" ]; then
  echo "seed: sender address set from MAIL_FROM_ADDRESS"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 --quiet -v address="$MAIL_FROM_ADDRESS" <<'EOSQL'
UPDATE hiring.settings
   SET value = to_jsonb(:'address'::text), updated_at = now(), updated_by = 'seed: MAIL_FROM_ADDRESS'
 WHERE key = 'company.careers_email' AND value IS DISTINCT FROM to_jsonb(:'address'::text);
EOSQL
fi

echo "seed: done"
