param(
    [switch]$RemoveData,
    [switch]$StopMinikube,
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
    exit 0
}

if ($RemoveData) {
    Write-Warn "Removing namespace '$Namespace'. This deletes InfraWatch Kubernetes resources, secrets, and local PVC data in that namespace."
    & kubectl delete namespace $Namespace --ignore-not-found=true
    Assert-LastCommand "Could not delete namespace '$Namespace'."

    & kubectl wait "--for=delete" "namespace/$Namespace" --timeout=120s 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Namespace deletion is still finishing in the background."
    }
}
else {
    Write-Info "Deleting HPA so it does not scale paused workloads back up"
    & kubectl delete hpa infrawatch-backend --namespace $Namespace --ignore-not-found=true
    Assert-LastCommand "Could not delete the backend HPA."

    Write-Info "Scaling InfraWatch workloads to zero replicas"
    Scale-IfExists -Kind "deployment" -Name "infrawatch-backend" -Replicas 0
    Scale-IfExists -Kind "deployment" -Name "infrawatch-frontend" -Replicas 0
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
