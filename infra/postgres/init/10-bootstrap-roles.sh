#!/bin/sh
# Runs once, when the business database volume is first initialised (docker-entrypoint-initdb.d).
# Creates the three login roles used by the system. Passwords come from the environment, never from files.
#
#   novatech_owner : owns the database and every object; used only by migrations/seeding
#   n8n_app        : runtime role for n8n  (SELECT on tables, EXECUTE on api.* functions)
#   backend_app    : runtime role for FastAPI (same privileges as n8n_app)
#
# For managed Postgres (e.g. Supabase) run db/bootstrap/roles.sql manually instead.
set -eu

: "${NOVATECH_OWNER_PASSWORD:?NOVATECH_OWNER_PASSWORD is required}"
: "${NOVATECH_N8N_DB_PASSWORD:?NOVATECH_N8N_DB_PASSWORD is required}"
: "${NOVATECH_BACKEND_DB_PASSWORD:?NOVATECH_BACKEND_DB_PASSWORD is required}"

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  -v dbname="$POSTGRES_DB" \
  -v owner_password="$NOVATECH_OWNER_PASSWORD" \
  -v n8n_password="$NOVATECH_N8N_DB_PASSWORD" \
  -v backend_password="$NOVATECH_BACKEND_DB_PASSWORD" <<'EOSQL'
CREATE ROLE novatech_owner LOGIN PASSWORD :'owner_password';
CREATE ROLE n8n_app       LOGIN PASSWORD :'n8n_password';
CREATE ROLE backend_app   LOGIN PASSWORD :'backend_password';

ALTER DATABASE :"dbname" OWNER TO novatech_owner;
REVOKE ALL ON DATABASE :"dbname" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"dbname" TO novatech_owner, n8n_app, backend_app;

-- Runtime roles fail fast instead of holding locks forever.
ALTER ROLE n8n_app     SET statement_timeout = '30s';
ALTER ROLE backend_app SET statement_timeout = '30s';
ALTER ROLE n8n_app     SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE backend_app SET idle_in_transaction_session_timeout = '60s';
EOSQL

echo "novatech: roles bootstrapped"
