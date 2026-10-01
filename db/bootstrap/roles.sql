-- Manual bootstrap for managed Postgres (Supabase, Cloud SQL, RDS).
-- Run ONCE as an admin role before `dbmate up`. Replace the placeholders with values from your secret manager;
-- do not commit real passwords.
--
-- Local Docker does this automatically via infra/postgres/init/10-bootstrap-roles.sh.

CREATE ROLE novatech_owner LOGIN PASSWORD '<owner-password>';
CREATE ROLE n8n_app        LOGIN PASSWORD '<n8n-password>';
CREATE ROLE backend_app    LOGIN PASSWORD '<backend-password>';

-- On Supabase the migration owner must be able to create schemas in the existing database:
GRANT CREATE ON DATABASE postgres TO novatech_owner;
GRANT CONNECT ON DATABASE postgres TO n8n_app, backend_app;

ALTER ROLE n8n_app     SET statement_timeout = '30s';
ALTER ROLE backend_app SET statement_timeout = '30s';
ALTER ROLE n8n_app     SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE backend_app SET idle_in_transaction_session_timeout = '60s';
