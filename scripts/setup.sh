#!/usr/bin/env bash
# Linux-friendly InfraWatch setup for local Kubernetes.
#
# This script checks prerequisites, applies the Terraform-managed foundation,
# creates the local Kubernetes secret when missing, and applies the existing
# InfraWatch application manifests. It does not install software silently and
# never deletes or resets the user's Kubernetes cluster.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TERRAFORM_DIR="${IW_TERRAFORM_DIR:-${REPO_ROOT}/terraform}"
K8S_DIR="${IW_K8S_DIR:-${REPO_ROOT}/k8s}"
NAMESPACE="${IW_NAMESPACE:-infrawatch}"
KUBE_CONTEXT="${IW_KUBE_CONTEXT:-}"
POSTGRES_SECRET="${IW_POSTGRES_SECRET:-infrawatch-secrets}"
POSTGRES_USER="${IW_POSTGRES_USER:-infrawatch}"
POSTGRES_DB="${IW_POSTGRES_DB:-infrawatch}"
AUTO_START_MINIKUBE="${IW_AUTO_START_MINIKUBE:-false}"

CHECK_ONLY=false
FOUNDATION_ONLY=false
SKIP_TERRAFORM=false
SKIP_K8S=false
START_MINIKUBE=false

PASS="✓"
FAIL="✗"
WARN="⚠"

usage() {
  cat <<'EOF'
InfraWatch Linux setup

Usage:
  scripts/setup.sh [options]

Options:
  --check-only        Only check prerequisites; do not apply Terraform or Kubernetes manifests.
  --foundation-only   Apply only Terraform foundation and required secret; skip app manifests.
  --skip-terraform    Skip Terraform apply. Use only if foundation already exists.
  --skip-k8s          Skip kubectl apply -k k8s.
  --start-minikube    Start Minikube when the current cluster is not reachable.
  -h, --help          Show this help.

Environment:
  IW_NAMESPACE              Namespace to manage. Default: infrawatch
  IW_KUBE_CONTEXT           Optional kube context passed to Terraform.
  IW_AUTO_START_MINIKUBE    true/false; same as --start-minikube when true.
  IW_POSTGRES_PASSWORD      Optional password for the infrawatch-secrets Secret.
EOF
}

log() {
  printf '%s\n' "$*"
}

pass() {
  printf '%s %s\n' "${PASS}" "$*"
}

warn() {
  printf '%s %s\n' "${WARN}" "$*"
}

fail() {
  printf '%s %s\n' "${FAIL}" "$*"
}

die() {
  fail "$*"
  exit 1
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

version_line() {
  "$@" 2>/dev/null | head -n 1 || true
}

require_command() {
  local name="$1"
  local hint="$2"
  if command_exists "${name}"; then
    pass "${name} available: $(command -v "${name}")"
  else
    fail "${name} missing. ${hint}"
    MISSING_REQUIRED=$((MISSING_REQUIRED + 1))
  fi
}

optional_command() {
  local name="$1"
  local hint="$2"
  if command_exists "${name}"; then
    pass "${name} available: $(command -v "${name}")"
  else
    warn "${name} missing. ${hint}"
  fi
}

cluster_reachable() {
  kubectl cluster-info >/dev/null 2>&1
}

terraform_args() {
  if [[ -n "${KUBE_CONTEXT}" ]]; then
    printf '%s\n' "-var=kube_context=${KUBE_CONTEXT}"
  fi
}

terraform_import_if_exists() {
  local address="$1"
  local import_id="$2"
  shift 2
  local kubectl_args=("$@")

  if kubectl "${kubectl_args[@]}" >/dev/null 2>&1; then
    terraform -chdir="${TERRAFORM_DIR}" import "${TF_ARGS[@]}" "${address}" "${import_id}" >/dev/null 2>&1 || true
    pass "Terraform state checked for ${address}"
  fi
}

generate_password() {
  if command_exists openssl; then
    openssl rand -base64 24 | tr -d '\n'
  else
    python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(24), end="")
PY
  fi
}

ensure_postgres_secret() {
  if kubectl get secret "${POSTGRES_SECRET}" --namespace "${NAMESPACE}" >/dev/null 2>&1; then
    pass "Kubernetes secret ${POSTGRES_SECRET} already exists"
    return
  fi

  local password="${IW_POSTGRES_PASSWORD:-}"
  if [[ -z "${password}" ]]; then
    password="$(generate_password)"
    warn "Generated a local PostgreSQL password for ${POSTGRES_SECRET}; it is stored only in the Kubernetes Secret."
  fi

  local database_url
  database_url="postgresql://${POSTGRES_USER}:${password}@infrawatch-postgres:5432/${POSTGRES_DB}"

  kubectl create secret generic "${POSTGRES_SECRET}" \
    --namespace "${NAMESPACE}" \
    "--from-literal=POSTGRES_PASSWORD=${password}" \
    "--from-literal=DATABASE_URL=${database_url}" \
    --dry-run=client \
    -o yaml | kubectl apply -f -

  pass "Created Kubernetes secret ${POSTGRES_SECRET}"
}

for arg in "$@"; do
  case "${arg}" in
    --check-only)
      CHECK_ONLY=true
      ;;
    --foundation-only)
      FOUNDATION_ONLY=true
      ;;
    --skip-terraform)
      SKIP_TERRAFORM=true
      ;;
    --skip-k8s)
      SKIP_K8S=true
      ;;
    --start-minikube)
      START_MINIKUBE=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      die "Unknown option: ${arg}"
      ;;
  esac
done

if [[ "${AUTO_START_MINIKUBE}" == "true" ]]; then
  START_MINIKUBE=true
