-- migrate:up

-- =============================================================================================
-- Test directives (dev/demo only): carry an X-Fault-Inject directive from the intake request to the
-- later, asynchronous steps of the same business transaction (keyed by correlation id), so failure
-- scenarios can be demonstrated deterministically end-to-end.
-- Disabled unless hiring.settings 'dev.fault_injection_enabled' is true; the backend additionally
-- ignores faults when APP_ENV=production.
-- Clearing a directive (empty string) is the "correction" step before replaying a failed item.
-- =============================================================================================
INSERT INTO hiring.settings (key, value, value_type, description) VALUES
  ('dev.fault_injection_enabled', 'false', 'boolean',
   'DEV/DEMO ONLY: allow X-Fault-Inject directives to follow a transaction into later workflow steps')
ON CONFLICT (key) DO NOTHING;

CREATE TABLE ops.test_directives (
  correlation_id  text PRIMARY KEY,
  directives      text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER test_directives_touch BEFORE UPDATE ON ops.test_directives
  FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();

CREATE FUNCTION api.set_test_directive(p_correlation_id text, p_directives text, p_ctx jsonb)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_ctx jsonb := ops.require_ctx(p_ctx);
BEGIN
  IF NOT coalesce((SELECT (s.value #>> '{}')::boolean FROM hiring.settings s
                    WHERE s.key = 'dev.fault_injection_enabled'), false) THEN
    RETURN false;
  END IF;
  IF nullif(btrim(p_directives), '') IS NULL THEN
    DELETE FROM ops.test_directives d WHERE d.correlation_id = p_correlation_id;
    PERFORM ops.write_log(v_ctx || jsonb_build_object('correlation_id', p_correlation_id), 'SYSTEM', p_correlation_id,
      'TEST_DIRECTIVE_CLEARED', 'SUCCESS');
    RETURN true;
  END IF;
  INSERT INTO ops.test_directives (correlation_id, directives) VALUES (p_correlation_id, btrim(p_directives))
  ON CONFLICT (correlation_id) DO UPDATE SET directives = EXCLUDED.directives;
  PERFORM ops.write_log(v_ctx || jsonb_build_object('correlation_id', p_correlation_id), 'SYSTEM', p_correlation_id,
    'TEST_DIRECTIVE_SET', 'SUCCESS', jsonb_build_object('directives', btrim(p_directives)));
  RETURN true;
END;
$$;

CREATE FUNCTION api.test_directive(p_correlation_id text)
RETURNS text
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT CASE WHEN coalesce((SELECT (s.value #>> '{}')::boolean FROM hiring.settings s
                              WHERE s.key = 'dev.fault_injection_enabled'), false)
              THEN coalesce((SELECT d.directives FROM ops.test_directives d WHERE d.correlation_id = p_correlation_id), '')
              ELSE '' END
$$;

GRANT SELECT ON ops.test_directives TO n8n_app, backend_app;
GRANT EXECUTE ON FUNCTION api.set_test_directive(text, text, jsonb), api.test_directive(text) TO n8n_app, backend_app;

-- migrate:down
DROP FUNCTION IF EXISTS api.test_directive(text), api.set_test_directive(text, text, jsonb);
DROP TABLE IF EXISTS ops.test_directives;
DELETE FROM hiring.settings WHERE key = 'dev.fault_injection_enabled';
