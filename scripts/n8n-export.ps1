# Exports every n8n workflow to n8n/workflows/<workflow-name>.json for version control.
# Credentials are NOT exported. Run after every change you make in the n8n editor, then commit.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\n8n-export.ps1
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

$inner = @'
set -e
rm -rf /tmp/n8n-export && mkdir -p /tmp/n8n-export
n8n export:workflow --all --separate --pretty --output=/tmp/n8n-export/ >/dev/null
find /home/node/workflows -maxdepth 1 -name "*.json" -delete
node -e '
  const fs = require("fs");
  for (const f of fs.readdirSync("/tmp/n8n-export")) {
    const wf = JSON.parse(fs.readFileSync("/tmp/n8n-export/" + f, "utf8"));
    const slug = wf.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    fs.writeFileSync("/home/node/workflows/" + slug + ".json", JSON.stringify(wf, null, 2) + "\n");
    console.log("exported " + slug + ".json");
  }
'
'@
# Pass the script base64-encoded: Windows PowerShell mangles quotes in native arguments and adds a
# byte-order mark when piping to stdin; base64 avoids both.
$b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes(($inner -replace "`r", "")))
docker compose exec -T n8n sh -c "echo $b64 | base64 -d | sh"
if ($LASTEXITCODE -ne 0) { throw "n8n export failed" }

# Guard against accidentally committed secrets in node parameters.
$suspicious = Select-String -Path "n8n\workflows\*.json" -Pattern '(sk-ant-|sk-[A-Za-z0-9]{20,}|"password"\s*:\s*"[^"]+"|Bearer [A-Za-z0-9._-]{20,})' -ErrorAction SilentlyContinue
if ($suspicious) {
    Write-Host "WARNING: possible secrets found in exported workflows - move them into n8n credentials:" -ForegroundColor Red
    $suspicious | ForEach-Object { Write-Host "  $($_.Path):$($_.LineNumber)" }
    exit 1
}
Write-Host "Exported workflows to n8n\workflows" -ForegroundColor Green