fi

log "InfraWatch Linux Setup"
log "----------------------"

MISSING_REQUIRED=0

if [[ -n "${BASH_VERSION:-}" ]]; then
  pass "bash available: ${BASH_VERSION}"
else
  fail "bash is required"
  MISSING_REQUIRED=$((MISSING_REQUIRED + 1))
fi

require_command python3 "Install Python 3 for helper commands and local development."
require_command kubectl "Install kubectl to communicate with the local Kubernetes cluster."
require_command terraform "Install Terraform 1.6+ to apply the InfraWatch local foundation."
optional_command docker "Install Docker if your local cluster runtime or Docker Compose stack needs it."
optional_command minikube "Optional; install it if Minikube is your local Kubernetes runtime."
optional_command kind "Optional; install it if kind is your local Kubernetes runtime."
optional_command k3d "Optional; install it if k3d is your local Kubernetes runtime."
optional_command helm "Required later for Loki/Alloy observability setup."

if [[ "${MISSING_REQUIRED}" -gt 0 ]]; then
  die "Install the missing required tools and run this script again."
fi

if docker info >/dev/null 2>&1; then
  pass "Docker daemon reachable"
else
  warn "Docker daemon not reachable. This is okay only if your Kubernetes cluster is already running another way."
fi

if cluster_reachable; then
  pass "Kubernetes cluster reachable"
else
  if [[ "${START_MINIKUBE}" == "true" ]]; then
    command_exists minikube || die "--start-minikube requested, but minikube is not installed."
    warn "Kubernetes is not reachable; starting Minikube because --start-minikube was requested."
    minikube start
  else
    die "Kubernetes cluster is not reachable. Start Minikube/kind/k3d first, or rerun with --start-minikube for Minikube."
  fi
fi

CURRENT_CONTEXT="$(kubectl config current-context 2>/dev/null || true)"
if [[ -n "${CURRENT_CONTEXT}" ]]; then
  pass "Current Kubernetes context: ${CURRENT_CONTEXT}"
else
  warn "kubectl has no current context"
fi

if [[ -n "${KUBE_CONTEXT}" ]]; then
  pass "Terraform will use kube context: ${KUBE_CONTEXT}"
else
  warn "Terraform will use the kubeconfig current context."
fi

if [[ "${CHECK_ONLY}" == "true" ]]; then
  pass "Check-only mode completed; no changes were applied."
  exit 0
fi

cd "${REPO_ROOT}"

if [[ "${SKIP_TERRAFORM}" == "false" ]]; then
  log ""
  log "Applying Terraform foundation"
  terraform -chdir="${TERRAFORM_DIR}" init
  terraform -chdir="${TERRAFORM_DIR}" fmt -check
  terraform -chdir="${TERRAFORM_DIR}" validate
  mapfile -t TF_ARGS < <(terraform_args)
  terraform_import_if_exists kubernetes_namespace_v1.infrawatch "${NAMESPACE}" get namespace "${NAMESPACE}"
  terraform_import_if_exists kubernetes_service_account_v1.backend "${NAMESPACE}/infrawatch-backend" get serviceaccount infrawatch-backend --namespace "${NAMESPACE}"
  terraform_import_if_exists kubernetes_cluster_role_v1.local_reader infrawatch-local-reader get clusterrole infrawatch-local-reader
  terraform_import_if_exists kubernetes_cluster_role_binding_v1.local_reader infrawatch-local-reader get clusterrolebinding infrawatch-local-reader
  terraform_import_if_exists kubernetes_role_v1.deployer "${NAMESPACE}/infrawatch-deployer" get role infrawatch-deployer --namespace "${NAMESPACE}"
  terraform_import_if_exists kubernetes_role_binding_v1.deployer "${NAMESPACE}/infrawatch-deployer" get rolebinding infrawatch-deployer --namespace "${NAMESPACE}"
  terraform_import_if_exists kubernetes_resource_quota_v1.infrawatch "${NAMESPACE}/infrawatch-quota" get resourcequota infrawatch-quota --namespace "${NAMESPACE}"
  terraform_import_if_exists kubernetes_config_map_v1.backend_config "${NAMESPACE}/infrawatch-backend-config" get configmap infrawatch-backend-config --namespace "${NAMESPACE}"
  terraform -chdir="${TERRAFORM_DIR}" apply -auto-approve "${TF_ARGS[@]}"
  pass "Terraform foundation applied"
else
  warn "Skipping Terraform foundation because --skip-terraform was provided"
fi

ensure_postgres_secret

if [[ "${FOUNDATION_ONLY}" == "true" || "${SKIP_K8S}" == "true" ]]; then
  pass "Foundation setup completed; app manifests were not applied."
  exit 0
fi

log ""
log "Applying InfraWatch Kubernetes manifests"
kubectl apply -k "${K8S_DIR}"
kubectl rollout status statefulset/infrawatch-postgres --namespace "${NAMESPACE}" --timeout=240s
kubectl rollout status deployment/infrawatch-redis --namespace "${NAMESPACE}" --timeout=180s
kubectl rollout status deployment/infrawatch-backend --namespace "${NAMESPACE}" --timeout=240s
kubectl rollout status deployment/infrawatch-frontend --namespace "${NAMESPACE}" --timeout=240s

pass "InfraWatch local Kubernetes setup completed"
log ""
log "Useful commands:"
log "  kubectl get pods,svc --namespace ${NAMESPACE}"
log "  kubectl port-forward svc/infrawatch-frontend --namespace ${NAMESPACE} 3000:8080"
log "  bash scripts/health-check.sh"
