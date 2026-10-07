#!/bin/sh
# Creates the server's .env (fresh random secrets + production settings) and config/staff.csv.
#
#   sh deploy/configure.sh
#
# It asks for: the three public domains, an email for the HTTPS certificates, the sending mailbox and its
# app password, the two Mistral keys, and whether to self-host Langfuse. Secrets are typed hidden and written only to
# .env on this server (never to git). Run it again to change answers: existing secrets are kept.
set -eu
cd "$(dirname "$0")/.."
command -v python3 >/dev/null || { echo "python3 is required (run deploy/server-setup.sh first)" >&2; exit 1; }

ask() {  # ask VAR "question" [default]
  printf '%s%s: ' "$2" "${3:+ [$3]}"
  read -r answer
  eval "$1=\${answer:-\${3:-}}"
}
ask_secret() {  # ask_secret VAR "question"  (input hidden; empty keeps the current value)
  printf '%s (hidden; Enter keeps the current value): ' "$2"
  stty -echo 2>/dev/null || true
  read -r answer
  stty echo 2>/dev/null || true
  echo
  eval "$1=\${answer}"
}
current() { python3 - "$1" <<'PY'
import re, sys
try:
    text = open('.env', encoding='utf-8').read()
except FileNotFoundError:
    sys.exit(0)
m = re.search(r'(?m)^%s=(.*)$' % re.escape(sys.argv[1]), text)
print(m.group(1).split(' #')[0].strip().strip('"').strip("'") if m else '')
PY
}

if [ ! -f .env ]; then
  echo "== Creating .env with fresh random secrets"
  sh scripts/new-env.sh
else
  echo "== Updating the existing .env (its secrets are kept)"
fi

echo
echo "Public addresses (each must already point to this server's IP in DNS):"
ask PORTAL_DOMAIN "  Portal domain (careers page + HR portal), e.g. novatech-careers.duckdns.org" "$(current PORTAL_DOMAIN)"
ask API_DOMAIN    "  API domain, e.g. novatech-api.duckdns.org" "$(current API_DOMAIN)"
ask N8N_DOMAIN    "  n8n webhook domain, e.g. novatech-n8n.duckdns.org" "$(current N8N_DOMAIN)"
ask ACME_EMAIL    "  Email for HTTPS certificate notices (Let's Encrypt)" "$(current ACME_EMAIL)"
echo
echo "Outgoing email (the mailbox candidates receive email from):"
ask MAIL_FROM_ADDRESS "  Sending mailbox address" "$(current MAIL_FROM_ADDRESS)"
ask SMTP_HOST "  SMTP host" "$(current SMTP_HOST | sed 's/^$/smtp.gmail.com/')"
ask SMTP_PORT "  SMTP port (465 = SSL)" "$(current SMTP_PORT | sed 's/^$/465/')"
ask_secret SMTP_PASSWORD "  SMTP password (Gmail: the 16-character App password, no spaces)"
echo
echo "Mistral AI (console.mistral.ai -> API Keys):"
ask_secret MISTRAL_API_KEY "  Backend key (MISTRAL_API_KEY)"
ask_secret N8N_MISTRAL_API_KEY "  n8n key (N8N_MISTRAL_API_KEY)"
echo
ask LANGFUSE "Self-host Langfuse for AI tracing on this server (needs ~3 GB RAM)? y/n" "y"

export PORTAL_DOMAIN API_DOMAIN N8N_DOMAIN ACME_EMAIL MAIL_FROM_ADDRESS SMTP_HOST SMTP_PORT SMTP_PASSWORD \
       MISTRAL_API_KEY N8N_MISTRAL_API_KEY LANGFUSE
python3 - <<'PY'
import csv, os, re

path = '.env'
text = open(path, encoding='utf-8').read()

def setv(key, value, keep_if_empty=False):
    global text
    if keep_if_empty and not value:
        return
    if any(c in value for c in ' #"\'') and not value.startswith('"'):
        value = '"' + value.replace('"', '') + '"'
    pattern = re.compile(r'(?m)^%s=.*$' % re.escape(key))
    line = f'{key}={value}'
    text = pattern.sub(lambda m: line, text) if pattern.search(text) else text.rstrip('\n') + '\n' + line + '\n'

e = os.environ
portal, api, n8n = (f"https://{e[k].strip().lower().removeprefix('https://').strip('/')}" for k in ('PORTAL_DOMAIN', 'API_DOMAIN', 'N8N_DOMAIN'))
mailbox = e['MAIL_FROM_ADDRESS'].strip().lower()
local, _, domain = mailbox.partition('@')

for key, value in {
    # mode
    'APP_ENV': 'production', 'STAFF_DEMO_PASSWORD': '', 'FAULT_INJECTION_ENABLED': 'false', 'EXPOSE_API_DOCS': 'false',
    'COMPOSE_FILE': 'docker-compose.yml:deploy/docker-compose.prod.yml',
    'COMPOSE_PROFILES': 'observability' if e['LANGFUSE'].strip().lower().startswith('y') else '',
    # public addresses
    'PORTAL_DOMAIN': portal[8:], 'API_DOMAIN': api[8:], 'N8N_DOMAIN': n8n[8:], 'ACME_EMAIL': e['ACME_EMAIL'].strip(),
    'PORTAL_PUBLIC_URL': portal, 'CORS_ALLOWED_ORIGINS': portal,
    'PORTAL_API_URL': api, 'PORTAL_INTAKE_URL': f'{n8n}/webhook/applications', 'PORTAL_CONNECT_SRC': f'{api} {n8n}',
    'PORTAL_DEV_MAILBOX_URL': '',
    'N8N_HOST': n8n[8:], 'N8N_PROTOCOL': 'https', 'N8N_WEBHOOK_URL': f'{n8n}/',
    # the editor is reached through an SSH tunnel (http://localhost:5678), never from the internet
    'N8N_EDITOR_BASE_URL': 'http://localhost:5678/', 'N8N_SECURE_COOKIE': 'false',
    # email
    'MAIL_FROM_ADDRESS': mailbox, 'SMTP_USER': mailbox, 'SMTP_HOST': e['SMTP_HOST'].strip(), 'SMTP_PORT': e['SMTP_PORT'].strip(),
    'OPS_ALERT_EMAIL': f'{local}+alerts@{domain}',
    # AI
    'LLM_PROVIDER': 'mistral', 'LLM_MODEL': 'ministral-14b-latest', 'LLM_REQUESTS_PER_SECOND': '0.25',
    'LANGFUSE_HOST': 'http://langfuse-web:3000',
}.items():
    setv(key, value)
for key in ('SMTP_PASSWORD', 'MISTRAL_API_KEY', 'N8N_MISTRAL_API_KEY'):
    setv(key, e[key].strip().replace(' ', ''), keep_if_empty=True)
open(path, 'w', encoding='utf-8', newline='\n').write(text)
os.chmod(path, 0o600)

# Staff: every role on a plus-address of the sending mailbox until the real team is entered (config/README.md).
if not os.path.exists('config/staff.csv'):
    rows = list(csv.DictReader(open('config/staff.example.csv', encoding='utf-8')))
    for r in rows:
        r['email'] = f"{local}+{r['email'].split('@')[0].replace('.', '-')}@{domain}"
    with open('config/staff.csv', 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator='\n')
        w.writeheader()
        w.writerows(rows)
    print(f'config/staff.csv: {len(rows)} staff roles on plus-addresses of {mailbox}')
print(f'.env written for {portal} (API {api}, n8n webhooks {n8n}); file permissions 600')
PY

echo
echo "Next: sh deploy/up.sh"
