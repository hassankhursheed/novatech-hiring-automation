#!/bin/sh
# Builds and starts the whole stack on the server (HTTPS proxy included) and installs the daily jobs.
#
#   sh deploy/up.sh
#
# Safe to run again after `git pull`: it rebuilds what changed and restarts it.
set -eu
cd "$(dirname "$0")/.."
grep -q '^COMPOSE_FILE=.*deploy/docker-compose.prod.yml' .env 2>/dev/null \
  || { echo "Run sh deploy/configure.sh first (it writes the server .env)." >&2; exit 1; }

echo "== Building and starting (first time: ~10 minutes on a small ARM server)"
docker compose up -d --build

echo "== Waiting for the backend"
for _ in $(seq 1 60); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q backend)" 2>/dev/null)" = healthy ] && break
  sleep 5
done
docker compose ps --format 'table {{.Name}}\t{{.Status}}'

echo "== Daily jobs (crontab of $(whoami))"
dir=$(pwd)
mkdir -p backups
( crontab -l 2>/dev/null | grep -v '# novatech:' ;
  echo "15 2 * * * cd $dir && sh deploy/backup.sh >> backups/backup.log 2>&1 # novatech: nightly database + document backup"
  echo "30 3 * * * cd $dir && docker compose run --rm seed >> backups/seed.log 2>&1 # novatech: interview slots for the next 15 days"
) | crontab -
crontab -l | grep '# novatech:'

portal=$(grep -E '^PORTAL_PUBLIC_URL=' .env | cut -d= -f2-)
echo
echo "Running. Public portal: $portal (the HTTPS certificate is issued on the first visit, allow a minute)."
echo "Next: create the n8n owner account through the SSH tunnel, then: sh deploy/n8n-setup.sh"
