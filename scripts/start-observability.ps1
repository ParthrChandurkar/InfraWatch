param(
    [string]$Namespace = "infrawatch",
    [string]$GrafanaAdminPassword = "",
    [switch]$KeepFallback,
    [switch]$NoPortForward,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

$LokiChartVersion = "6.24.0"
$AlloyChartVersion = "1.13.0"

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

function Resolve-HelmCommand {
    $helmCommand = Get-Command helm -ErrorAction SilentlyContinue
    if ($helmCommand) {
        return $helmCommand.Source
    }

    $candidatePaths = @(
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\helm.exe"),
        "C:\ProgramData\chocolatey\bin\helm.exe"
    )

    foreach ($path in $candidatePaths) {
        if ($path -and (Test-Path -LiteralPath $path)) {
            return $path
        }
    }

    $wingetPackagesPath = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path -LiteralPath $wingetPackagesPath) {
        $wingetHelm = Get-ChildItem -Path $wingetPackagesPath -Directory -Filter "Helm.Helm_*" -ErrorAction SilentlyContinue |
            ForEach-Object {
                Join-Path $_.FullName "windows-amd64\helm.exe"
            } |
            Where-Object { Test-Path -LiteralPath $_ } |
            Select-Object -First 1

        if ($wingetHelm) {
            return $wingetHelm
        }
    }

    return ""
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

        $values[$parts[0].Trim()] = $parts[1].Trim().Trim('"').Trim("'")
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

function Test-TcpPort {
    param(
        [string]$HostName,
        [int]$Port
    )

    $client = [System.Net.Sockets.TcpClient]::new()

    try {
        $connectTask = $client.ConnectAsync($HostName, $Port)
        return $connectTask.Wait(800) -and $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Start-PortForward {
    param(
        [string]$Name,
        [string]$Resource,
        [int]$LocalPort,
        [int]$RemotePort
    )

    if (Test-TcpPort -HostName "127.0.0.1" -Port $LocalPort) {
        Write-Warn "$Name port-forward was not started because localhost:$LocalPort is already in use."
        return
    }

    $portMapping = "${LocalPort}:${RemotePort}"
    $arguments = @("port-forward", "--namespace", $Namespace, $Resource, $portMapping)
    $process = Start-Process -FilePath "kubectl" -ArgumentList $arguments -WindowStyle Hidden -PassThru
    Write-Host "  $Name : http://localhost:$LocalPort  (port-forward pid $($process.Id))"
}

function Apply-ConfigMapFromFiles {
    param(
        [string]$Name,
        [string[]]$FromFiles
    )

    $arguments = @("create", "configmap", $Name, "--namespace", $Namespace)

    foreach ($fromFile in $FromFiles) {
        $arguments += "--from-file=$fromFile"
    }

    $arguments += @("--dry-run=client", "-o", "yaml")

    $manifest = & kubectl @arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Could not build ConfigMap '$Name'."
        exit $LASTEXITCODE
    }

    $manifest | & kubectl apply -f -
    Assert-LastCommand "Could not apply ConfigMap '$Name'."
}

function Apply-GrafanaSecret {
    param([string]$Password)

    $manifest = & kubectl create secret generic infrawatch-grafana-admin `
        --namespace $Namespace `
        "--from-literal=admin-password=$Password" `
        --dry-run=client `
        -o yaml

    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Could not build the Grafana admin secret."
        exit $LASTEXITCODE
    }

    $manifest | & kubectl apply -f -
    Assert-LastCommand "Could not apply the Grafana admin secret."
}

function Test-HelmReleaseExists {
    param(
        [string]$HelmCommand,
        [string]$Release
    )

    return (Invoke-NativeQuiet -Command $HelmCommand -Arguments @("status", $Release, "--namespace", $Namespace)) -eq 0
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$EnvPath = Join-Path $RepoRoot ".env"

Set-Location -LiteralPath $RepoRoot

Write-Host ""
Write-Host "InfraWatch Kubernetes observability stack" -ForegroundColor Green
Write-Host ""

if (-not (Test-CommandAvailable "kubectl")) {
    Write-Fail "kubectl was not found. Install kubectl and run this script again."
    exit 1
}

$HelmCommand = Resolve-HelmCommand
if ([string]::IsNullOrWhiteSpace($HelmCommand)) {
    Write-Fail "helm was not found. Install Helm and run this script again."
    exit 1
}

Write-Info "Checking Kubernetes access"
if ((Invoke-NativeQuiet -Command "kubectl" -Arguments @("cluster-info")) -ne 0) {
    Write-Fail "Kubernetes is not reachable from the current kubectl context. Start Minikube first with .\scripts\start-k8s.ps1."
    exit 1
}

if ((Invoke-NativeQuiet -Command "kubectl" -Arguments @("get", "namespace", $Namespace)) -ne 0) {
    Write-Fail "Namespace '$Namespace' does not exist. Run .\scripts\start-k8s.ps1 first."
    exit 1
}

Write-Info "Checking Helm"
& $HelmCommand version --short
Assert-LastCommand "Helm is installed, but the version check failed."

if ($CheckOnly) {
    Write-Info "Check-only mode completed. No observability components were installed."
    exit 0
}

if ([string]::IsNullOrWhiteSpace($GrafanaAdminPassword)) {
    $EnvValues = Read-EnvFile -Path $EnvPath
    $GrafanaAdminPassword = Get-EnvValue -Values $EnvValues -Name "GRAFANA_ADMIN_PASSWORD" -Default ""
}

if ([string]::IsNullOrWhiteSpace($GrafanaAdminPassword) -or $GrafanaAdminPassword -eq "change-this-in-your-local-env") {
    $GrafanaAdminPassword = "infrawatch-local-admin"
    Write-Warn "Using default local Grafana password 'infrawatch-local-admin'. Change it for anything beyond local testing."
}

Write-Info "Adding Grafana Helm repository"
& $HelmCommand repo add grafana https://grafana.github.io/helm-charts
Assert-LastCommand "Could not add grafana Helm repository."

& $HelmCommand repo update
Assert-LastCommand "Could not update Helm repositories."

if (Test-HelmReleaseExists -HelmCommand $HelmCommand -Release "infrawatch") {
    Write-Info "Removing legacy kube-prometheus-stack release"
    & $HelmCommand uninstall infrawatch --namespace $Namespace --timeout 5m
    Assert-LastCommand "Could not remove the legacy kube-prometheus-stack release."
}

Write-Info "Installing/upgrading Loki"
& $HelmCommand upgrade --install infrawatch-loki grafana/loki `
    --namespace $Namespace `
    --version $LokiChartVersion `
    --values logging/loki/values.yaml `
    --wait `
    --timeout 8m
Assert-LastCommand "Loki Helm release failed."

Write-Info "Creating observability ConfigMaps"
Apply-ConfigMapFromFiles -Name "infrawatch-prometheus-config" -FromFiles @(
    "prometheus.yml=$(Join-Path $RepoRoot 'monitoring\prometheus\prometheus-k8s.yml')",
    "alert_rules.yml=$(Join-Path $RepoRoot 'monitoring\prometheus\alert_rules.yml')"
)

Apply-ConfigMapFromFiles -Name "infrawatch-alertmanager-config" -FromFiles @(
    "alertmanager.yml=$(Join-Path $RepoRoot 'monitoring\alertmanager\alertmanager.yml')"
)

Apply-ConfigMapFromFiles -Name "infrawatch-grafana-datasources" -FromFiles @(
    "datasources.yml=$(Join-Path $RepoRoot 'monitoring\grafana\provisioning\datasources\kubernetes.yml')"
)

Apply-ConfigMapFromFiles -Name "infrawatch-grafana-dashboard-provider" -FromFiles @(
    "default.yml=$(Join-Path $RepoRoot 'monitoring\grafana\provisioning\dashboards\default.yml')"
)

Apply-ConfigMapFromFiles -Name "infrawatch-grafana-dashboards" -FromFiles @(
    "cluster-overview.json=$(Join-Path $RepoRoot 'monitoring\grafana\dashboards\cluster-overview.json')",
    "hpa-overview.json=$(Join-Path $RepoRoot 'monitoring\grafana\dashboards\hpa-overview.json')",
    "service-health.json=$(Join-Path $RepoRoot 'monitoring\grafana\dashboards\service-health.json')"
)

Apply-GrafanaSecret -Password $GrafanaAdminPassword

Write-Info "Applying lightweight Prometheus, Grafana, Alertmanager, and kube-state-metrics"
& kubectl apply -k k8s/observability
Assert-LastCommand "Could not apply lightweight observability manifests."

foreach ($deployment in @(
    "deployment/infrawatch-prometheus",
    "deployment/infrawatch-alertmanager",
    "deployment/infrawatch-grafana",
    "deployment/infrawatch-kube-state-metrics"
)) {
    & kubectl rollout status $deployment --namespace $Namespace --timeout=240s
    Assert-LastCommand "$deployment did not become ready in time."
}

Write-Info "Installing/upgrading Grafana Alloy"
& $HelmCommand upgrade --install infrawatch-alloy grafana/alloy `
    --namespace $Namespace `
    --version $AlloyChartVersion `
    --values logging/alloy/values.yaml `
    --wait `
    --timeout 8m
Assert-LastCommand "Grafana Alloy Helm release failed."

if (-not $KeepFallback) {
    Write-Info "Switching InfraWatch backend to strict Prometheus/Loki observability"
    $Patch = @{
        data = @{
            INFRAWATCH_ALLOW_MOCK_OBSERVABILITY = "false"
            INFRAWATCH_PROMETHEUS_URL = "http://infrawatch-prometheus:9090"
            INFRAWATCH_LOKI_URL = "http://infrawatch-loki-gateway"
        }
    } | ConvertTo-Json -Compress

    $PatchFile = New-TemporaryFile
    try {
        Set-Content -LiteralPath $PatchFile -Value $Patch -Encoding UTF8
        & kubectl patch configmap infrawatch-backend-config --namespace $Namespace --type merge "--patch-file=$PatchFile"
        Assert-LastCommand "Could not patch the backend ConfigMap."
    }
    finally {
        Remove-Item -LiteralPath $PatchFile -Force -ErrorAction SilentlyContinue
    }

    & kubectl rollout restart deployment/infrawatch-backend --namespace $Namespace
    Assert-LastCommand "Could not restart the InfraWatch backend."

    & kubectl rollout status deployment/infrawatch-backend --namespace $Namespace --timeout=240s
    Assert-LastCommand "InfraWatch backend did not roll out after strict observability update."
}

Write-Info "Current observability pods"
& kubectl get pods --namespace $Namespace -l "app.kubernetes.io/part-of=infrawatch-observability"
& kubectl get pods --namespace $Namespace | Select-String "loki|alloy" | ForEach-Object { $_.Line }

Write-Host ""
Write-Host "Observability stack is installed." -ForegroundColor Green
Write-Host ""
Write-Host "Grafana login"
Write-Host "  Username: admin"
Write-Host "  Password: $GrafanaAdminPassword"
Write-Host ""

if (-not $NoPortForward) {
    Write-Host "Starting local port-forwards" -ForegroundColor Green
    Start-PortForward -Name "Grafana" -Resource "svc/infrawatch-grafana" -LocalPort 3001 -RemotePort 80
    Start-PortForward -Name "Prometheus" -Resource "svc/infrawatch-prometheus" -LocalPort 9090 -RemotePort 9090
    Start-PortForward -Name "Alertmanager" -Resource "svc/infrawatch-alertmanager" -LocalPort 9093 -RemotePort 9093
    Start-PortForward -Name "Loki" -Resource "svc/infrawatch-loki-gateway" -LocalPort 3100 -RemotePort 80
}
else {
    Write-Host "Port-forward commands"
    Write-Host "  kubectl port-forward --namespace $Namespace svc/infrawatch-grafana 3001:80"
    Write-Host "  kubectl port-forward --namespace $Namespace svc/infrawatch-prometheus 9090:9090"
    Write-Host "  kubectl port-forward --namespace $Namespace svc/infrawatch-alertmanager 9093:9093"
    Write-Host "  kubectl port-forward --namespace $Namespace svc/infrawatch-loki-gateway 3100:80"
}

Write-Host ""
Write-Host "Local URLs"
Write-Host "  Grafana      : http://localhost:3001"
Write-Host "  Prometheus   : http://localhost:9090"
Write-Host "  Alertmanager : http://localhost:9093"
Write-Host "  Loki         : http://localhost:3100"
