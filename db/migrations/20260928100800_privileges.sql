-- migrate:up

-- Least privilege for runtime roles (n8n_app, backend_app):
--   * read everything they need to render messages and re-check state
--   * write ONLY through api.* functions (SECURITY DEFINER, validated, audited)
--   * no INSERT/UPDATE/DELETE on any table, no access to internal helper functions
GRANT USAGE ON SCHEMA hiring, ops, api, reporting TO n8n_app, backend_app;

GRANT SELECT ON ALL TABLES IN SCHEMA hiring    TO n8n_app, backend_app;
GRANT SELECT ON ALL TABLES IN SCHEMA ops       TO n8n_app, backend_app;
GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO n8n_app, backend_app;

GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA api       TO n8n_app, backend_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA reporting TO n8n_app, backend_app;

-- Objects created by later migrations get the same privileges automatically.
ALTER DEFAULT PRIVILEGES IN SCHEMA hiring    GRANT SELECT ON TABLES TO n8n_app, backend_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA ops       GRANT SELECT ON TABLES TO n8n_app, backend_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA reporting GRANT SELECT ON TABLES TO n8n_app, backend_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA api       GRANT EXECUTE ON FUNCTIONS TO n8n_app, backend_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA reporting GRANT EXECUTE ON FUNCTIONS TO n8n_app, backend_app;

-- migrate:down
ALTER DEFAULT PRIVILEGES IN SCHEMA hiring    REVOKE SELECT ON TABLES FROM n8n_app, backend_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA ops       REVOKE SELECT ON TABLES FROM n8n_app, backend_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA reporting REVOKE SELECT ON TABLES FROM n8n_app, backend_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA api       REVOKE EXECUTE ON FUNCTIONS FROM n8n_app, backend_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA reporting REVOKE EXECUTE ON FUNCTIONS FROM n8n_app, backend_app;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA api, reporting FROM n8n_app, backend_app;
REVOKE ALL ON ALL TABLES IN SCHEMA hiring, ops, reporting FROM n8n_app, backend_app;
REVOKE USAGE ON SCHEMA hiring, ops, api, reporting FROM n8n_app, backend_app;
