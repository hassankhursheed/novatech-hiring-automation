#!/bin/sh
# Removes applications from the business database (db/maintenance/purge_applications.sql).
#
#   sh scripts/purge-applications.sh              test applicants only (reserved test domains such as example.com)
#   sh scripts/purge-applications.sh --all        EVERY application: a clean start before go-live
#   add --dry-run to only see what would be removed, --yes to skip the confirmation question
#
# It always shows the dry run first and takes a backup (backups/novatech-before-purge-<time>.dump) before changing
# anything. Restore a backup with:
#   docker compose cp backups/<file>.dump db:/tmp/restore.dump
#   docker compose exec db pg_restore -U postgres -d novatech --clean --if-exists /tmp/restore.dump
set -eu
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."

scope=test dry_only=false assume_yes=false
for arg in "$@"; do
  case "$arg" in
    --all) scope=all ;;
    --dry-run) dry_only=true ;;
    --yes) assume_yes=true ;;
    *) echo "unknown option $arg (use --all, --dry-run, --yes)" >&2; exit 2 ;;
  esac
done

run() {  # $1 = dry_run true|false
  docker compose exec -T db psql -U novatech_owner -d novatech -X -q -v scope="$scope" -v dry_run="$1" \
    -f - < db/maintenance/purge_applications.sql
}

echo "== Dry run (scope: $scope)"
run true
[ "$dry_only" = true ] && exit 0

if [ "$assume_yes" != true ]; then
  printf 'Remove these rows permanently? Type DELETE to continue: '
  read -r answer
  [ "$answer" = DELETE ] || { echo "Cancelled; nothing was changed."; exit 1; }
fi

mkdir -p backups
backup="backups/novatech-before-purge-$(date +%Y%m%d-%H%M%S).dump"
docker compose exec -T db sh -c 'pg_dump -U postgres -d novatech --format=custom --file=/tmp/purge-backup.dump'
docker compose cp db:/tmp/purge-backup.dump "$backup" >/dev/null
docker compose exec -T db rm -f /tmp/purge-backup.dump
echo "== Backup: $backup"

echo "== Removing (scope: $scope)"
run false
