# Input variables for local Kubernetes-only InfraWatch foundation resources.

variable "namespace" {
  description = "Kubernetes namespace for InfraWatch local resources."
  type        = string
  default     = "infrawatch"
}

variable "kubeconfig_path" {
  description = "Path to the kubeconfig file for the existing local cluster."
  type        = string
  default     = "~/.kube/config"
}

variable "kube_context" {
  description = "Optional kubeconfig context. Leave null to use the current context; set to minikube, kind-..., or k3d-... when needed."
  type        = string
  default     = null
}

variable "service_account_name" {
  description = "ServiceAccount used by the InfraWatch backend."
  type        = string
  default     = "infrawatch-backend"
}

variable "cluster_role_name" {
  description = "ClusterRole granting narrow read access for local cluster visibility."
  type        = string
  default     = "infrawatch-local-reader"
}

variable "cluster_role_binding_name" {
  description = "ClusterRoleBinding connecting the read-only ClusterRole to the InfraWatch backend ServiceAccount."
  type        = string
  default     = "infrawatch-local-reader"
}

variable "role_name" {
  description = "Namespace Role allowing InfraWatch to manage workloads only inside its namespace."
  type        = string
  default     = "infrawatch-deployer"
}

variable "role_binding_name" {
  description = "RoleBinding connecting the namespace deployer Role to the InfraWatch backend ServiceAccount."
  type        = string
  default     = "infrawatch-deployer"
}

variable "resource_quota_name" {
  description = "ResourceQuota name for the InfraWatch namespace."
  type        = string
  default     = "infrawatch-quota"
}

variable "backend_configmap_name" {
  description = "ConfigMap name for non-secret InfraWatch backend runtime settings."
  type        = string
  default     = "infrawatch-backend-config"
}

variable "resource_quota_requests_cpu" {
  description = "Total requested CPU allowed in the InfraWatch namespace."
  type        = string
  default     = "3"
}

variable "resource_quota_requests_memory" {
  description = "Total requested memory allowed in the InfraWatch namespace."
  type        = string
  default     = "4Gi"
}

variable "resource_quota_limits_cpu" {
  description = "Total CPU limits allowed in the InfraWatch namespace."
  type        = string
  default     = "6"
}

variable "resource_quota_limits_memory" {
  description = "Total memory limits allowed in the InfraWatch namespace."
  type        = string
  default     = "8Gi"
}

variable "resource_quota_pods" {
  description = "Maximum pods allowed in the InfraWatch namespace."
  type        = string
  default     = "40"
}

variable "resource_quota_services" {
  description = "Maximum services allowed in the InfraWatch namespace."
  type        = string
  default     = "20"
}

variable "infrawatch_environment" {
  description = "Runtime environment value passed to the backend."
  type        = string
  default     = "local-kubernetes"
}

variable "execute_kubectl" {
  description = "Whether the backend should execute kubectl for deployment actions."
  type        = bool
  default     = true
}

variable "allow_mock_observability" {
  description = "Whether the backend may use mock metrics/logs when Prometheus or Loki is unavailable."
  type        = bool
  default     = true
}

variable "prometheus_url" {
  description = "Prometheus URL reachable from inside the InfraWatch namespace."
  type        = string
  default     = "http://infrawatch-prometheus:9090"
}

variable "loki_url" {
  description = "Loki URL reachable from inside the InfraWatch namespace."
  type        = string
  default     = "http://infrawatch-loki-gateway"
}

variable "alertmanager_url" {
  description = "Alertmanager URL reachable from inside the InfraWatch namespace."
  type        = string
  default     = "http://infrawatch-alertmanager:9093"
}

variable "redis_url" {
  description = "Redis URL reachable from the backend for short-lived Prometheus response caching."
  type        = string
  default     = "redis://infrawatch-redis:6379/0"
}

variable "redis_cache_ttl" {
  description = "Short Redis cache TTL in seconds for dashboard Prometheus query responses."
  type        = number
  default     = 5

  validation {
    condition     = var.redis_cache_ttl >= 1 && var.redis_cache_ttl <= 60
    error_message = "redis_cache_ttl must be between 1 and 60 seconds."
  }
}

variable "rollout_timeout_seconds" {
  description = "Maximum time the backend waits for Kubernetes rollouts."
  type        = number
  default     = 180
}

variable "observability_timeout_seconds" {
  description = "Timeout for backend calls to observability systems."
  type        = number
  default     = 10
}
