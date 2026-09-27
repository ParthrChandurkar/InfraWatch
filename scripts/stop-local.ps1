param(
    [switch]$Volumes
)

$ErrorActionPreference = "Stop"

function Test-CommandAvailable {
    param([string]$Name)

    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Write-Fail {
    param([string]$Message)
    Write-Host "ERROR: $Message" -ForegroundColor Red
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
Set-Location -LiteralPath $RepoRoot

Write-Host ""
Write-Host "Stopping InfraWatch local stack" -ForegroundColor Green
Write-Host ""

if (-not (Test-CommandAvailable "docker")) {
    Write-Fail "Docker CLI was not found. Install Docker Desktop before using this script."
    exit 1
}

$DockerInfoExitCode = Invoke-DockerQuiet -Arguments @("info")
if ($DockerInfoExitCode -ne 0) {
    Write-Fail "Docker is installed, but the Docker daemon is not reachable. Start Docker Desktop if you need to stop a running stack."
    exit 1
}

$ComposeArgs = @("compose", "down")

if ($Volumes) {
    $ComposeArgs += "--volumes"
    $ComposeArgs += "--remove-orphans"
    Write-Host "This will also remove local Docker volumes for InfraWatch." -ForegroundColor Yellow
}

& docker @ComposeArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "InfraWatch stopped." -ForegroundColor Green

if ($Volumes) {
    Write-Host "Local volumes were removed."
}
