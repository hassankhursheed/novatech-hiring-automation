# Exports every n8n workflow to n8n/workflows/*.json (one file per workflow) for version control.
# Credentials are NOT exported. Run after every change you make in the n8n editor, then commit.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\n8n-export.ps1
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

docker compose exec -T n8n n8n export:workflow --all --separate --pretty --output=/home/node/workflows/
if ($LASTEXITCODE -ne 0) { throw "n8n export failed" }

# Guard against accidentally committed secrets in node parameters.
$suspicious = Select-String -Path "n8n\workflows\*.json" -Pattern '(sk-ant-|sk-[A-Za-z0-9]{20,}|"password"\s*:\s*"[^"]+"|Bearer [A-Za-z0-9._-]{20,})' -ErrorAction SilentlyContinue
if ($suspicious) {
    Write-Host "WARNING: possible secrets found in exported workflows - move them into n8n credentials:" -ForegroundColor Red
    $suspicious | ForEach-Object { Write-Host "  $($_.Path):$($_.LineNumber)" }
    exit 1
}
Write-Host "Exported workflows to n8n\workflows" -ForegroundColor Green
