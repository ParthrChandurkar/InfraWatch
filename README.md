# InfraWatch

InfraWatch is a local-first deployment and observability project for running a sample application stack with Docker Compose or Minikube. It combines a React dashboard, FastAPI backend, Kubernetes resources, Terraform-based namespace setup, Helm-managed monitoring, and Redis-backed caching.

## Features

- React dashboard for deployment and infrastructure views
- FastAPI endpoints for cluster, workload, and deployment operations
- Docker Compose development stack
- Minikube-oriented Kubernetes manifests and Kustomize configuration
- Terraform configuration for the Kubernetes foundation
- Helm scripts for Prometheus, Grafana, and Alertmanager
- PostgreSQL persistence and Redis caching
- Failure-demonstration manifests for common Kubernetes workload states
- GitHub Actions checks for code, tests, builds, images, and optional deployment

## Architecture

The browser calls the FastAPI backend. The backend reads and changes resources in the connected Kubernetes cluster and uses PostgreSQL and Redis for application data and caching. Prometheus collects metrics; Grafana visualizes them; Alertmanager handles configured alerts. Docker Compose provides a local application stack, while Minikube exercises the Kubernetes path.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for component boundaries and request flows.

## Tech Stack

| Area | Technologies |
| --- | --- |
| Frontend | React, TypeScript, Vite |
| Backend | Python, FastAPI |
| Data | PostgreSQL, Redis |
| Containers | Docker, Docker Compose |
| Orchestration | Kubernetes, Minikube, Kustomize, Helm |
| Infrastructure | Terraform |
| Observability | Prometheus, Grafana, Alertmanager |
| Automation | GitHub Actions |

## Local Docker Stack

Prerequisites: Docker with the Compose plugin.

```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose ps
```

Refer to [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md) for the current service addresses and validation flow. Stop the stack with `docker compose down`.

## Local Kubernetes Deployment

Prerequisites: Docker, Minikube, `kubectl`, Terraform, and Helm.

The repository includes helper scripts under `scripts/` as well as a manual path. The core manual flow is:

```powershell
minikube start
minikube addons enable metrics-server

Set-Location terraform
terraform init
terraform validate
terraform apply -var="kube_context=minikube"
Set-Location ..

kubectl create secret generic infrawatch-secrets --namespace infrawatch --from-literal=POSTGRES_PASSWORD=replace-me --from-literal=DATABASE_URL=postgresql://infrawatch:replace-me@infrawatch-postgres:5432/infrawatch
kubectl apply -k k8s
kubectl rollout status deployment/infrawatch-backend --namespace infrawatch --timeout=180s
kubectl rollout status deployment/infrawatch-frontend --namespace infrawatch --timeout=180s
minikube service infrawatch-frontend --namespace infrawatch
```

Use a strong local password and do not commit generated Secret manifests. Monitoring installation details are documented in the repository scripts and monitoring configuration.

## Verification

Backend:

```powershell
Set-Location backend
python -m ruff check app tests
python -m pytest
```

Frontend:

```powershell
Set-Location frontend
npm ci
npm run lint
npm run build
```

Infrastructure configuration:

```powershell
docker compose config --quiet
kubectl kustomize k8s
terraform -chdir=terraform validate
```

## CI/CD

`.github/workflows/ci-cd.yml` verifies backend and frontend code and builds container images. Publishing and cluster rollout require configured registry and Kubernetes credentials. A GitHub-hosted runner cannot directly reach a laptop-only Minikube cluster; the repository documents an optional self-hosted runner path for that case.

## Limitations

- InfraWatch is designed for local development and demonstration, not as a hosted multi-tenant control plane.
- User authentication, multi-tenant authorization, TLS ingress, and arbitrary Git repository onboarding are not implemented.
- The UI deploys an existing container image; it does not build arbitrary user repositories.
- Local Minikube availability and resource capacity depend on the host machine.

