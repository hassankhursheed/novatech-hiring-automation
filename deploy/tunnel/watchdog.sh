#!/bin/sh
# Runs cloudflared for the Cloudflare quick tunnel and makes it recover from network drops.
# A quick tunnel has no account behind it: when this PC loses its connection for a while, Cloudflare deletes the
# tunnel and cloudflared keeps retrying the deleted one forever ("Unauthorized: Tunnel not found"), so the public
# link stays dead. cloudflared's /ready endpoint answers 200 only while a connection to Cloudflare is up; once it has
# failed for TUNNEL_READY_GRACE_SECONDS this script exits, Docker restarts the container (restart: unless-stopped),
# cloudflared requests a fresh tunnel and the tunnel-url service moves the links in emails to the new address.
# Runs in the "tunnel" service of deploy/docker-compose.tunnel.yml.
set -u
: "${TUNNEL_ORIGIN:?TUNNEL_ORIGIN is required}"
grace=${TUNNEL_READY_GRACE_SECONDS:-120}
step=10

cloudflared tunnel --no-autoupdate --metrics 0.0.0.0:2000 --url "$TUNNEL_ORIGIN" &
pid=$!
trap 'kill -TERM "$pid" 2>/dev/null; wait "$pid"; exit 0' TERM INT

down=0
while kill -0 "$pid" 2>/dev/null; do
  sleep "$step" & wait $!   # a background sleep lets "docker stop" interrupt the wait at once
  if wget -q -T 5 -O /dev/null http://127.0.0.1:2000/ready 2>/dev/null; then
    down=0
  else
    down=$((down + step))
  fi
  if [ "$down" -ge "$grace" ]; then
    echo "tunnel-watchdog: no connection to Cloudflare for ${down}s, restarting to get a new quick tunnel" >&2
    kill -TERM "$pid" 2>/dev/null
    wait "$pid"
    exit 1
  fi
done
wait "$pid"
