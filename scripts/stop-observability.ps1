param(
    [string]$Namespace = "infrawatch",
    [switch]$UninstallStack,
    [switch]$RemoveLokiData,
    [switch]$KeepStrictBackend,
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

function Stop-PortForwardProcess {
    param([int]$ProcessId)

    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $process) {
        return $false
    }

    $commandLine = $process.CommandLine
    if ($commandLine -notmatch "kubectl" -or $commandLine -notmatch "port-forward" -or $commandLine -notmatch $Namespace) {
        Write-Warn "Skipping PID $ProcessId because it is not an InfraWatch kubectl port-forward process."
        return $false
    }

    Stop-Process -Id $ProcessId -Force
    return $true
}

function Stop-KnownPortForwards {
    $stopped = 0

    if (Test-Path -LiteralPath $PortForwardStatePath) {
        $records = @(Get-Content -Raw -LiteralPath $PortForwardStatePath | ConvertFrom-Json)
        foreach ($record in $records) {
            if ($record.pid -and (Stop-PortForwardProcess -ProcessId ([int]$record.pid))) {
                Write-Host "  stopped $($record.name) port-forward pid $($record.pid)"
                $stopped += 1
            }
        }

        Remove-Item -LiteralPath $PortForwardStatePath -Force -ErrorAction SilentlyContinue
    }

    $resources = @(
        "svc/infrawatch-grafana",
        "svc/infrawatch-prometheus",
        "svc/infrawatch-alertmanager",
        "svc/infrawatch-loki-gateway"
    )

    $kubectlProcesses = Get-CimInstance Win32_Process -Filter "Name = 'kubectl.exe'" -ErrorAction SilentlyContinue
    foreach ($process in $kubectlProcesses) {
        $commandLine = $process.CommandLine
        if ($commandLine -notmatch "port-forward" -or $commandLine -notmatch $Namespace) {
            continue
        }

        if ($resources | Where-Object { $commandLine -match [regex]::Escape($_) }) {
            if (Stop-PortForwardProcess -ProcessId ([int]$process.ProcessId)) {
                Write-Host "  stopped kubectl port-forward pid $($process.ProcessId)"
                $stopped += 1
            }
        }
    }

    if ($stopped -eq 0) {
        Write-Warn "No tracked InfraWatch observability port-forwards were running."
    }
}

function Scale-IfExists {
    param(
        [string]$Kind,
        [string]$Name,
        [int]$Replicas
    )

    if ((Invoke-NativeQuiet -Command "kubectl" -Arguments @("get", "$Kind/$Name", "--namespace", $Namespace)) -eq 0) {
        & kubectl scale "$Kind/$Name" --namespace $Namespace "--replicas=$Replicas"
        Assert-LastCommand "Could not scale $Kind/$Name."
    }
}

function Wait-ForScaleDown {
    $selectors = @(
        "app.kubernetes.io/part-of=infrawatch-observability",
        "app.kubernetes.io/instance=infrawatch-alloy",
        "app.kubernetes.io/instance=infrawatch-loki"
    )

    foreach ($selector in $selectors) {
        $pods = & kubectl get pods --namespace $Namespace -l $selector --no-headers 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $pods) {
            continue
        }

        & kubectl wait --for=delete pod --namespace $Namespace -l $selector --timeout=120s 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "Some observability pods are still terminating for selector '$selector'."
        }
    }
}

