#!/bin/sh
# Applies configuration seeds after migrations. Every seed file is idempotent.
#   SEED_DEMO_DATA=true    -> load the NovaTech demo configuration (db/seed/0*.sql)
#   DEMO_FAST_TIMERS=true  -> compress reminder/expiry delays to minutes (demo only)
set -eu

: "${DATABASE_URL:?DATABASE_URL is required}"

if [ "${SEED_DEMO_DATA:-true}" != "true" ]; then
  echo "seed: SEED_DEMO_DATA is not true, skipping demo configuration"
  exit 0
fi

for file in /seed/0*.sql; do
  echo "seed: applying $(basename "$file")"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 --quiet --file "$file"
done

if [ "${DEMO_FAST_TIMERS:-false}" = "true" ]; then
  echo "seed: DEMO_FAST_TIMERS=true, compressing delays to minutes"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 --quiet --file /seed/optional/fast_timers.sql
fi

echo "seed: done"
