#!/bin/sh
# Puts n8n's own Mistral key (N8N_MISTRAL_API_KEY in .env) into the n8n credential "Mistral AI (n8n)", used by WF-04
# for the interviewer's suggested questions. The key never appears in output or in the repository.
#
#   sh scripts/n8n-mistral-key.sh
#
# n8n only ever uses N8N_MISTRAL_API_KEY; the backend's MISTRAL_API_KEY is never copied into n8n.
# Alternative without this script: n8n -> Credentials -> "Mistral AI (n8n)" -> paste the key -> Save.
set -eu
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."

key=$(grep -E "^N8N_MISTRAL_API_KEY=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r' \
  | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/")
[ -n "$key" ] || { echo "N8N_MISTRAL_API_KEY is empty in .env: paste n8n's Mistral key there first." >&2; exit 1; }

project=$(docker compose exec -T n8n-db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT id FROM project WHERE type = '"'"'personal'"'"' ORDER BY \"createdAt\" LIMIT 1"' | tr -d '\r')
[ -n "$project" ] || { echo "No n8n owner account yet: open http://localhost:5678 and create it first." >&2; exit 1; }

# The key travels on stdin into the container, is written to a private temp file, imported (n8n encrypts it with
# its own key) and deleted.
printf '%s' "$key" | docker compose exec -T n8n sh -c '
  umask 077
  NT_KEY=$(cat) node -e "require(\"fs\").writeFileSync(\"/tmp/nt-mistral.json\", JSON.stringify([{id: \"ntMistralCloud01\", name: \"Mistral AI (n8n)\", type: \"mistralCloudApi\", data: {apiKey: process.env.NT_KEY}}]))"
  n8n import:credentials --input=/tmp/nt-mistral.json --projectId='"$project"' >/dev/null 2>&1
  status=$?
  rm -f /tmp/nt-mistral.json
  exit $status'
echo "n8n credential \"Mistral AI (n8n)\" now uses N8N_MISTRAL_API_KEY."
