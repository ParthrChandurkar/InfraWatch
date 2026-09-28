# InfraWatch Terraform Foundation

Terraform provisions the local Kubernetes foundation that InfraWatch needs before the application manifests are applied.

This layer is intentionally local-only:

- no AWS
- no GCP
- no Azure
- no OCI
- no cloud infrastructure

Terraform connects to an existing local Kubernetes cluster through your kubeconfig.

Supported local targets include Minikube, kind, k3d, and compatible local Kubernetes clusters.

## What Terraform creates

Terraform owns these foundational resources:

| Resource | Purpose |
|---|---|
| Namespace | Dedicated `infrawatch` boundary |
| ServiceAccount | Identity used by the InfraWatch backend pod |
| ClusterRole | Narrow read access for pods, nodes, events, pod logs, deployments, replicasets, and HPAs |
| ClusterRoleBinding | Binds the read-only ClusterRole to the InfraWatch ServiceAccount |
| Role | Namespace-scoped permissions to create/update/delete InfraWatch-managed app Deployments, Services, Pods, and HPAs |
| RoleBinding | Binds the namespace Role to the InfraWatch ServiceAccount |
| ResourceQuota | Keeps local laptop resource usage bounded |
| ConfigMap | Non-secret backend runtime configuration |

Terraform does not create application Deployments, Services, PostgreSQL, Prometheus, Grafana, Loki, Alloy, or Alertmanager workloads. Those remain owned by the existing Kubernetes manifests and scripts.

## Prerequisites

- Terraform 1.6+
- kubectl
- a running local Kubernetes cluster:
  - Minikube, or
  - kind, or
  - k3d, or
  - another compatible local cluster

Check your active context:

```powershell
kubectl config current-context
kubectl cluster-info
```

## Usage

From the repository root:

```powershell
cd terraform
terraform init
terraform fmt
terraform validate
terraform plan
terraform apply
```

The provided setup scripts run safe `terraform import` checks before `terraform apply`. This lets Terraform adopt older InfraWatch resources that may already exist from the previous Kustomize-only workflow.

If you want to pin a specific context:

```powershell
terraform plan -var="kube_context=minikube"
terraform apply -var="kube_context=minikube"
```

For kind or k3d, use the context name from:

```powershell
kubectl config get-contexts
```

Examples:

```powershell
terraform apply -var="kube_context=kind-infrawatch"
terraform apply -var="kube_context=k3d-infrawatch"
```

## Verify resources

After `terraform apply`, verify the Terraform-owned foundation:

```powershell
kubectl get namespace infrawatch
kubectl get serviceaccount infrawatch-backend --namespace infrawatch
kubectl get clusterrole infrawatch-local-reader
kubectl get clusterrolebinding infrawatch-local-reader
kubectl get role infrawatch-deployer --namespace infrawatch
kubectl get rolebinding infrawatch-deployer --namespace infrawatch
kubectl get resourcequota infrawatch-quota --namespace infrawatch
kubectl get configmap infrawatch-backend-config --namespace infrawatch
```

Then apply the app workloads:

```powershell
kubectl apply -k ../k8s
```

## Destroy

Terraform owns the `infrawatch` namespace. Kustomize owns application resources inside that namespace.

Because Kubernetes namespace deletion cascades to namespaced resources, do not run raw `terraform destroy` while InfraWatch application or observability workloads are still installed. That can delete Kustomize/Helm-owned workloads as a side effect of namespace deletion.

Use the safe full-cleanup workflow from the repository root:

```powershell
.\scripts\stop-k8s.ps1 -RemoveData
```

That script:

1. uninstalls Helm-managed observability components;
2. deletes Kustomize-owned application resources with `kubectl delete -k k8s`;
3. runs `terraform destroy` only after those workloads have been explicitly removed;
4. leaves the Minikube/kind/k3d cluster itself untouched unless you also pass `-StopMinikube`.

Raw Terraform destroy is only safe when no Kustomize/Helm-owned InfraWatch workloads remain in the namespace:

```powershell
terraform destroy
```

Terraform destroy does not delete Minikube, kind, k3d, Docker Desktop, or any local Kubernetes cluster.

## Ownership boundary

Terraform owns:

- namespace
- backend ServiceAccount
- foundational RBAC
- ResourceQuota
- backend runtime ConfigMap

Kubernetes manifests own:

- backend/frontend Deployments
- backend/frontend Services
- PostgreSQL StatefulSet and Service
- application HPA
- app-specific ConfigMaps such as PostgreSQL config and alert rules
- observability workloads already managed under `k8s/observability`

Do not add the Terraform-owned resources back into `k8s/kustomization.yaml`, or both tools will try to manage the same objects.