function Set-BackendFallback {
    if ($KeepStrictBackend) {
        Write-Warn "Backend strict observability mode was preserved."
        return
    }

    if ((Invoke-NativeQuiet -Command "kubectl" -Arguments @("get", "configmap", "infrawatch-backend-config", "--namespace", $Namespace)) -ne 0) {
        return
    }

    Write-Info "Re-enabling backend mock observability fallback"
    $Patch = @{
        data = @{
            INFRAWATCH_ALLOW_MOCK_OBSERVABILITY = "true"
        }
    } | ConvertTo-Json -Compress

    $PatchFile = New-TemporaryFile
    try {
        Set-Content -LiteralPath $PatchFile -Value $Patch -Encoding UTF8
        & kubectl patch configmap infrawatch-backend-config --namespace $Namespace --type merge "--patch-file=$PatchFile"
        Assert-LastCommand "Could not patch backend observability fallback."
    }
    finally {
        Remove-Item -LiteralPath $PatchFile -Force -ErrorAction SilentlyContinue
    }

    if ((Invoke-NativeQuiet -Command "kubectl" -Arguments @("get", "deployment", "infrawatch-backend", "--namespace", $Namespace)) -eq 0) {
        & kubectl rollout restart deployment/infrawatch-backend --namespace $Namespace
        Assert-LastCommand "Could not restart the InfraWatch backend."

        & kubectl rollout status deployment/infrawatch-backend --namespace $Namespace --timeout=240s
        Assert-LastCommand "InfraWatch backend did not roll out after fallback update."
    }
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$StateDir = Join-Path $RepoRoot ".infrawatch"
$PortForwardStatePath = Join-Path $StateDir "observability-port-forwards.json"

Set-Location -LiteralPath $RepoRoot

Write-Host ""
Write-Host "Stopping InfraWatch observability stack" -ForegroundColor Green
Write-Host ""

if (-not (Test-CommandAvailable "kubectl")) {
    Write-Fail "kubectl was not found. Install kubectl before using this script."
    exit 1
}

$HelmCommand = Resolve-HelmCommand
if ($UninstallStack -and [string]::IsNullOrWhiteSpace($HelmCommand)) {
    Write-Fail "Helm was not found. Helm is required for -UninstallStack because Loki and Alloy are Helm releases."
    exit 1
}

if ($CheckOnly) {
    Write-Info "Check-only mode completed. No observability components were changed."
    Write-Host ""
    Write-Host "To pause observability and stop port-forwards:"
    Write-Host "  .\scripts\stop-observability.ps1"
    Write-Host ""
    Write-Host "To uninstall observability resources:"
    Write-Host "  .\scripts\stop-observability.ps1 -UninstallStack"
    exit 0
}

Write-Info "Stopping local observability port-forwards"
Stop-KnownPortForwards

if ((Invoke-NativeQuiet -Command "kubectl" -Arguments @("cluster-info")) -ne 0) {
    Write-Warn "Kubernetes is not reachable. Port-forwards were handled, but cluster resources were not changed."
    exit 0
}

if ((Invoke-NativeQuiet -Command "kubectl" -Arguments @("get", "namespace", $Namespace)) -ne 0) {
    Write-Warn "Namespace '$Namespace' does not exist. No cluster observability resources were changed."
    exit 0
}

if ($UninstallStack) {
    Write-Info "Uninstalling Helm-managed observability components"
    if ((Invoke-NativeQuiet -Command $HelmCommand -Arguments @("status", "infrawatch-alloy", "--namespace", $Namespace)) -eq 0) {
        & $HelmCommand uninstall infrawatch-alloy --namespace $Namespace --timeout 5m
        Assert-LastCommand "Could not uninstall Grafana Alloy."
    }

    if ((Invoke-NativeQuiet -Command $HelmCommand -Arguments @("status", "infrawatch-loki", "--namespace", $Namespace)) -eq 0) {
        & $HelmCommand uninstall infrawatch-loki --namespace $Namespace --timeout 5m
        Assert-LastCommand "Could not uninstall Loki."
    }

    Write-Info "Deleting lightweight observability manifests and generated ConfigMaps"
    & kubectl delete -k k8s/observability --ignore-not-found=true
    Assert-LastCommand "Could not delete lightweight observability manifests."

    & kubectl delete configmap `
        infrawatch-prometheus-config `
        infrawatch-alertmanager-config `
        infrawatch-grafana-datasources `
        infrawatch-grafana-dashboard-provider `
        infrawatch-grafana-dashboards `
        --namespace $Namespace `
        --ignore-not-found=true
    Assert-LastCommand "Could not delete observability ConfigMaps."

    & kubectl delete secret infrawatch-grafana-admin --namespace $Namespace --ignore-not-found=true
    Assert-LastCommand "Could not delete Grafana admin secret."

    if ($RemoveLokiData) {
        Write-Warn "Removing Loki local PVC data."
        $LokiPv = & kubectl get pvc storage-infrawatch-loki-0 --namespace $Namespace -o jsonpath="{.spec.volumeName}" 2>$null
        & kubectl delete pvc storage-infrawatch-loki-0 --namespace $Namespace --ignore-not-found=true
        Assert-LastCommand "Could not delete Loki PVC."
        if ($LokiPv) {
            & kubectl delete pv $LokiPv --ignore-not-found=true
            Assert-LastCommand "Could not delete Loki PV."
        }
    }
}
else {
    Write-Info "Scaling observability workloads to zero replicas"
    foreach ($deployment in @(
        "infrawatch-prometheus",
        "infrawatch-alertmanager",
        "infrawatch-grafana",
        "infrawatch-kube-state-metrics",
        "infrawatch-alloy",
        "infrawatch-loki-gateway"
    )) {
        Scale-IfExists -Kind "deployment" -Name $deployment -Replicas 0
    }

    Scale-IfExists -Kind "statefulset" -Name "infrawatch-loki" -Replicas 0
    Wait-ForScaleDown
}

Set-BackendFallback

Write-Host ""
Write-Host "InfraWatch observability stack stopped." -ForegroundColor Green

if ($UninstallStack) {
    Write-Host "Reinstall it with:"
    Write-Host "  .\scripts\start-observability.ps1"
}
else {
    Write-Host "Start it again with:"
    Write-Host "  .\scripts\start-observability.ps1"
}
