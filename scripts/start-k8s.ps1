param(
    [string]$ImageRepository = "parthchn178",
    [string]$PostgresPassword = "",
    [switch]$StrictObservability,
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

function Test-CommandAvailable {
    param([string]$Name)

    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-NativeQuiet {
    param(
        [string]$Command,
        [string[]]$Arguments
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    try {
        & $Command @Arguments > $null 2>&1
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Assert-LastCommand {
    param([string]$Message)

    if ($LASTEXITCODE -ne 0) {
        Write-Fail $Message
        exit $LASTEXITCODE
    }
}

function New-LocalPassword {
    $bytes = [byte[]]::new(18)
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()

    try {
        $rng.GetBytes($bytes)
        return [Convert]::ToBase64String($bytes).Replace("+", "p").Replace("/", "s").TrimEnd("=")
    }
    finally {
        $rng.Dispose()
    }
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Namespace = "infrawatch"

Set-Location -LiteralPath $RepoRoot

Write-Host ""
Write-Host "InfraWatch local Kubernetes stack" -ForegroundColor Green
Write-Host ""

foreach ($command in @("docker", "kubectl", "minikube")) {
    if (-not (Test-CommandAvailable $command)) {
        Write-Fail "$command was not found. Install $command and run this script again."
        exit 1
    }
}

Write-Info "Checking Docker daemon"
$DockerInfoExitCode = Invoke-NativeQuiet -Command "docker" -Arguments @("info")
if ($DockerInfoExitCode -ne 0) {
    Write-Fail "Docker is installed, but the Docker daemon is not reachable. Start Docker Desktop and try again."
    exit 1
}

Write-Info "Checking kubectl client"
$KubectlVersionExitCode = Invoke-NativeQuiet -Command "kubectl" -Arguments @("version", "--client")
if ($KubectlVersionExitCode -ne 0) {
    Write-Fail "kubectl is installed, but the client check failed."
    exit 1
}

Write-Info "Checking Minikube"
$MinikubeVersionExitCode = Invoke-NativeQuiet -Command "minikube" -Arguments @("version")
if ($MinikubeVersionExitCode -ne 0) {
    Write-Fail "minikube is installed, but the version check failed."
    exit 1
}

if ($CheckOnly) {
    Write-Info "Check-only mode completed. The Kubernetes stack was not changed."
    Write-Host ""
    Write-Host "Next command:" -ForegroundColor Green
    Write-Host "  .\scripts\start-k8s.ps1"
    exit 0
}

Write-Info "Starting Minikube if it is not already running"
$MinikubeStatus = (& minikube status "--format={{.Host}}" 2>$null | Select-Object -First 1)
if ($LASTEXITCODE -ne 0 -or $MinikubeStatus -ne "Running") {
    & minikube start
    Assert-LastCommand "Minikube failed to start."
}
else {
    Write-Host "Minikube is already running."
}

Write-Info "Using Minikube kubectl context"
& kubectl config use-context minikube
Assert-LastCommand "Could not switch kubectl context to minikube."

Write-Info "Enabling Minikube metrics-server addon"
& minikube addons enable metrics-server
if ($LASTEXITCODE -ne 0) {
    Write-Warn "metrics-server addon could not be enabled automatically. InfraWatch can still start, but kubectl top/HPA metrics may be delayed."
}

if ([string]::IsNullOrWhiteSpace($PostgresPassword)) {
    $PostgresPassword = New-LocalPassword
    Write-Warn "Generated a local PostgreSQL password for this cluster. It is stored only in the Kubernetes secret."
}

$EncodedPostgresPassword = [System.Uri]::EscapeDataString($PostgresPassword)
$DatabaseUrl = "postgresql://infrawatch:$EncodedPostgresPassword@infrawatch-postgres:5432/infrawatch"

Write-Info "Creating namespace"
& kubectl apply -f k8s/namespace.yaml
Assert-LastCommand "Could not create or update the infrawatch namespace."

Write-Info "Creating PostgreSQL connection secret"
$SecretYaml = & kubectl create secret generic infrawatch-secrets `
    --namespace $Namespace `
    "--from-literal=POSTGRES_PASSWORD=$PostgresPassword" `
    "--from-literal=DATABASE_URL=$DatabaseUrl" `
    --dry-run=client `
    -o yaml
Assert-LastCommand "Could not build the infrawatch-secrets manifest."

$SecretYaml | & kubectl apply -f -
Assert-LastCommand "Could not apply the infrawatch-secrets secret."

Write-Info "Applying InfraWatch Kubernetes manifests"
& kubectl apply -k k8s
Assert-LastCommand "Could not apply InfraWatch Kubernetes manifests."

$AllowMockObservability = if ($StrictObservability) { "false" } else { "true" }
if ($StrictObservability) {
    Write-Warn "Strict observability is enabled. Install Prometheus, Loki, and Alloy first or metrics/log API calls may fail."
}
else {
    Write-Warn "Mock observability fallback is enabled for local K8s so the dashboard stays useful before Prometheus/Loki are installed."
}

Write-Info "Configuring backend observability mode"
$Patch = '{"data":{"INFRAWATCH_ALLOW_MOCK_OBSERVABILITY":"' + $AllowMockObservability + '"}}'
& kubectl patch configmap infrawatch-backend-config --namespace $Namespace --type merge --patch $Patch
Assert-LastCommand "Could not patch the backend ConfigMap."

$CleanImageRepository = $ImageRepository.Trim().Trim("/")
if ([string]::IsNullOrWhiteSpace($CleanImageRepository)) {
    Write-Fail "ImageRepository cannot be empty. Use a DockerHub username such as 'parthchn178'."
    exit 1
}

$BackendImage = "docker.io/$CleanImageRepository/infrawatch-backend:latest"
$FrontendImage = "docker.io/$CleanImageRepository/infrawatch-frontend:latest"

Write-Info "Rolling out DockerHub images"
& kubectl set image deployment/infrawatch-backend "backend=$BackendImage" --namespace $Namespace
Assert-LastCommand "Could not set the backend image."

& kubectl set image deployment/infrawatch-frontend "frontend=$FrontendImage" --namespace $Namespace
Assert-LastCommand "Could not set the frontend image."

& kubectl rollout restart deployment/infrawatch-backend --namespace $Namespace
Assert-LastCommand "Could not restart the backend deployment."

Write-Info "Waiting for rollouts"
& kubectl rollout status statefulset/infrawatch-postgres --namespace $Namespace --timeout=240s
Assert-LastCommand "PostgreSQL did not become ready in time."

& kubectl rollout status deployment/infrawatch-backend --namespace $Namespace --timeout=240s
Assert-LastCommand "Backend did not become ready in time."

& kubectl rollout status deployment/infrawatch-frontend --namespace $Namespace --timeout=240s
Assert-LastCommand "Frontend did not become ready in time."

Write-Info "Collecting local dashboard URL"
$DashboardUrl = (& minikube service infrawatch-frontend --namespace $Namespace --url 2>$null | Select-Object -First 1)

Write-Host ""
Write-Host "InfraWatch is running on local Kubernetes." -ForegroundColor Green
Write-Host ""

if (-not [string]::IsNullOrWhiteSpace($DashboardUrl)) {
    Write-Host "  Dashboard : $DashboardUrl"
}
else {
    Write-Host "  Dashboard : run 'minikube service infrawatch-frontend --namespace infrawatch --url'"
}

Write-Host ""
Write-Host "Useful commands"
Write-Host "  kubectl get pods,svc --namespace infrawatch"
Write-Host "  kubectl logs deployment/infrawatch-backend --namespace infrawatch"
Write-Host "  kubectl port-forward svc/infrawatch-backend --namespace infrawatch 8000:8000"
Write-Host ""
Write-Host "If you install the Terraform observability stack, Grafana and Prometheus can be opened with:"
Write-Host "  kubectl port-forward svc/infrawatch-grafana --namespace infrawatch 3001:80"
Write-Host "  kubectl port-forward svc/infrawatch-kube-prometheus-prometheus --namespace infrawatch 9090:9090"
