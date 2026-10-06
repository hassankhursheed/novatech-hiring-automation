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
    'INTERNAL_API_KEYS', 'LINK_SIGNING_SECRET', 'LANGFUSE_DB_PASSWORD', 'LANGFUSE_CLICKHOUSE_PASSWORD',
    'LANGFUSE_MINIO_PASSWORD', 'LANGFUSE_REDIS_PASSWORD', 'LANGFUSE_SALT', 'LANGFUSE_NEXTAUTH_SECRET',
    'LANGFUSE_INIT_USER_PASSWORD'
)

$lines = Get-Content $example | ForEach-Object {
    $line = $_
    foreach ($key in $secretKeys) {
        if ($line -match "^$key=") { $line = "$key=$(New-Secret)" }
    }
    if ($line -match '^LANGFUSE_ENCRYPTION_KEY=') {
        $hex = New-Object byte[] 32
        [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($hex)
        $line = 'LANGFUSE_ENCRYPTION_KEY=' + (($hex | ForEach-Object { $_.ToString('x2') }) -join '')
    }
    if ($line -match '^LANGFUSE_PUBLIC_KEY=$') { $line = "LANGFUSE_PUBLIC_KEY=pk-lf-$([guid]::NewGuid())" }
    if ($line -match '^LANGFUSE_SECRET_KEY=$') { $line = "LANGFUSE_SECRET_KEY=sk-lf-$([guid]::NewGuid())" }
    $line
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($target, $lines, $utf8NoBom)
Write-Host "Created .env with fresh secrets. Paste your Mistral key into MISTRAL_API_KEY before starting." -ForegroundColor Green
