# Imports workflows from n8n/workflows/*.json into the running n8n (e.g. on a fresh install).
# Credentials must be created in the n8n UI afterwards (see docs/n8n-setup.md); workflows reference them by name.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\n8n-import.ps1
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Get-ChildItem "n8n\workflows\*.json" -ErrorAction SilentlyContinue)) {
    Write-Host "No workflow files in n8n\workflows - nothing to import." -ForegroundColor Yellow
    exit 0
}
docker compose exec -T n8n n8n import:workflow --separate --input=/home/node/workflows/
if ($LASTEXITCODE -ne 0) { throw "n8n import failed" }
Write-Host "Imported workflows. Open http://localhost:5678, attach credentials and publish them." -ForegroundColor Green
