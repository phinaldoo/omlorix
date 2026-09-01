param(
    [string]$ServerHome = "",
    [switch]$SetupOnly
)

$ErrorActionPreference = "Stop"

function Write-Step($Message) {
    Write-Host "==> $Message"
}

function Set-RandomBytes([byte[]]$Buffer) {
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($Buffer)
    } finally {
        $generator.Dispose()
    }
}

function New-Secret([int]$Bytes = 48) {
    $buffer = [byte[]]::new($Bytes)
    Set-RandomBytes $buffer
    return [Convert]::ToBase64String($buffer)
}

function New-UrlSecret([int]$Bytes = 48) {
    return (New-Secret $Bytes).Replace("+", "-").Replace("/", "_").Replace("=", "")
}

function ConvertTo-UrlComponent([string]$Value) {
    $encoded = [System.Text.StringBuilder]::new()

    # Encode UTF-8 bytes explicitly so Windows PowerShell and modern PowerShell
    # use the same RFC 3986 unreserved set as setup.sh and the other launchers.
    foreach ($currentByte in [System.Text.Encoding]::UTF8.GetBytes($Value)) {
        if (
            ($currentByte -ge [byte][char]'a' -and $currentByte -le [byte][char]'z') -or
            ($currentByte -ge [byte][char]'A' -and $currentByte -le [byte][char]'Z') -or
            ($currentByte -ge [byte][char]'0' -and $currentByte -le [byte][char]'9') -or
            $currentByte -in @([byte][char]'-', [byte][char]'.', [byte][char]'_', [byte][char]'~')
        ) {
            [void]$encoded.Append([char]$currentByte)
        } else {
            [void]$encoded.Append('%')
            [void]$encoded.Append($currentByte.ToString('X2'))
        }
    }
    return $encoded.ToString()
}

function New-FernetKey {
    $buffer = [byte[]]::new(32)
    Set-RandomBytes $buffer
    return [Convert]::ToBase64String($buffer).Replace("+", "-").Replace("/", "_")
}

function Get-DefaultGrafanaAdminUser {
    return "omlorix-admin"
}

function Set-EnvValue($Path, $Key, $Value) {
    if (-not (Test-Path $Path)) {
        New-Item -ItemType File -Path $Path -Force | Out-Null
    }
    $lines = Get-Content -Path $Path -ErrorAction SilentlyContinue
    $updated = $false
    $next = foreach ($line in $lines) {
        if ($line -match "^$([regex]::Escape($Key))=") {
            "$Key=$Value"
            $updated = $true
        } else {
            $line
        }
    }
    if (-not $updated) {
        $next += "$Key=$Value"
    }
    Set-Content -Path $Path -Value $next -Encoding UTF8
}

function ConvertFrom-DoubleQuotedEnvValue([string]$Value) {
    $decoded = [System.Text.StringBuilder]::new()
    $escaped = $false

    # Start after the opening quote. Escaped quotes and backslashes are value
    # bytes; the first unescaped quote terminates the dotenv value.
    for ($i = 1; $i -lt $Value.Length; $i++) {
        $character = $Value[$i]
        if ($escaped) {
            if ($character -eq [char]34 -or $character -eq [char]92) {
                [void]$decoded.Append($character)
            } else {
                [void]$decoded.Append([char]92)
                [void]$decoded.Append($character)
            }
            $escaped = $false
        } elseif ($character -eq [char]92) {
            $escaped = $true
        } elseif ($character -eq [char]34) {
            return $decoded.ToString()
        } else {
            [void]$decoded.Append($character)
        }
    }

    if ($escaped) {
        [void]$decoded.Append([char]92)
    }
    return $decoded.ToString()
}

function Get-EnvValue($Path, $Key) {
    if (-not (Test-Path $Path)) {
        return ""
    }
    $line = Get-Content -Path $Path -ErrorAction SilentlyContinue |
        Where-Object { $_ -match "^$([regex]::Escape($Key))=" } |
        Select-Object -Last 1
    if (-not $line) {
        return ""
    }
    $value = ($line -split "=", 2)[1].Trim()
    if ($value.StartsWith('"')) {
        return ConvertFrom-DoubleQuotedEnvValue $value
    }
    if ($value.StartsWith("'")) {
        $closingIndex = $value.IndexOf([char]39, 1)
        if ($closingIndex -ge 0) {
            return $value.Substring(1, $closingIndex - 1)
        }
        return $value.Substring(1)
    }

    # In an unquoted Compose value, `#` starts a comment only when whitespace
    # precedes it. A hash inside a password is otherwise literal input.
    for ($i = 1; $i -lt $value.Length; $i++) {
        if ($value[$i] -eq [char]35 -and [char]::IsWhiteSpace($value[$i - 1])) {
            return $value.Substring(0, $i).Trim()
        }
    }
    return $value
}

