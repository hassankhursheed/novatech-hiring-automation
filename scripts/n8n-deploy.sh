#!/bin/sh
# Deploys workflows into the running n8n: import into the owner's project, publish, restart n8n so triggers register.
#
#   sh scripts/n8n-deploy.sh            # build the code-defined workflows (n8n/src) and deploy them
#   sh scripts/n8n-deploy.sh --exported # deploy every exported workflow in n8n/workflows (fresh install / restore)
#   sh scripts/n8n-deploy.sh WF-04 WF-05  # build and deploy only these
#
# Credentials are never part of a deploy; create them once (docs/n8n-setup.md). Afterwards run scripts/n8n-export.sh
# and commit, so n8n/workflows always matches what runs.
set -eu
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."

# The application form may only be posted from the portal's own address (WF-01); servers set PORTAL_PUBLIC_URL.
portal_url=$(grep -E '^PORTAL_PUBLIC_URL=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r"' || true)
export INTAKE_ALLOWED_ORIGINS="${portal_url%/}"

if [ "${1:-}" = "--exported" ]; then
  src=n8n/workflows
else
  rm -rf n8n/build && node n8n/src/build.mjs "$@"
  src=n8n/build
fi

project=$(docker compose exec -T n8n-db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT id FROM project WHERE type = '"'"'personal'"'"' ORDER BY \"createdAt\" LIMIT 1"' | tr -d '\r')
[ -n "$project" ] || { echo "No n8n owner account yet: open http://localhost:5678 and create it first." >&2; exit 1; }

docker compose exec -T n8n sh -c 'rm -rf /tmp/nt-deploy && mkdir -p /tmp/nt-deploy'
ids=""
for f in "$src"/*.json; do
  docker compose cp "$f" n8n:/tmp/nt-deploy/ >/dev/null
  ids="$ids $(node -e 'process.stdout.write(require(process.argv[1]).id)' "./$f")"
done
docker compose exec -T n8n n8n import:workflow --separate --input=/tmp/nt-deploy/ --projectId="$project"

# WF-07 first: it is the error workflow of all the others.
ordered=""
case " $ids " in *" ntWf07ErrRecover "*) ordered="ntWf07ErrRecover" ;; esac
for id in $ids; do [ "$id" = ntWf07ErrRecover ] || ordered="$ordered $id"; done
for id in $ordered; do
  docker compose exec -T n8n n8n publish:workflow --id="$id" >/dev/null
  echo "published $id"
done

docker compose restart n8n >/dev/null
echo "Deployed to project $project; n8n restarted so schedules and webhooks are registered."
