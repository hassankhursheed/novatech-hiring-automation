-- migrate:up

-- Staff sign-ins are security events about a staff member: allow STAFF as an audit entity, and record the
-- sign-in (or a refused re-use of a sign-in link) against the staff member instead of the link.
ALTER TABLE ops.automation_logs DROP CONSTRAINT automation_logs_entity_type_check;
ALTER TABLE ops.automation_logs ADD CONSTRAINT automation_logs_entity_type_check CHECK (entity_type = ANY (ARRAY[
  'CANDIDATE', 'APPLICATION', 'INTERVIEW', 'OFFER', 'EMPLOYEE', 'ONBOARDING_TASK', 'EVENT', 'NOTIFICATION',
  'SCHEDULED_ACTION', 'ERROR', 'REPORT', 'CONFIG', 'SYSTEM', 'STAFF']));

CREATE OR REPLACE FUNCTION api.consume_link_token(p_token_id text, p_purpose text, p_subject_id text,
                                                  p_expires_at timestamptz, p_ctx jsonb)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_ctx jsonb := ops.require_ctx(p_ctx);
BEGIN
  DELETE FROM ops.consumed_link_tokens t WHERE t.expires_at < now() - interval '1 day';  -- housekeeping
  INSERT INTO ops.consumed_link_tokens (token_id, purpose, subject_id, expires_at)
  VALUES (p_token_id, upper(p_purpose), p_subject_id, p_expires_at)
  ON CONFLICT (token_id) DO NOTHING;
  IF NOT FOUND THEN
    PERFORM ops.write_log(v_ctx, 'STAFF', p_subject_id, 'LINK_REUSE_REFUSED', 'FAILURE',
      jsonb_build_object('purpose', upper(p_purpose), 'token_id', p_token_id));
    RETURN false;
  END IF;
  PERFORM ops.write_log(v_ctx, 'STAFF', p_subject_id,
    CASE WHEN upper(p_purpose) = 'STAFF_LOGIN' THEN 'STAFF_SIGNED_IN' ELSE 'LINK_CONSUMED' END, 'SUCCESS',
    jsonb_build_object('purpose', upper(p_purpose), 'token_id', p_token_id));
  RETURN true;
END;
$$;

-- migrate:down
ALTER TABLE ops.automation_logs DROP CONSTRAINT automation_logs_entity_type_check;
ALTER TABLE ops.automation_logs ADD CONSTRAINT automation_logs_entity_type_check CHECK (entity_type = ANY (ARRAY[
  'CANDIDATE', 'APPLICATION', 'INTERVIEW', 'OFFER', 'EMPLOYEE', 'ONBOARDING_TASK', 'EVENT', 'NOTIFICATION',
  'SCHEDULED_ACTION', 'ERROR', 'REPORT', 'CONFIG', 'SYSTEM']));
