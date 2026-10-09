#!/bin/sh
# Removes applications from the business database (db/maintenance/purge_applications.sql), then the stored files
# (CVs with their extracted text, offer letters) that no remaining application or offer refers to.
#
#   sh scripts/purge-applications.sh              test applicants only (reserved test domains such as example.com)
#   sh scripts/purge-applications.sh --all        EVERY application: a clean start before go-live
#   add --dry-run to only see what would be removed, --yes to skip the confirmation question
#
# It always shows the dry run first and takes backups (backups/novatech-before-purge-<time>.dump and
# backups/storage-before-purge-<time>.tar.gz) before changing anything. Restore them with:
#   docker compose cp backups/<file>.dump db:/tmp/restore.dump
#   docker compose exec db pg_restore -U postgres -d novatech --clean --if-exists /tmp/restore.dump
#   docker compose cp backups/<file>.tar.gz backend:/tmp/restore.tar.gz
#   docker compose exec backend tar xzf /tmp/restore.tar.gz -C /data/storage
# Delete the backups once you are sure: they hold the same personal data.
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

# Lists (into /tmp/orphans in the backend container) the stored files no application or offer refers to.
orphan_files() {
  docker compose exec -T db psql -U novatech_owner -d novatech -X -A -t -c "
    SELECT cv_storage_key FROM hiring.applications WHERE cv_storage_key IS NOT NULL
    UNION SELECT regexp_replace(cv_storage_key, '[.][^./]+$', '.txt') FROM hiring.applications
     WHERE cv_storage_key IS NOT NULL
    UNION SELECT document_storage_key FROM hiring.offers WHERE document_storage_key IS NOT NULL" | tr -d '\r' |
    docker compose exec -T backend sh -c 'cd /data/storage && sort -u > /tmp/keep &&
      find cv offers -type f 2>/dev/null | sort > /tmp/all; comm -23 /tmp/all /tmp/keep > /tmp/orphans; wc -l < /tmp/orphans' |
    tr -d '\r '
}

echo "== Dry run (scope: $scope)"
run true
echo "Stored files no application refers to already: $(orphan_files) (removed too, with the files of removed applications)"
[ "$dry_only" = true ] && exit 0

if [ "$assume_yes" != true ]; then
  printf 'Remove these rows permanently? Type DELETE to continue: '
  read -r answer
  [ "$answer" = DELETE ] || { echo "Cancelled; nothing was changed."; exit 1; }
fi

mkdir -p backups
stamp=$(date +%Y%m%d-%H%M%S)
backup="backups/novatech-before-purge-$stamp.dump"
docker compose exec -T db sh -c 'pg_dump -U postgres -d novatech --format=custom --file=/tmp/purge-backup.dump'
docker compose cp db:/tmp/purge-backup.dump "$backup" >/dev/null
docker compose exec -T db rm -f /tmp/purge-backup.dump
echo "== Backup: $backup"

echo "== Removing (scope: $scope)"
run false

files=$(orphan_files)
if [ "${files:-0}" -gt 0 ]; then
  archive="backups/storage-before-purge-$stamp.tar.gz"
  docker compose exec -T backend sh -c 'cd /data/storage && tar czf /tmp/purge-files.tar.gz -T /tmp/orphans'
  docker compose cp backend:/tmp/purge-files.tar.gz "$archive" >/dev/null 2>&1
  docker compose exec -T backend sh -c 'cd /data/storage && xargs rm -f < /tmp/orphans &&
    find cv offers -mindepth 1 -type d -empty -delete 2>/dev/null; rm -f /tmp/keep /tmp/all /tmp/orphans /tmp/purge-files.tar.gz'
  echo "== Files: removed $files stored files (archive: $archive)"
else
  echo "== Files: nothing to remove"
fi
