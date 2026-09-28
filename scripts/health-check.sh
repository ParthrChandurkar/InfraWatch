#!/usr/bin/env bash
# Read-only health checks for the local InfraWatch Kubernetes environment.

set -euo pipefail

NAMESPACE="${IW_NAMESPACE:-infrawatch}"
TIMEOUT_SECONDS="${IW_HEALTH_TIMEOUT_SECONDS:-5}"

PASS="✓"
FAIL="✗"
WARN="⚠"

FAILURES=0
WARNINGS=0

pass() {
  printf '%s %s\n' "${PASS}" "$*"
}

warn() {
  WARNINGS=$((WARNINGS + 1))
  printf '%s %s\n' "${WARN}" "$*"
}

fail() {
  FAILURES=$((FAILURES + 1))
  printf '%s %s\n' "${FAIL}" "$*"
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

kubectl_quiet() {
  kubectl "$@" >/dev/null 2>&1
}

check_deployment() {
  local name="$1"
  if ! kubectl_quiet get deployment "${name}" --namespace "${NAMESPACE}"; then
    fail "Deployment ${name} missing"
    return
  fi

  local desired ready available
  desired="$(kubectl get deployment "${name}" --namespace "${NAMESPACE}" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 0)"
  ready="$(kubectl get deployment "${name}" --namespace "${NAMESPACE}" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)"
  available="$(kubectl get deployment "${name}" --namespace "${NAMESPACE}" -o jsonpath='{.status.availableReplicas}' 2>/dev/null || echo 0)"
  ready="${ready:-0}"
  available="${available:-0}"

  if [[ "${ready}" == "${desired}" && "${available}" == "${desired}" ]]; then
    pass "Deployment ${name} ready (${ready}/${desired})"
  else
    fail "Deployment ${name} not ready (ready=${ready}/${desired}, available=${available}/${desired})"
  fi
}

check_statefulset() {
  local name="$1"
  if ! kubectl_quiet get statefulset "${name}" --namespace "${NAMESPACE}"; then
    fail "StatefulSet ${name} missing"
    return
  fi

  local desired ready
  desired="$(kubectl get statefulset "${name}" --namespace "${NAMESPACE}" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 0)"
  ready="$(kubectl get statefulset "${name}" --namespace "${NAMESPACE}" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)"
  ready="${ready:-0}"

  if [[ "${ready}" == "${desired}" ]]; then
    pass "StatefulSet ${name} ready (${ready}/${desired})"
  else
    fail "StatefulSet ${name} not ready (${ready}/${desired})"
  fi
}

check_service() {
  local name="$1"
  if kubectl_quiet get service "${name}" --namespace "${NAMESPACE}"; then
    pass "Service ${name} exists"
  else
    fail "Service ${name} missing"
  fi
}

check_optional_service() {
  local name="$1"
  local label="$2"
  if kubectl_quiet get service "${name}" --namespace "${NAMESPACE}"; then
    pass "${label} service exists"
  else
    warn "${label} service not found; run scripts/start-observability.ps1 or the documented observability setup if needed"
  fi
}

print_problem_pods() {
  local problems
  problems="$(kubectl get pods --namespace "${NAMESPACE}" --no-headers 2>/dev/null | grep -E 'CrashLoopBackOff|ImagePullBackOff|ErrImagePull|Error|Pending|OOMKilled' || true)"
  if [[ -n "${problems}" ]]; then
    fail "Some pods need attention:"
    printf '%s\n' "${problems}"
  else
    pass "No obvious pod error states"
  fi
}

echo "InfraWatch Health Check"
echo "-----------------------"

if ! command_exists kubectl; then
  fail "kubectl missing"
  echo "Health check finished with ${FAILURES} failure(s)."
  exit 1
fi

if kubectl cluster-info --request-timeout="${TIMEOUT_SECONDS}s" >/dev/null 2>&1; then
  pass "Kubernetes cluster reachable"
else
  fail "Kubernetes cluster not reachable"
  echo "Health check finished with ${FAILURES} failure(s)."
  exit 1
fi

CONTEXT="$(kubectl config current-context 2>/dev/null || true)"
if [[ -n "${CONTEXT}" ]]; then
  pass "Context: ${CONTEXT}"
else
  warn "kubectl current context is empty"
fi

if kubectl_quiet get namespace "${NAMESPACE}"; then
  pass "InfraWatch namespace exists: ${NAMESPACE}"
else
  fail "InfraWatch namespace missing: ${NAMESPACE}"
  echo "Health check finished with ${FAILURES} failure(s)."
  exit 1
fi

if kubectl_quiet get serviceaccount infrawatch-backend --namespace "${NAMESPACE}"; then
  pass "Terraform foundation ServiceAccount exists"
else
  fail "ServiceAccount infrawatch-backend missing; run Terraform foundation first"
fi

if kubectl_quiet get configmap infrawatch-backend-config --namespace "${NAMESPACE}"; then
  pass "Terraform foundation ConfigMap exists"
else
  fail "ConfigMap infrawatch-backend-config missing; run Terraform foundation first"
fi

if kubectl_quiet get resourcequota infrawatch-quota --namespace "${NAMESPACE}"; then
  pass "Terraform foundation ResourceQuota exists"
else
  fail "ResourceQuota infrawatch-quota missing; run Terraform foundation first"
fi

check_statefulset infrawatch-postgres
check_deployment infrawatch-backend
check_deployment infrawatch-frontend

check_service infrawatch-postgres
check_service infrawatch-backend
check_service infrawatch-frontend

print_problem_pods

check_optional_service infrawatch-prometheus "Prometheus"
check_optional_service infrawatch-grafana "Grafana"
check_optional_service infrawatch-alertmanager "Alertmanager"
check_optional_service infrawatch-loki-gateway "Loki"

echo ""
if [[ "${FAILURES}" -gt 0 ]]; then
  echo "Health check finished with ${FAILURES} failure(s) and ${WARNINGS} warning(s)."
  exit 1
fi

if [[ "${WARNINGS}" -gt 0 ]]; then
  echo "Health check finished with 0 failures and ${WARNINGS} warning(s)."
  exit 0
fi

echo "Health check passed."
