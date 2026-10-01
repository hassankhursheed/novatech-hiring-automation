# Creates .env from .env.example with strong random secrets (Windows PowerShell).
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\new-env.ps1 [-Force]
param([switch]$Force)
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$example = Join-Path $root '.env.example'
$target = Join-Path $root '.env'

if ((Test-Path $target) -and -not $Force) {
    Write-Host ".env already exists. Use -Force to overwrite (this rotates every secret)." -ForegroundColor Yellow
    exit 1
}

function New-Secret([int]$length = 40) {
    $chars = [char[]]'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
    $bytes = New-Object byte[] $length
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}

$secretKeys = @(
    'POSTGRES_SUPERUSER_PASSWORD', 'NOVATECH_OWNER_PASSWORD', 'NOVATECH_N8N_DB_PASSWORD',
    'NOVATECH_BACKEND_DB_PASSWORD', 'N8N_DB_PASSWORD', 'N8N_ENCRYPTION_KEY', 'N8N_RUNNERS_AUTH_TOKEN',
    'INTERNAL_API_KEYS'
)

$lines = Get-Content $example | ForEach-Object {
    $line = $_
    foreach ($key in $secretKeys) {
        if ($line -match "^$key=") { $line = "$key=$(New-Secret)" }
    }
    $line
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($target, $lines, $utf8NoBom)
Write-Host "Created .env with fresh secrets. Add your LLM API key (e.g. ANTHROPIC_API_KEY) before starting." -ForegroundColor Green
