#!/bin/sh
# Creates the four n8n credentials from the server's .env and deploys + publishes all workflows.
#
#   sh deploy/n8n-setup.sh
#
# Run it once the n8n owner account exists (create it in the editor through the SSH tunnel:
# http://localhost:5678). Safe to run again: credentials are updated in place, workflows re-deployed.
# Secrets travel on stdin into the n8n container, are imported (n8n encrypts them) and the temp file is deleted.
set -eu
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."

project=$(docker compose exec -T n8n-db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT id FROM project WHERE type = '"'"'personal'"'"' ORDER BY \"createdAt\" LIMIT 1"' | tr -d '\r')
[ -n "$project" ] || { echo "No n8n owner account yet: open http://localhost:5678 through the SSH tunnel and create it first." >&2; exit 1; }

echo "== Credentials"
python3 - <<'PY' | docker compose exec -T n8n sh -c "umask 077; cat > /tmp/nt-credentials.json; n8n import:credentials --input=/tmp/nt-credentials.json --projectId=$project >/dev/null 2>&1; status=\$?; rm -f /tmp/nt-credentials.json; exit \$status"
import json, re, sys

text = open('.env', encoding='utf-8').read()
def env(key, default=''):
    m = re.search(r'(?m)^%s=(.*)$' % re.escape(key), text)
    value = m.group(1).split(' #')[0].strip().strip('"').strip("'") if m else ''
    return value or default

missing = [k for k in ('NOVATECH_N8N_DB_PASSWORD', 'INTERNAL_API_KEYS', 'SMTP_USER', 'SMTP_PASSWORD', 'N8N_MISTRAL_API_KEY') if not env(k)]
if missing:
    sys.exit('missing in .env: ' + ', '.join(missing) + ' (run sh deploy/configure.sh)')
port = int(env('SMTP_PORT', '465'))
json.dump([
    {'id': 'ntPostgresApp001', 'name': 'NovaTech DB (n8n_app)', 'type': 'postgres',
     'data': {'host': 'db', 'port': 5432, 'database': 'novatech', 'user': 'n8n_app',
              'password': env('NOVATECH_N8N_DB_PASSWORD'), 'ssl': 'disable', 'allowUnauthorizedCerts': False,
              'maxConnections': 10, 'sshTunnel': False}},
    {'id': 'ntBackendApiKey1', 'name': 'NovaTech Backend API key', 'type': 'httpHeaderAuth',
     'data': {'name': 'X-API-Key', 'value': env('INTERNAL_API_KEYS').split(',')[0].strip()}},
    {'id': 'ntMailpitSmtp001', 'name': 'NovaTech SMTP (outgoing email)', 'type': 'smtp',
     'data': {'user': env('SMTP_USER'), 'password': env('SMTP_PASSWORD'), 'host': env('SMTP_HOST', 'smtp.gmail.com'),
              'port': port, 'secure': port == 465, 'disableStartTls': False}},
    {'id': 'ntMistralCloud01', 'name': 'Mistral AI (n8n)', 'type': 'mistralCloudApi',
     'data': {'apiKey': env('N8N_MISTRAL_API_KEY')}},
], sys.stdout)
PY
echo "created/updated: NovaTech DB (n8n_app), NovaTech Backend API key, NovaTech SMTP (outgoing email), Mistral AI (n8n)"

echo "== Workflows"
sh scripts/n8n-deploy.sh

echo
echo "n8n is ready. Check: the portal's careers page accepts an application and the confirmation email arrives."
