#!/bin/sh
# Exports every n8n workflow to n8n/workflows/*.json for version control (credentials are not exported).
set -eu
cd "$(dirname "$0")/.."
docker compose exec -T n8n n8n export:workflow --all --separate --pretty --output=/home/node/workflows/
if grep -EnH '(sk-ant-|sk-[A-Za-z0-9]{20,}|"password"[[:space:]]*:[[:space:]]*"[^"]+"|Bearer [A-Za-z0-9._-]{20,})' n8n/workflows/*.json; then
  echo "WARNING: possible secrets in exported workflows - move them into n8n credentials" >&2
  exit 1
fi
echo "Exported workflows to n8n/workflows"
