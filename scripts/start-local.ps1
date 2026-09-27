param(
    [switch]$Detached,
    [switch]$NoBuild,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

function Write-Info {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Warn {
    param([string]$Message)
    Write-Host "WARN: $Message" -ForegroundColor Yellow
}

function Write-Fail {
    param([string]$Message)
    Write-Host "ERROR: $Message" -ForegroundColor Red
}

function Read-EnvFile {
    param([string]$Path)

    $values = @{}

    if (-not (Test-Path -LiteralPath $Path)) {
        return $values
    }

    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()

        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
            continue
        }

        $parts = $trimmed -split "=", 2

        if ($parts.Count -ne 2) {
            continue
        }

        $key = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        $values[$key] = $value
    }

    return $values
}

function Get-EnvValue {
    param(
        [hashtable]$Values,
        [string]$Name,
        [string]$Default
    )

    if ($Values.ContainsKey($Name) -and $Values[$Name]) {
        return $Values[$Name]
    }

    return $Default
}

function Test-CommandAvailable {
    param([string]$Name)

    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-DockerQuiet {
    param([string[]]$Arguments)

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    try {
        & docker @Arguments > $null 2>&1
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$EnvExamplePath = Join-Path $RepoRoot ".env.example"
$EnvPath = Join-Path $RepoRoot ".env"

Set-Location -LiteralPath $RepoRoot

Write-Host ""
Write-Host "InfraWatch local stack" -ForegroundColor Green
Write-Host ""

if (-not (Test-CommandAvailable "docker")) {
    Write-Fail "Docker CLI was not found. Install Docker Desktop, open it, and run this script again."
    exit 1
}

Write-Info "Checking Docker daemon"
$DockerInfoExitCode = Invoke-DockerQuiet -Arguments @("info")
if ($DockerInfoExitCode -ne 0) {
    Write-Fail "Docker is installed, but the Docker daemon is not reachable. Start Docker Desktop and try again."
    exit 1
}

Write-Info "Checking Docker Compose"
$DockerComposeExitCode = Invoke-DockerQuiet -Arguments @("compose", "version")
if ($DockerComposeExitCode -ne 0) {
    Write-Fail "Docker Compose is not available. Update Docker Desktop or install the Docker Compose plugin."
    exit 1
}

if (-not (Test-Path -LiteralPath $EnvPath)) {
    if (-not (Test-Path -LiteralPath $EnvExamplePath)) {
        Write-Fail ".env.example was not found. Cannot create local .env file."
        exit 1
    }

    Copy-Item -LiteralPath $EnvExamplePath -Destination $EnvPath
    Write-Warn "Created .env from .env.example. For personal use, change POSTGRES_PASSWORD and GRAFANA_ADMIN_PASSWORD in .env."
}

$EnvValues = Read-EnvFile -Path $EnvPath

if (
    (Get-EnvValue -Values $EnvValues -Name "POSTGRES_PASSWORD" -Default "") -eq "change-this-in-your-local-env" -or
    (Get-EnvValue -Values $EnvValues -Name "GRAFANA_ADMIN_PASSWORD" -Default "") -eq "change-this-in-your-local-env"
) {
    Write-Warn "Default local passwords are still present in .env. This is acceptable for a private local demo only."
}

$FrontendPort = Get-EnvValue -Values $EnvValues -Name "FRONTEND_PORT" -Default "3000"
$BackendPort = Get-EnvValue -Values $EnvValues -Name "BACKEND_PORT" -Default "8000"
$PrometheusPort = Get-EnvValue -Values $EnvValues -Name "PROMETHEUS_PORT" -Default "9090"
$GrafanaPort = Get-EnvValue -Values $EnvValues -Name "GRAFANA_PORT" -Default "3001"
$LokiPort = Get-EnvValue -Values $EnvValues -Name "LOKI_PORT" -Default "3100"
$AlloyPort = Get-EnvValue -Values $EnvValues -Name "ALLOY_PORT" -Default "12345"
$AlertmanagerPort = Get-EnvValue -Values $EnvValues -Name "ALERTMANAGER_PORT" -Default "9093"
$GrafanaUser = Get-EnvValue -Values $EnvValues -Name "GRAFANA_ADMIN_USER" -Default "admin"

Write-Info "Validating Docker Compose configuration"
$DockerComposeConfigExitCode = Invoke-DockerQuiet -Arguments @("compose", "config", "--quiet")
if ($DockerComposeConfigExitCode -ne 0) {
    Write-Fail "Docker Compose configuration is invalid. Check docker-compose.yml and .env."
    exit 1
}

Write-Host ""
Write-Host "Local URLs" -ForegroundColor Green
Write-Host "  InfraWatch dashboard : http://localhost:$FrontendPort"
Write-Host "  FastAPI docs         : http://localhost:$BackendPort/docs"
Write-Host "  Grafana              : http://localhost:$GrafanaPort"
Write-Host "  Prometheus           : http://localhost:$PrometheusPort"
Write-Host "  Loki                 : http://localhost:$LokiPort"
Write-Host "  Grafana Alloy        : http://localhost:$AlloyPort"
Write-Host "  Alertmanager         : http://localhost:$AlertmanagerPort"
Write-Host ""
Write-Host "Grafana login"
Write-Host "  Username: $GrafanaUser"
Write-Host "  Password: value of GRAFANA_ADMIN_PASSWORD in .env"
Write-Host ""

if ($CheckOnly) {
    Write-Info "Check-only mode completed. The stack was not started."
    exit 0
}

$ComposeArgs = @("compose", "up")

if (-not $NoBuild) {
    $ComposeArgs += "--build"
}

if ($Detached) {
    $ComposeArgs += "-d"
    Write-Info "Starting InfraWatch in detached mode"
}
else {
    Write-Info "Starting InfraWatch in foreground mode. Press Ctrl+C to stop logs."
}

& docker @ComposeArgs
exit $LASTEXITCODE