function Sync-LocalRedisUrl($EnvPath, $RedisPassword) {
    $redisUrl = Get-EnvValue $EnvPath "REDIS_URL"
    $encodedRedisPassword = ConvertTo-UrlComponent $RedisPassword
    $expectedRedisUrl = "redis://:$encodedRedisPassword@redis:6379/0"

    # The bundled service is reachable through Compose DNS, and its URL is
    # derived state that must change whenever the password changes.
    if ($redisUrl -ne $expectedRedisUrl) {
        Set-EnvValue $EnvPath "REDIS_URL" "`"$expectedRedisUrl`""
    }
}

function Read-Toggle($EnvPath, $Key) {
    $value = Get-EnvValue $EnvPath $Key
    if ($null -eq $value) {
        $value = ""
    }
    $lower = $value.Trim().ToLower()
    return $lower -in @("1", "true", "yes", "on")
}

function Read-RedisEnabled($EnvPath) {
    # Match the application default so older env files without this key keep
    # Redis enabled until the operator explicitly selects Off.
    $value = Get-EnvValue $EnvPath "REDIS_ENABLED"
    if (-not $value) {
        return $true
    }
    return Read-Toggle $EnvPath "REDIS_ENABLED"
}

function Ensure-MinioEnv($EnvPath) {
    $minioUser = Get-EnvValue $EnvPath "MINIO_ROOT_USER"
    $minioPassword = Get-EnvValue $EnvPath "MINIO_ROOT_PASSWORD"
    if (-not $minioUser -or $minioUser -eq "CHANGE_ME") {
        $suffix = (New-Secret 18).Replace("+", "").Replace("/", "").Replace("=", "")
        Set-EnvValue $EnvPath "MINIO_ROOT_USER" "`"omlorix-$suffix`""
    }
    if (-not $minioPassword -or $minioPassword -eq "CHANGE_ME") {
        Set-EnvValue $EnvPath "MINIO_ROOT_PASSWORD" "`"$(New-Secret)`""
    }
}

function Require-ExternalDbEnv($EnvPath) {
    if (-not (Get-EnvValue $EnvPath "DATABASE_URL")) {
        throw "External database requires DATABASE_URL in $EnvPath."
    }
}

function Require-ExternalRedisEnv($EnvPath) {
    $redisUrl = Get-EnvValue $EnvPath "REDIS_URL"
    if (-not $redisUrl) {
        throw "External Redis requires REDIS_URL in $EnvPath."
    }
    if ($redisUrl -match "CHANGE_ME" -or $redisUrl -match "redis(s)?://.*(localhost|127\.0\.0\.1):" -or $redisUrl -eq "redis://redis:6379/0") {
        throw "External Redis requires REDIS_URL to point to your external Redis service, not a placeholder or localhost."
    }
}

function Require-ExternalStorageEnv($EnvPath) {
    $storageProvider = Get-EnvValue $EnvPath "FILE_STORAGE_PROVIDER"
    if (-not $storageProvider -or $storageProvider -eq "local") {
        throw "External storage requires FILE_STORAGE_PROVIDER to be s3, gcs, azure, or webdav."
    }
}

function Build-ComposeProfiles($EnvPath) {
    $profiles = @()
    $redisEnabled = Read-RedisEnabled $EnvPath
    if (Read-Toggle $EnvPath "OMLORIX_USE_BUNDLED_DB") {
        $profiles += "bundled-db"
    }
    if ($redisEnabled) {
        $profiles += "redis-enabled"
    }
    if ($redisEnabled -and (Read-Toggle $EnvPath "OMLORIX_USE_BUNDLED_REDIS")) {
        $profiles += "bundled-redis"
    }
    if (Read-Toggle $EnvPath "OMLORIX_USE_PGBOUNCER") {
        $profiles += "pgbouncer"
    }
    if (Read-Toggle $EnvPath "OMLORIX_USE_BUNDLED_STORAGE") {
        $profiles += "bundled-storage"
    }
    return $profiles -join ","
}

function Get-ComposeArgs($EnvPath) {
    $bundledDb = Read-Toggle $EnvPath "OMLORIX_USE_BUNDLED_DB"
    $bundledRedis = Read-Toggle $EnvPath "OMLORIX_USE_BUNDLED_REDIS"
    $redisEnabled = Read-RedisEnabled $EnvPath
    $bundledStorage = Read-Toggle $EnvPath "OMLORIX_USE_BUNDLED_STORAGE"
    $usePgbouncer = Read-Toggle $EnvPath "OMLORIX_USE_PGBOUNCER"
    $mode = (Get-EnvValue $EnvPath "MODE").Trim().ToLower()

    if (
        -not $bundledDb -and
        (-not $redisEnabled -or -not $bundledRedis) -and
        -not $bundledStorage -and
        -not $usePgbouncer
    ) {
        $base = "docker-compose.managed-cloud.yml"
    } else {
        $base = "docker-compose.server.yml"
    }

    $files = @("-f", $base, "-f", "docker-compose.frontend-port.yml")
    if ($mode -eq "dev" -and ($bundledDb -or ($redisEnabled -and $bundledRedis))) {
        $files += @("-f", "docker-compose.dev-ports.yml")
    }
    return $files
}

function Ensure-Docker {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) {
        throw "Docker is not installed. Install Docker Desktop and retry."
    }
    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker is not running. Start Docker Desktop and retry."
    }
    docker compose version *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose is not available."
    }
}

function Open-OmlorixUrl($Url) {
    $preference = if ($env:OMLORIX_OPEN_BROWSER) { $env:OMLORIX_OPEN_BROWSER } else { "auto" }
    if ($preference -in @("0", "false", "False", "no", "NO")) {
        return
    }
    try {
        Start-Process $Url | Out-Null
    } catch {
        # Headless or locked-down hosts can still use the printed URL.
    }
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Resolve-Path (Join-Path $scriptDir "..\..")
if ($ServerHome) {
    $rootDir = Resolve-Path $ServerHome
}
$envFile = if ($env:OMLORIX_ENV_FILE) { $env:OMLORIX_ENV_FILE } else { Join-Path $rootDir ".env" }
$exampleEnv = Join-Path $rootDir ".env.example"

Set-Location $rootDir

if (-not (Test-Path $envFile)) {
    if (Test-Path $exampleEnv) {
        Copy-Item $exampleEnv $envFile
    } else {
        New-Item -ItemType File -Path $envFile -Force | Out-Null
    }
}

$content = Get-Content -Path $envFile -Raw
if ($content -match 'JWT_SECRET_KEY=""' -or $content -notmatch '(?m)^JWT_SECRET_KEY=') {
    Set-EnvValue $envFile "JWT_SECRET_KEY" "`"$(New-Secret 64)`""
}
if ($content -match 'ENCRYPTION_KEY=""' -or $content -notmatch '(?m)^ENCRYPTION_KEY=') {
    Set-EnvValue $envFile "ENCRYPTION_KEY" "`"$(New-FernetKey)`""
}
$passwordResetSalt = Get-EnvValue $envFile "PASSWORD_RESET_IDENTIFIER_HASH_SALT"
if (-not $passwordResetSalt -or $passwordResetSalt.Length -lt 16) {
    Set-EnvValue $envFile "PASSWORD_RESET_IDENTIFIER_HASH_SALT" "`"$(New-UrlSecret 32)`""
}
if (-not (Get-EnvValue $envFile "BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE")) {
    Set-EnvValue $envFile "BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE" "`"$(New-Secret)`""
}
if ($content -match 'DATABASE_PASSWORD=("CHANGE_ME"|CHANGE_ME)' -or $content -notmatch '(?m)^DATABASE_PASSWORD=') {
    Set-EnvValue $envFile "DATABASE_PASSWORD" "`"$(New-Secret)`""
}
if ($content -match 'REDIS_PASSWORD=("CHANGE_ME"|CHANGE_ME)' -or $content -notmatch '(?m)^REDIS_PASSWORD=') {
    Set-EnvValue $envFile "REDIS_PASSWORD" "`"$(New-UrlSecret)`""
}

if ($content -match '(?m)^GRAFANA_ADMIN_USER=("CHANGE_ME"|CHANGE_ME|"admin"|admin)(\s*#.*)?$' -or $content -notmatch '(?m)^GRAFANA_ADMIN_USER=') {
    Set-EnvValue $envFile "GRAFANA_ADMIN_USER" "`"$(Get-DefaultGrafanaAdminUser)`""
}
if ($content -match '(?m)^GRAFANA_ADMIN_PASSWORD=("CHANGE_ME"|CHANGE_ME)(\s*#.*)?$' -or $content -notmatch '(?m)^GRAFANA_ADMIN_PASSWORD=') {
    Set-EnvValue $envFile "GRAFANA_ADMIN_PASSWORD" "`"$(New-Secret)`""
}

# Set default toggles if absent BEFORE validation so the effective
# configuration is known before running external-service checks.
if (-not (Get-EnvValue $envFile "OMLORIX_USE_BUNDLED_DB")) {
    Set-EnvValue $envFile "OMLORIX_USE_BUNDLED_DB" "true"
}
if (-not (Get-EnvValue $envFile "OMLORIX_USE_BUNDLED_REDIS")) {
    Set-EnvValue $envFile "OMLORIX_USE_BUNDLED_REDIS" "true"
}
if (-not (Get-EnvValue $envFile "REDIS_ENABLED")) {
    Set-EnvValue $envFile "REDIS_ENABLED" "true"
}
if (-not (Get-EnvValue $envFile "OMLORIX_USE_PGBOUNCER")) {
    Set-EnvValue $envFile "OMLORIX_USE_PGBOUNCER" "false"
}
if (-not (Get-EnvValue $envFile "OMLORIX_USE_BUNDLED_STORAGE")) {
    Set-EnvValue $envFile "OMLORIX_USE_BUNDLED_STORAGE" "false"
}

# Recompute toggles after setting defaults so validation is correct.
$bundledDb = Read-Toggle $envFile "OMLORIX_USE_BUNDLED_DB"
$bundledRedis = Read-Toggle $envFile "OMLORIX_USE_BUNDLED_REDIS"
$redisEnabled = Read-RedisEnabled $envFile
$bundledStorage = Read-Toggle $envFile "OMLORIX_USE_BUNDLED_STORAGE"
$usePgbouncer = Read-Toggle $envFile "OMLORIX_USE_PGBOUNCER"

if (-not $bundledDb -and $usePgbouncer) {
    Set-EnvValue $envFile "OMLORIX_USE_PGBOUNCER" "false"
    $usePgbouncer = $false
}
if ($bundledDb) {
    Set-EnvValue $envFile "DATABASE_URL" '""'
    if ($usePgbouncer) {
        Set-EnvValue $envFile "DATABASE_HOST_OVERRIDE" "pgbouncer"
    } else {
        Set-EnvValue $envFile "DATABASE_HOST_OVERRIDE" "postgres"
    }
    Set-EnvValue $envFile "DATABASE_PORT_OVERRIDE" "5432"
    Set-EnvValue $envFile "DATABASE_MIGRATION_HOST_OVERRIDE" "postgres"
    Set-EnvValue $envFile "DATABASE_MIGRATION_PORT_OVERRIDE" "5432"
}
if ($usePgbouncer) {
    $poolMode = (Get-EnvValue $envFile "PGBOUNCER_POOL_MODE").Trim().ToLower()
    if ($poolMode -and $poolMode -notin @("transaction", "session")) {
        throw "PGBOUNCER_POOL_MODE must be transaction or session."
    }
}

if ($redisEnabled -and $bundledRedis) {
    Sync-LocalRedisUrl $envFile (Get-EnvValue $envFile "REDIS_PASSWORD")
}
if ($bundledStorage) {
    Ensure-MinioEnv $envFile
}

if ((-not $bundledStorage) -and (-not $bundledDb) -and (-not $redisEnabled -or -not $bundledRedis)) {
    Require-ExternalStorageEnv $envFile
}
if (-not $bundledDb) {
    Require-ExternalDbEnv $envFile
}
if ($redisEnabled -and -not $bundledRedis) {
    Require-ExternalRedisEnv $envFile
}

if (-not (Get-EnvValue $envFile "FRONTEND_HTTP_HOST_PORT")) {
    Set-EnvValue $envFile "FRONTEND_HTTP_HOST_PORT" "8080"
}

if ($SetupOnly -or $env:OMLORIX_SETUP_ONLY -eq "1") {
    Write-Step "Setup complete. Review .env before starting Omlorix."
    exit 0
}

Ensure-Docker

$composeFiles = Get-ComposeArgs $envFile
$composeProfiles = Build-ComposeProfiles $envFile

$env:COMPOSE_PROFILES = $composeProfiles

Write-Step "Pulling and starting Omlorix release stack"
docker compose --env-file $envFile @composeFiles pull
docker compose --env-file $envFile @composeFiles up -d --remove-orphans

$port = Get-EnvValue $envFile "FRONTEND_HTTP_HOST_PORT"
if (-not $port) { $port = "8080" }
$url = "http://localhost:$port"
Write-Step "Omlorix is starting at $url"
Open-OmlorixUrl $url
