# Terraform foundation for InfraWatch on a local kubeconfig-backed cluster.
# This intentionally uses only the Kubernetes provider. No cloud providers are used.
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.34"
    }
  }
}

provider "kubernetes" {
  config_path    = var.kubeconfig_path
  config_context = var.kube_context
}

locals {
  common_labels = {
    "app.kubernetes.io/name"       = "infrawatch"
    "app.kubernetes.io/part-of"    = "infrawatch"
    "app.kubernetes.io/managed-by" = "Terraform"
    "infrawatch.io/component"      = "foundation"
  }
}

resource "kubernetes_namespace_v1" "infrawatch" {
  metadata {
    name = var.namespace
    labels = merge(local.common_labels, {
      "infrawatch.io/ownership" = "terraform-foundation"
    })
    annotations = {
      "infrawatch.io/purpose" = "Local Kubernetes boundary for InfraWatch application and observability resources."
    }
  }
}

resource "kubernetes_service_account_v1" "backend" {
  metadata {
    name      = var.service_account_name
    namespace = kubernetes_namespace_v1.infrawatch.metadata[0].name
    labels    = local.common_labels
    annotations = {
      "infrawatch.io/purpose" = "Service account used by the InfraWatch backend inside local Kubernetes."
    }
  }
}

resource "kubernetes_cluster_role_v1" "local_reader" {
  metadata {
    name   = var.cluster_role_name
    labels = local.common_labels
    annotations = {
      "infrawatch.io/purpose" = "Read-only local cluster visibility for InfraWatch status, events, pods, nodes, and logs."
    }
  }

  rule {
    api_groups = [""]
    resources  = ["pods", "nodes", "events"]
    verbs      = ["get", "list", "watch"]
  }

  rule {
    api_groups = [""]
    resources  = ["pods/log"]
    verbs      = ["get"]
  }

  rule {
    api_groups = ["events.k8s.io"]
    resources  = ["events"]
    verbs      = ["get", "list", "watch"]
  }

  rule {
    api_groups = ["apps"]
    resources  = ["deployments", "replicasets"]
    verbs      = ["get", "list", "watch"]
  }

  rule {
    api_groups = ["autoscaling"]
    resources  = ["horizontalpodautoscalers"]
    verbs      = ["get", "list", "watch"]
  }
}

resource "kubernetes_cluster_role_binding_v1" "local_reader" {
  metadata {
    name   = var.cluster_role_binding_name
    labels = local.common_labels
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role_v1.local_reader.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.backend.metadata[0].name
    namespace = kubernetes_namespace_v1.infrawatch.metadata[0].name
  }
}

resource "kubernetes_role_v1" "deployer" {
  metadata {
    name      = var.role_name
    namespace = kubernetes_namespace_v1.infrawatch.metadata[0].name
    labels    = local.common_labels
    annotations = {
      "infrawatch.io/purpose" = "Namespace-scoped workload management for InfraWatch-created lightweight app deployments."
    }
  }

  rule {
    api_groups = [""]
    resources  = ["services", "pods"]
    verbs      = ["get", "list", "watch", "create", "update", "patch", "delete"]
  }

  rule {
    api_groups = ["apps"]
    resources  = ["deployments"]
    verbs      = ["get", "list", "watch", "create", "update", "patch", "delete"]
  }

  rule {
    api_groups = ["autoscaling"]
    resources  = ["horizontalpodautoscalers"]
    verbs      = ["get", "list", "watch", "create", "update", "patch", "delete"]
  }
}

resource "kubernetes_role_binding_v1" "deployer" {
  metadata {
    name      = var.role_binding_name
    namespace = kubernetes_namespace_v1.infrawatch.metadata[0].name
    labels    = local.common_labels
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role_v1.deployer.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.backend.metadata[0].name
    namespace = kubernetes_namespace_v1.infrawatch.metadata[0].name
  }
}

resource "kubernetes_resource_quota_v1" "infrawatch" {
  metadata {
    name      = var.resource_quota_name
    namespace = kubernetes_namespace_v1.infrawatch.metadata[0].name
    labels    = local.common_labels
    annotations = {
      "infrawatch.io/purpose" = "Keeps the local InfraWatch namespace bounded on developer machines."
    }
  }

  spec {
    hard = {
      "requests.cpu"    = var.resource_quota_requests_cpu
      "requests.memory" = var.resource_quota_requests_memory
      "limits.cpu"      = var.resource_quota_limits_cpu
      "limits.memory"   = var.resource_quota_limits_memory
      pods              = var.resource_quota_pods
      services          = var.resource_quota_services
    }
  }
}

resource "kubernetes_config_map_v1" "backend_config" {
  metadata {
    name      = var.backend_configmap_name
    namespace = kubernetes_namespace_v1.infrawatch.metadata[0].name
    labels    = local.common_labels
    annotations = {
      "infrawatch.io/purpose" = "Non-secret runtime configuration for the InfraWatch backend."
    }
  }

  data = {
    INFRAWATCH_ENVIRONMENT                   = var.infrawatch_environment
    INFRAWATCH_EXECUTE_KUBECTL               = tostring(var.execute_kubectl)
    INFRAWATCH_KUBECTL_NAMESPACE             = kubernetes_namespace_v1.infrawatch.metadata[0].name
    INFRAWATCH_ALLOW_MOCK_OBSERVABILITY      = tostring(var.allow_mock_observability)
    INFRAWATCH_PROMETHEUS_URL                = var.prometheus_url
    INFRAWATCH_LOKI_URL                      = var.loki_url
    INFRAWATCH_ALERTMANAGER_URL              = var.alertmanager_url
    REDIS_URL                                = var.redis_url
    REDIS_CACHE_TTL                          = tostring(var.redis_cache_ttl)
    INFRAWATCH_SERVICE_NAME                  = "infrawatch-backend"
    INFRAWATCH_STATE_FILE                    = "/data/deployments.json"
    INFRAWATCH_AUDIT_FILE                    = "/data/audit-log.json"
    INFRAWATCH_ROLLOUT_TIMEOUT_SECONDS       = tostring(var.rollout_timeout_seconds)
    INFRAWATCH_OBSERVABILITY_TIMEOUT_SECONDS = tostring(var.observability_timeout_seconds)
  }
}
