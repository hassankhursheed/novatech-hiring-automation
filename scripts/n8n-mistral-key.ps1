# Windows wrapper: puts the Mistral key from .env into the n8n credential "Mistral AI (n8n)".
#   powershell -ExecutionPolicy Bypass -File scripts\n8n-mistral-key.ps1
$ErrorActionPreference = 'Stop'
$bash = 'C:\Program Files\Git\bin\bash.exe'
if (-not (Test-Path $bash)) { throw 'Git Bash is required (https://git-scm.com/download/win).' }
& $bash (Join-Path $PSScriptRoot 'n8n-mistral-key.sh')
exit $LASTEXITCODE
