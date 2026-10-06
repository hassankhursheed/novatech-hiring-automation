# Windows wrapper for scripts/purge-applications.sh (dry run, backup, confirmation, removal).
#   powershell -ExecutionPolicy Bypass -File scripts\purge-applications.ps1 [--all] [--dry-run] [--yes]
$ErrorActionPreference = 'Stop'
$bash = 'C:\Program Files\Git\bin\bash.exe'
if (-not (Test-Path $bash)) { throw 'Git Bash is required (https://git-scm.com/download/win).' }
& $bash (Join-Path $PSScriptRoot 'purge-applications.sh') @args
exit $LASTEXITCODE
