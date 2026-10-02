# Windows wrapper for scripts/n8n-deploy.sh (needs Git for Windows, which provides sh.exe).
#   powershell -ExecutionPolicy Bypass -File scripts\n8n-deploy.ps1              # build n8n/src and deploy
#   powershell -ExecutionPolicy Bypass -File scripts\n8n-deploy.ps1 --exported   # deploy n8n/workflows (fresh install)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$git = (Get-Command git -ErrorAction Stop).Source
$sh = Join-Path (Split-Path (Split-Path $git -Parent) -Parent) 'bin\sh.exe'
if (-not (Test-Path $sh)) { throw "sh.exe not found next to git ($sh); run scripts/n8n-deploy.sh from Git Bash instead" }
& $sh scripts/n8n-deploy.sh @args
if ($LASTEXITCODE -ne 0) { throw "n8n deploy failed" }
