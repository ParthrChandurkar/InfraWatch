param(
    [switch]$RemoveData,
    [switch]$StopMinikube,
    [switch]$KeepObservability,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$env:PATH = [Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [Environment]::GetEnvironmentVariable("PATH", "User")

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

function Scale-IfExists {
    param(
        [string]$Kind,
        [string]$Name,
        [int]$Replicas
    )

    $exists = Invoke-NativeQuiet -Command "kubectl" -Arguments @("get", "$Kind/$Name", "--namespace", $Namespace)
    if ($exists -eq 0) {
        & kubectl scale "$Kind/$Name" --namespace $Namespace "--replicas=$Replicas"
        Assert-LastCommand "Could not scale $Kind/$Name."
    }
    else {
        Write-Warn "$Kind/$Name was not found in namespace '$Namespace'."
    }
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Namespace = "infrawatch"

Set-Location -LiteralPath $RepoRoot

Write-Host ""
Write-Host "Stopping InfraWatch local Kubernetes stack" -ForegroundColor Green
Write-Host ""

if (-not (Test-CommandAvailable "kubectl")) {
    Write-Fail "kubectl was not found. Install kubectl before using this script."
    exit 1
}

if ($StopMinikube -and -not (Test-CommandAvailable "minikube")) {
    Write-Fail "minikube was not found. Install Minikube or remove -StopMinikube."
    exit 1
}

Write-Info "Checking Kubernetes access"
$ClusterReachable = Invoke-NativeQuiet -Command "kubectl" -Arguments @("cluster-info")
if ($ClusterReachable -ne 0) {
    Write-Warn "Kubernetes is not reachable from the current kubectl context. No InfraWatch workloads were changed."

    if ($StopMinikube) {
        Write-Info "Attempting to stop Minikube anyway"
        & minikube stop
        exit $LASTEXITCODE
    }

    exit 0
}

$NamespaceExists = Invoke-NativeQuiet -Command "kubectl" -Arguments @("get", "namespace", $Namespace)
if ($NamespaceExists -ne 0) {
    Write-Warn "Namespace '$Namespace' does not exist. Nothing to stop."

    if ($StopMinikube) {
        Write-Info "Stopping Minikube"
        & minikube stop
        exit $LASTEXITCODE
    }

    exit 0
}

if ($CheckOnly) {
    Write-Info "Check-only mode completed. Namespace '$Namespace' exists and can be managed."
    Write-Host ""
    Write-Host "To pause workloads:"
    Write-Host "  .\scripts\stop-k8s.ps1"
    Write-Host ""
    Write-Host "To remove all InfraWatch Kubernetes data:"
    Write-Host "  .\scripts\stop-k8s.ps1 -RemoveData"
    Write-Host ""
    Write-Host "To keep observability running while pausing the app:"
    Write-Host "  .\scripts\stop-k8s.ps1 -KeepObservability"
    exit 0
}

if ($RemoveData) {
    if (-not (Test-CommandAvailable "terraform")) {
        Write-Fail "terraform was not found. Terraform owns the InfraWatch namespace foundation; install Terraform or remove -RemoveData."
        exit 1
    }

    $HostPathPrefix = "/tmp/hostpath-provisioner/$Namespace/"
    $VolumeNames = @()
    $HostPaths = @()

    $PvcVolumes = & kubectl get pvc --namespace $Namespace -o jsonpath="{range .items[*]}{.spec.volumeName}{'\n'}{end}" 2>$null
    if ($LASTEXITCODE -eq 0 -and $PvcVolumes) {
        $VolumeNames = @($PvcVolumes -split "`r?`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    }

    foreach ($VolumeName in $VolumeNames) {
        $HostPath = & kubectl get pv $VolumeName -o jsonpath="{.spec.hostPath.path}" 2>$null
        if ($LASTEXITCODE -eq 0 -and $HostPath -and $HostPath.StartsWith($HostPathPrefix)) {
            $HostPaths += $HostPath
        }
    }

    if ($KeepObservability) {
        Write-Warn "-KeepObservability is ignored with -RemoveData because namespace '$Namespace' will be deleted."
    }

    $ObservabilityExists = $false
    foreach ($Resource in @(
        "deployment/infrawatch-prometheus",
        "deployment/infrawatch-alertmanager",
        "deployment/infrawatch-grafana",
        "deployment/infrawatch-kube-state-metrics",
        "deployment/infrawatch-alloy",
        "deployment/infrawatch-loki-gateway",
        "statefulset/infrawatch-loki"
    )) {
        if ((Invoke-NativeQuiet -Command "kubectl" -Arguments @("get", $Resource, "--namespace", $Namespace)) -eq 0) {
            $ObservabilityExists = $true
            break
        }
    }

    if ($ObservabilityExists) {
        if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot "stop-observability.ps1"))) {
            Write-Fail "Observability resources exist, but scripts/stop-observability.ps1 was not found. Remove observability first, then rerun -RemoveData."
            exit 1
        }

        Write-Info "Uninstalling observability resources before Terraform namespace cleanup"
        $PowerShellExecutable = (Get-Process -Id $PID).Path
        & $PowerShellExecutable -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "stop-observability.ps1") -Namespace $Namespace -UninstallStack -RemoveLokiData -KeepStrictBackend
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Observability uninstall failed. Terraform namespace cleanup was not started, so Helm/Kustomize-owned workloads were not removed implicitly."
            exit $LASTEXITCODE
        }
    }
    else {
        Write-Info "No observability workloads found to uninstall"
    }

    Write-Info "Deleting Kustomize-owned application resources before Terraform namespace cleanup"
    & kubectl delete -k k8s --ignore-not-found=true
    Assert-LastCommand "Could not delete Kustomize-owned InfraWatch resources."

    & kubectl wait --for=delete pod --namespace $Namespace -l "app.kubernetes.io/part-of=infrawatch" --timeout=120s 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Some application pods may still be terminating; Terraform namespace cleanup will finish removing local InfraWatch data."
    }

    Write-Warn "Destroying Terraform-managed InfraWatch foundation after Kustomize/Helm-owned workloads were removed."
    & terraform -chdir=terraform destroy -auto-approve "-var=kube_context=minikube"
    Assert-LastCommand "Terraform destroy failed."

    & kubectl wait "--for=delete" "namespace/$Namespace" --timeout=120s 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Namespace deletion is still finishing in the background."
    }

    foreach ($VolumeName in $VolumeNames) {
        & kubectl delete pv $VolumeName --ignore-not-found=true
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "PersistentVolume '$VolumeName' could not be deleted automatically."
        }
    }

    if ($HostPaths.Count -gt 0 -and (Test-CommandAvailable "minikube")) {
        foreach ($HostPath in $HostPaths) {
            if (-not $HostPath.StartsWith($HostPathPrefix)) {
                Write-Warn "Skipping unexpected hostPath outside InfraWatch namespace: $HostPath"
                continue
            }

            Write-Info "Removing Minikube hostPath data: $HostPath"
            & minikube ssh -- "sudo rm -rf '$HostPath'"
            if ($LASTEXITCODE -ne 0) {
                Write-Warn "Could not remove Minikube hostPath data at $HostPath."
            }
        }
    }
}
else {
    if (-not $KeepObservability -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot "stop-observability.ps1"))) {
        Write-Info "Stopping observability workloads first"
        $PowerShellExecutable = (Get-Process -Id $PID).Path
        & $PowerShellExecutable -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "stop-observability.ps1") -Namespace $Namespace
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "Observability stop script reported a problem. Continuing with app workload stop."
        }
    }

    Write-Info "Deleting HPA so it does not scale paused workloads back up"
    & kubectl delete hpa infrawatch-backend --namespace $Namespace --ignore-not-found=true
    Assert-LastCommand "Could not delete the backend HPA."

    Write-Info "Scaling InfraWatch workloads to zero replicas"
    Scale-IfExists -Kind "deployment" -Name "infrawatch-backend" -Replicas 0
    Scale-IfExists -Kind "deployment" -Name "infrawatch-frontend" -Replicas 0
    Scale-IfExists -Kind "deployment" -Name "infrawatch-redis" -Replicas 0
    Scale-IfExists -Kind "statefulset" -Name "infrawatch-postgres" -Replicas 0

    Write-Host ""
    & kubectl get pods,svc --namespace $Namespace
}

if ($StopMinikube) {
    Write-Info "Stopping Minikube"
    & minikube stop
    Assert-LastCommand "Could not stop Minikube."
}

Write-Host ""
Write-Host "InfraWatch local Kubernetes stack stopped." -ForegroundColor Green

if (-not $RemoveData) {
    Write-Host "State was preserved. Start again with:"
    Write-Host "  .\scripts\start-k8s.ps1"
}
