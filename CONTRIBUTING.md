# Contributing to InfraWatch

Thanks for helping improve InfraWatch. This project is a local-first Kubernetes deployment and observability platform for lightweight application testing, SRE learning, and developer workflows.

InfraWatch is intentionally scoped to local Docker, Docker Compose, Minikube-compatible Kubernetes, Terraform, Kustomize, Prometheus, Redis, Loki, Grafana Alloy, Alertmanager, and a React/FastAPI dashboard. It is not a hosted SaaS control plane.

## Prerequisites

Common tools:

- Python 3.11 or newer;
- Docker with the Compose plugin;
- Node.js and npm for the frontend;
- kubectl;
- Minikube for the primary local Kubernetes path;
- Terraform 1.6 or newer;
- Helm for Loki and Grafana Alloy observability installation.

The project has been developed and validated mainly on local Docker Desktop/Minikube workflows. Do not promise support for other operating systems or Kubernetes distributions unless you validate them.

## Development setup

From the repository root:

```powershell
python -m pip install -e .
infrawatch --help
infrawatch doctor
```

For Docker Compose development:

```powershell
Copy-Item .env.example .env
docker compose up --build
```

For local Kubernetes development:

```powershell
infrawatch start
infrawatch status
infrawatch stop
```

## Validation commands

Backend:

```powershell
Set-Location backend
python -m ruff check app tests
python -m pytest
Set-Location ..
```

CLI package:

```powershell
python -m ruff check infrawatch tests
python -m pytest tests
```

Frontend:

```powershell
Set-Location frontend
npm ci
npm run lint
npm run build
Set-Location ..
```

Terraform:

```powershell
terraform -chdir=terraform fmt -check
terraform -chdir=terraform init -backend=false
terraform -chdir=terraform validate
```

Kustomize:

```powershell
kubectl kustomize k8s
```

Docker Compose:

```powershell
docker compose config --quiet
```

Python package build:

```powershell
python -m pip install build
python -m build
```

Whitespace check:

```powershell
git diff --check
```

## Pull requests

Before opening a pull request:

- explain the problem and the change clearly;
- include the exact tests and validation commands you ran;
- update docs when behavior, setup, commands, or troubleshooting changes;
- keep changes focused and avoid unrelated formatting churn;
- do not include generated build artifacts, caches, local logs, or temporary validation files.

## Safety rules

- Do not commit secrets, kubeconfig files, DockerHub tokens, Terraform state, local `.env` files, or sensitive logs.
- Do not delete Minikube profiles or PostgreSQL PVCs as routine troubleshooting.
- Do not destroy Terraform-managed resources without explicit justification.
- Preserve the Terraform/Kustomize ownership boundary.
- Keep Prometheus as the metrics source of truth.
- Keep Loki as the logs source of truth.
- Redis may cache Prometheus query responses, but it must not replace Prometheus.
- Demo/mock data must stay clearly labeled and must not be presented as live observability data.
