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
           LINK_SIGNING_SECRET LANGFUSE_DB_PASSWORD LANGFUSE_CLICKHOUSE_PASSWORD LANGFUSE_MINIO_PASSWORD \
           LANGFUSE_REDIS_PASSWORD LANGFUSE_SALT LANGFUSE_NEXTAUTH_SECRET LANGFUSE_INIT_USER_PASSWORD; do
  value="$(secret)"
  sed -i.bak "s|^${key}=.*|${key}=${value}|" "$root/.env"
done
sed -i.bak "s|^LANGFUSE_ENCRYPTION_KEY=.*|LANGFUSE_ENCRYPTION_KEY=$(od -An -tx1 -N32 /dev/urandom | tr -d ' \n')|" "$root/.env"
sed -i.bak "s|^LANGFUSE_PUBLIC_KEY=$|LANGFUSE_PUBLIC_KEY=pk-lf-$(secret)|; s|^LANGFUSE_SECRET_KEY=$|LANGFUSE_SECRET_KEY=sk-lf-$(secret)|" "$root/.env"
rm -f "$root/.env.bak"
echo "Created .env with fresh secrets. Paste your Mistral key into MISTRAL_API_KEY before starting."
