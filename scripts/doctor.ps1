param(
    [int]$TimeoutSeconds = 3,
    [string]$Namespace = "infrawatch"
)

$ErrorActionPreference = "Stop"
$script:Failures = 0
$script:Warnings = 0

function Write-Pass {
    param([string]$Message)
    Write-Host "[PASS] $Message" -ForegroundColor Green
}

function Write-WarnCheck {
    param([string]$Message)
    $script:Warnings += 1
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Write-FailCheck {
    param([string]$Message)
    $script:Failures += 1
    Write-Host "[FAIL] $Message" -ForegroundColor Red
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

function Invoke-NativeCapture {
    param(
        [string]$Command,
        [string[]]$Arguments
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    try {
        $output = & $Command @Arguments 2>$null
        return @{
            ExitCode = $LASTEXITCODE
            Output = $output
        }
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
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

function Test-HttpEndpoint {
    param(
        [string]$Name,
        [string]$Url
    )

    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSeconds
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
            Write-Pass "$Name is reachable at $Url"
        }
        else {
            Write-WarnCheck "$Name returned HTTP $($response.StatusCode) at $Url"
        }
    }
    catch {
        Write-WarnCheck "$Name is not reachable at $Url"
    }
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$EnvPath = Join-Path $RepoRoot ".env"

Set-Location -LiteralPath $RepoRoot

Write-Host ""
Write-Host "InfraWatch Doctor" -ForegroundColor Green
Write-Host ""

foreach ($path in @(
    "docker-compose.yml",
    ".env.example",
    "k8s/kustomization.yaml",
    "scripts/start-local.ps1",
    "scripts/stop-local.ps1",
    "scripts/start-k8s.ps1",
    "scripts/stop-k8s.ps1",
    "scripts/start-observability.ps1",
    "scripts/doctor.ps1"
)) {
    if (Test-Path -LiteralPath (Join-Path $RepoRoot $path)) {
        Write-Pass "Found $path"
    }
    else {
        Write-FailCheck "Missing $path"
    }
}

$EnvValues = Read-EnvFile -Path $EnvPath
if (Test-Path -LiteralPath $EnvPath) {
    Write-Pass "Found local .env"
}
else {
    Write-WarnCheck ".env is missing. Run .\scripts\start-local.ps1 or copy .env.example to .env."
}

$FrontendPort = Get-EnvValue -Values $EnvValues -Name "FRONTEND_PORT" -Default "3000"
$BackendPort = Get-EnvValue -Values $EnvValues -Name "BACKEND_PORT" -Default "8000"
$PrometheusPort = Get-EnvValue -Values $EnvValues -Name "PROMETHEUS_PORT" -Default "9090"
$GrafanaPort = Get-EnvValue -Values $EnvValues -Name "GRAFANA_PORT" -Default "3001"
$LokiPort = Get-EnvValue -Values $EnvValues -Name "LOKI_PORT" -Default "3100"
$AlertmanagerPort = Get-EnvValue -Values $EnvValues -Name "ALERTMANAGER_PORT" -Default "9093"

if (Test-CommandAvailable "docker") {
    Write-Pass "Docker CLI is installed"

    if ((Invoke-NativeQuiet -Command "docker" -Arguments @("info")) -eq 0) {
        Write-Pass "Docker daemon is reachable"

        if ((Invoke-NativeQuiet -Command "docker" -Arguments @("compose", "version")) -eq 0) {
            Write-Pass "Docker Compose plugin is available"
        }
        else {
            Write-FailCheck "Docker Compose plugin is not available"
        }

        if ((Invoke-NativeQuiet -Command "docker" -Arguments @("compose", "config", "--quiet")) -eq 0) {
            Write-Pass "Docker Compose configuration is valid"
        }
        else {
            Write-FailCheck "Docker Compose configuration is invalid"
        }

        $containerCheck = Invoke-NativeCapture -Command "docker" -Arguments @("ps", "--format", "{{.Names}}")
        $infraContainers = @($containerCheck.Output | Where-Object { $_ -like "infrawatch-*" })
        if ($infraContainers.Count -gt 0) {
            Write-Pass "InfraWatch Docker containers are running: $($infraContainers -join ', ')"
        }
        else {
            Write-WarnCheck "No running InfraWatch Docker containers were found"
        }
    }
    else {
        Write-FailCheck "Docker daemon is not reachable. Start Docker Desktop."
    }
}
else {
    Write-FailCheck "Docker CLI is not installed"
}

if (Test-CommandAvailable "kubectl") {
    Write-Pass "kubectl is installed"

    if ((Invoke-NativeQuiet -Command "kubectl" -Arguments @("version", "--client")) -eq 0) {
        Write-Pass "kubectl client works"
    }
    else {
        Write-FailCheck "kubectl client check failed"
    }

    if ((Invoke-NativeQuiet -Command "kubectl" -Arguments @("kustomize", "k8s")) -eq 0) {
        Write-Pass "Kubernetes manifests render successfully"
    }
    else {
        Write-FailCheck "Kubernetes manifests do not render"
    }

    $context = Invoke-NativeCapture -Command "kubectl" -Arguments @("config", "current-context")
    if ($context.ExitCode -eq 0 -and $context.Output) {
        Write-Pass "Current kubectl context: $($context.Output)"
    }
    else {
        Write-WarnCheck "kubectl has no current context"
    }

    if ((Invoke-NativeQuiet -Command "kubectl" -Arguments @("cluster-info")) -eq 0) {
        Write-Pass "Kubernetes cluster is reachable"

        if ((Invoke-NativeQuiet -Command "kubectl" -Arguments @("get", "namespace", $Namespace)) -eq 0) {
            Write-Pass "Namespace '$Namespace' exists"

            $pods = Invoke-NativeCapture -Command "kubectl" -Arguments @("get", "pods", "--namespace", $Namespace, "--no-headers")
            if ($pods.ExitCode -eq 0 -and $pods.Output) {
                $problemPods = @($pods.Output | Where-Object { $_ -match "CrashLoopBackOff|ImagePullBackOff|ErrImagePull|Error|Pending" })
                if ($problemPods.Count -gt 0) {
                    Write-WarnCheck "Some InfraWatch pods need attention"
                    $problemPods | ForEach-Object { Write-Host "       $_" -ForegroundColor Yellow }
                }
                else {
                    Write-Pass "InfraWatch pods have no obvious error states"
                }
            }
            else {
                Write-WarnCheck "No pods found in namespace '$Namespace'"
            }

            if ((Invoke-NativeQuiet -Command "kubectl" -Arguments @("get", "svc", "infrawatch-frontend", "--namespace", $Namespace)) -eq 0) {
                Write-Pass "Kubernetes frontend service exists"
            }
            else {
                Write-WarnCheck "Kubernetes frontend service was not found"
            }

            $observabilityServices = @(
                @{ Name = "Grafana"; Resource = "svc/infrawatch-grafana" },
                @{ Name = "Prometheus"; Resource = "svc/infrawatch-prometheus" },
                @{ Name = "Alertmanager"; Resource = "svc/infrawatch-alertmanager" },
                @{ Name = "Loki"; Resource = "svc/infrawatch-loki-gateway" }
            )

            foreach ($service in $observabilityServices) {
                if ((Invoke-NativeQuiet -Command "kubectl" -Arguments @("get", $service.Resource, "--namespace", $Namespace)) -eq 0) {
                    Write-Pass "$($service.Name) Kubernetes service exists"
                }
                else {
                    Write-WarnCheck "$($service.Name) Kubernetes service was not found. Run .\scripts\start-observability.ps1."
                }
            }

            if ((Invoke-NativeQuiet -Command "kubectl" -Arguments @("get", "deployment", "infrawatch-alloy", "--namespace", $Namespace)) -eq 0) {
                Write-Pass "Grafana Alloy deployment exists"
            }
            else {
                Write-WarnCheck "Grafana Alloy deployment was not found. Run .\scripts\start-observability.ps1."
            }
        }
        else {
            Write-WarnCheck "Namespace '$Namespace' does not exist yet. Run .\scripts\start-k8s.ps1."
        }
    }
    else {
        Write-WarnCheck "Kubernetes cluster is not reachable from the current context"
    }
}
else {
    Write-FailCheck "kubectl is not installed"
}

$HelmCommand = Resolve-HelmCommand
if (-not [string]::IsNullOrWhiteSpace($HelmCommand)) {
    Write-Pass "Helm is installed"

    if ((Invoke-NativeQuiet -Command $HelmCommand -Arguments @("status", "infrawatch", "--namespace", $Namespace)) -eq 0) {
        Write-WarnCheck "Legacy kube-prometheus-stack Helm release exists. The current local profile uses lightweight manifests instead."
    }
    else {
        Write-Pass "Legacy kube-prometheus-stack Helm release is absent"
    }

    if ((Invoke-NativeQuiet -Command $HelmCommand -Arguments @("status", "infrawatch-loki", "--namespace", $Namespace)) -eq 0) {
        Write-Pass "Loki Helm release exists"
    }
    else {
        Write-WarnCheck "Loki Helm release was not found"
    }

    if ((Invoke-NativeQuiet -Command $HelmCommand -Arguments @("status", "infrawatch-alloy", "--namespace", $Namespace)) -eq 0) {
        Write-Pass "Grafana Alloy Helm release exists"
    }
    else {
        Write-WarnCheck "Grafana Alloy Helm release was not found"
    }
}
else {
    Write-WarnCheck "Helm is not installed. Kubernetes observability setup requires Helm."
}

if (Test-CommandAvailable "minikube") {
    Write-Pass "Minikube is installed"
    $minikubeStatus = Invoke-NativeCapture -Command "minikube" -Arguments @("status", "--format={{.Host}}")
    if ($minikubeStatus.ExitCode -eq 0 -and $minikubeStatus.Output -eq "Running") {
        Write-Pass "Minikube host is running"
    }
    else {
        Write-WarnCheck "Minikube is not running"
    }
}
else {
    Write-WarnCheck "Minikube is not installed. Docker Compose mode can still be used."
}

Test-HttpEndpoint -Name "InfraWatch dashboard" -Url "http://localhost:$FrontendPort"
Test-HttpEndpoint -Name "FastAPI docs" -Url "http://localhost:$BackendPort/docs"
Test-HttpEndpoint -Name "Prometheus" -Url "http://localhost:$PrometheusPort/-/ready"
Test-HttpEndpoint -Name "Grafana" -Url "http://localhost:$GrafanaPort/api/health"
Test-HttpEndpoint -Name "Loki" -Url "http://localhost:$LokiPort/loki/api/v1/status/buildinfo"
Test-HttpEndpoint -Name "Alertmanager" -Url "http://localhost:$AlertmanagerPort/-/ready"

Write-Host ""
if ($script:Failures -gt 0) {
    Write-Host "Doctor finished with $script:Failures failure(s) and $script:Warnings warning(s)." -ForegroundColor Red
    exit 1
}

if ($script:Warnings -gt 0) {
    Write-Host "Doctor finished with 0 failures and $script:Warnings warning(s)." -ForegroundColor Yellow
    exit 0
}

Write-Host "Doctor finished cleanly. InfraWatch looks ready." -ForegroundColor Green
