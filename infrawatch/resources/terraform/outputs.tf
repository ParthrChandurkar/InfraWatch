# Useful values from the Terraform-managed local Kubernetes foundation.

output "namespace_name" {
  description = "Namespace managed by Terraform for InfraWatch."
  value       = kubernetes_namespace_v1.infrawatch.metadata[0].name
}

output "service_account_name" {
  description = "ServiceAccount used by the InfraWatch backend."
  value       = kubernetes_service_account_v1.backend.metadata[0].name
}

output "cluster_role_name" {
  description = "Read-only ClusterRole for local cluster visibility."
  value       = kubernetes_cluster_role_v1.local_reader.metadata[0].name
}

output "cluster_role_binding_name" {
  description = "ClusterRoleBinding for the local reader permissions."
  value       = kubernetes_cluster_role_binding_v1.local_reader.metadata[0].name
}

output "role_name" {
  description = "Namespace-scoped Role used for InfraWatch workload management."
  value       = kubernetes_role_v1.deployer.metadata[0].name
}

output "role_binding_name" {
  description = "Namespace-scoped RoleBinding used for InfraWatch workload management."
  value       = kubernetes_role_binding_v1.deployer.metadata[0].name
}

output "resource_quota_name" {
  description = "ResourceQuota limiting local InfraWatch namespace usage."
  value       = kubernetes_resource_quota_v1.infrawatch.metadata[0].name
}

output "backend_configmap_name" {
  description = "ConfigMap containing non-secret backend configuration."
  value       = kubernetes_config_map_v1.backend_config.metadata[0].name
}

output "verify_commands" {
  description = "Read-only commands to verify Terraform-owned foundation resources."
  value = [
    "kubectl get namespace ${var.namespace}",
    "kubectl get serviceaccount ${var.service_account_name} --namespace ${var.namespace}",
    "kubectl get clusterrole ${var.cluster_role_name}",
    "kubectl get clusterrolebinding ${var.cluster_role_binding_name}",
    "kubectl get role ${var.role_name} --namespace ${var.namespace}",
    "kubectl get rolebinding ${var.role_binding_name} --namespace ${var.namespace}",
    "kubectl get resourcequota ${var.resource_quota_name} --namespace ${var.namespace}",
    "kubectl get configmap ${var.backend_configmap_name} --namespace ${var.namespace}",
  ]
}
