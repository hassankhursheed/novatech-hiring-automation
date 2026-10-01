#!/bin/sh
# Creates .env from .env.example with strong random secrets (Linux/macOS).
# Usage: sh scripts/new-env.sh [--force]
set -eu

root="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$root/.env" ] && [ "${1:-}" != "--force" ]; then
  echo ".env already exists. Use --force to overwrite (this rotates every secret)." >&2
  exit 1
fi

secret() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 40; }

cp "$root/.env.example" "$root/.env"
for key in POSTGRES_SUPERUSER_PASSWORD NOVATECH_OWNER_PASSWORD NOVATECH_N8N_DB_PASSWORD \
           NOVATECH_BACKEND_DB_PASSWORD N8N_DB_PASSWORD N8N_ENCRYPTION_KEY N8N_RUNNERS_AUTH_TOKEN INTERNAL_API_KEYS \
           LINK_SIGNING_SECRET; do
  value="$(secret)"
  sed -i.bak "s|^${key}=.*|${key}=${value}|" "$root/.env"
done
rm -f "$root/.env.bak"
echo "Created .env with fresh secrets. Add your LLM API key before starting."
