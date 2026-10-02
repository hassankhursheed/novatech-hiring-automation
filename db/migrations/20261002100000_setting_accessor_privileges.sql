-- migrate:up

-- Reporting views call these read-only setting accessors (e.g. hiring.company_timezone() for "today"), and
-- PostgreSQL checks EXECUTE on functions inside a view against the user reading the view. The runtime roles can
-- already SELECT hiring.settings, so allowing the accessors grants no new access; write helpers stay private.
GRANT EXECUTE ON FUNCTION
  hiring.setting_value(text, text),
  hiring.setting_text(text),
  hiring.setting_number(text),
  hiring.setting_interval(text),
  hiring.setting_bool(text),
  hiring.company_timezone()
TO n8n_app, backend_app;

-- migrate:down
REVOKE EXECUTE ON FUNCTION
  hiring.setting_value(text, text),
  hiring.setting_text(text),
  hiring.setting_number(text),
  hiring.setting_interval(text),
  hiring.setting_bool(text),
  hiring.company_timezone()
FROM n8n_app, backend_app;
