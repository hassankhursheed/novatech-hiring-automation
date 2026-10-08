#!/bin/sh
# Keeps company.portal_url (the address in every email link) equal to the Cloudflare quick tunnel's address.
# A quick tunnel gets a new random https://<words>.trycloudflare.com address whenever it restarts; this loop reads
# it from cloudflared's metrics endpoint every 15 seconds and updates the setting when it changes.
# Runs in the "tunnel-url" service of deploy/docker-compose.tunnel.yml.
set -u
: "${DATABASE_URL:?DATABASE_URL is required}"
last=""
while true; do
  host=$(wget -qO- http://tunnel:2000/quicktunnel 2>/dev/null | sed -n 's/.*"hostname":"\([^"]*\)".*/\1/p')
  if [ -n "$host" ] && [ "https://$host" != "$last" ]; then
    if psql "$DATABASE_URL" -X -q -v ON_ERROR_STOP=1 -v url="https://$host" <<'EOSQL'
UPDATE hiring.settings
   SET value = to_jsonb(:'url'::text), updated_at = now(), updated_by = 'tunnel-url-sync'
 WHERE key = 'company.portal_url' AND value IS DISTINCT FROM to_jsonb(:'url'::text);
EOSQL
    then
      last="https://$host"
      echo "$(date -Iseconds) public link: $last (email links updated)"
    fi
  fi
  sleep 15
done
