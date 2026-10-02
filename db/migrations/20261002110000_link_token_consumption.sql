-- migrate:up

-- Single-use signed links. Most links in this system may be opened many times (an offer can be viewed again), and
-- their actions are idempotent in the database. Sign-in links are different: a sign-in link must work exactly once,
-- so a forwarded or leaked email cannot be replayed to obtain a session.
CREATE TABLE ops.consumed_link_tokens (
  token_id     text PRIMARY KEY CHECK (token_id ~ '^[0-9a-f]{32}$'),
  purpose      text NOT NULL CHECK (purpose ~ '^[A-Z][A-Z_]*$'),
  subject_id   text NOT NULL,
  expires_at   timestamptz NOT NULL,
  consumed_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX consumed_link_tokens_expiry_idx ON ops.consumed_link_tokens (expires_at);
COMMENT ON TABLE ops.consumed_link_tokens IS
  'jti of single-use links (staff sign-in). Rows past expires_at can be purged: the token itself is expired by then.';

CREATE FUNCTION api.consume_link_token(p_token_id text, p_purpose text, p_subject_id text, p_expires_at timestamptz,
                                       p_ctx jsonb)
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
    PERFORM ops.write_log(v_ctx, 'LINK', p_token_id, 'LINK_REUSE_REFUSED', 'FAILURE',
      jsonb_build_object('purpose', upper(p_purpose), 'subject_id', p_subject_id));
    RETURN false;
  END IF;
  PERFORM ops.write_log(v_ctx, 'LINK', p_token_id, 'LINK_CONSUMED', 'SUCCESS',
    jsonb_build_object('purpose', upper(p_purpose), 'subject_id', p_subject_id));
  RETURN true;
END;
$$;

GRANT EXECUTE ON FUNCTION api.consume_link_token(text, text, text, timestamptz, jsonb) TO n8n_app, backend_app;
GRANT SELECT ON ops.consumed_link_tokens TO n8n_app, backend_app;

-- migrate:down
DROP FUNCTION IF EXISTS api.consume_link_token(text, text, text, timestamptz, jsonb);
DROP TABLE IF EXISTS ops.consumed_link_tokens;
