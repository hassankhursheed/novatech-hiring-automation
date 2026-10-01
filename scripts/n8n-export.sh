#!/bin/sh
# Exports every n8n workflow to n8n/workflows/<workflow-name>.json for version control.
# Credentials are never exported (workflows reference them by id/name only).
set -eu
# Git Bash on Windows rewrites "/home/..." arguments into Windows paths; container paths must stay as-is.
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."

docker compose exec -T n8n sh -c '
  set -e
  rm -rf /tmp/n8n-export && mkdir -p /tmp/n8n-export
  n8n export:workflow --all --separate --pretty --output=/tmp/n8n-export/ >/dev/null
  find /home/node/workflows -maxdepth 1 -name "*.json" -delete
  node -e "
    const fs = require(\"fs\");
    for (const f of fs.readdirSync(\"/tmp/n8n-export\")) {
      const wf = JSON.parse(fs.readFileSync(\"/tmp/n8n-export/\" + f, \"utf8\"));
      const slug = wf.name.toLowerCase().replace(/[^a-z0-9]+/g, \"-\").replace(/^-|-$/g, \"\");
      fs.writeFileSync(\"/home/node/workflows/\" + slug + \".json\", JSON.stringify(wf, null, 2) + \"\\n\");
      console.log(\"exported \" + slug + \".json\");
    }
  "
'

if grep -EnH '(sk-ant-|sk-[A-Za-z0-9]{20,}|"password"[[:space:]]*:[[:space:]]*"[^"]+"|Bearer [A-Za-z0-9._-]{20,})' n8n/workflows/*.json; then
  echo "WARNING: possible secrets in exported workflows - move them into n8n credentials" >&2
  exit 1
fi
echo "Exported workflows to n8n/workflows"
