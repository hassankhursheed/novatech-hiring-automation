#!/bin/sh
# Nightly backup (installed by deploy/up.sh): both databases and the stored documents (CVs, offer letters).
# Keeps 14 days in backups/<date>/. Copy them off the server regularly, together with a private copy of .env
# (N8N_ENCRYPTION_KEY is needed to restore n8n's credentials):
#   scp -i <key> -r ubuntu@<server-ip>:novatech-hiring-automation/backups/<date> .
#
# Restore one database:
#   docker compose cp backups/<date>/novatech.dump db:/tmp/r.dump
#   docker compose exec db pg_restore -U postgres -d novatech --clean --if-exists /tmp/r.dump
set -eu
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."

day=$(date +%F)
dir="backups/$day"
mkdir -p "$dir"
chmod 700 backups "$dir"

dump() {  # dump SERVICE USER DATABASE FILE
  docker compose exec -T "$1" sh -c "pg_dump -U $2 -d $3 --format=custom --file=/tmp/backup.dump"
  docker compose cp "$1:/tmp/backup.dump" "$dir/$4" >/dev/null
  docker compose exec -T "$1" rm -f /tmp/backup.dump
}
dump db postgres novatech novatech.dump
dump n8n-db n8n n8n n8n.dump

project=$(docker compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')
docker run --rm -v "${project}_storage_data:/data:ro" -v "$(pwd)/$dir:/out" alpine:3 \
  tar czf /out/documents.tgz -C /data .

find backups -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf {} +
echo "$(date -Is) backup ok: $(du -sh "$dir" | cut -f1) in $dir"
