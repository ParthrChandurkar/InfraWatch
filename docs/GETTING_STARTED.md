# Getting Started with InfraWatch

This guide is for new users who want to run InfraWatch locally and understand what it can do.

InfraWatch is a local-first Kubernetes deployment and observability platform. It is useful for testing lightweight containerized apps, learning SRE workflows, and viewing logs, metrics, health, and rollout status without using a paid cloud account.

---

## 1. Start with Docker Compose

Use Docker Compose first. It starts the InfraWatch dashboard, API, database, Redis cache, Prometheus, Grafana, Loki, Grafana Alloy, and Alertmanager.

Fastest path:

```powershell
.\scripts\start-local.ps1
```

Run it from the project root. The script checks Docker, creates `.env` if it is missing, validates the Compose file, starts the stack, and prints all local URLs.

To run the stack in the background:

```powershell
.\scripts\start-local.ps1 -Detached
```

To check prerequisites without starting containers:

```powershell
.\scripts\start-local.ps1 -CheckOnly
```

To stop it:

```powershell
.\scripts\stop-local.ps1
```

To stop it and remove local Docker volumes:

```powershell
.\scripts\stop-local.ps1 -Volumes
```

If something looks wrong, run:

```powershell
.\scripts\doctor.ps1
```

The doctor script checks Docker, Docker Compose, Kubernetes tooling, Minikube, manifests, running services, and local URLs without changing your machine.

Manual Docker Compose path:

```powershell
copy .env.example .env
docker compose up --build
```

Open:

| Tool | URL |
|---|---|
| InfraWatch dashboard | http://localhost:3000 |
| FastAPI docs | http://localhost:8000/docs |
| Grafana | http://localhost:3001 |
| Prometheus | http://localhost:9090 |
| Loki | http://localhost:3100 |
| Grafana Alloy | http://localhost:12345 |
| Alertmanager | http://localhost:9093 |

Grafana login uses the values from your local `.env` file.

---

## 2. Try the Python CLI foundation

InfraWatch includes an installable Python CLI foundation. From the project root:

```powershell
python -m pip install -e .
infrawatch --help
infrawatch --version
infrawatch doctor
```

To test the installable package without relying on the repository checkout:

```powershell
python -m build
python -m venv C:\Temp\infrawatch-wheel-test
C:\Temp\infrawatch-wheel-test\Scripts\python.exe -m pip install .\dist\infrawatch-0.3.0-py3-none-any.whl
Set-Location C:\Temp
infrawatch --version
infrawatch doctor
```

The locally built wheel contains the runtime Terraform and Kubernetes resources used by the CLI. This is the local package workflow; InfraWatch is not published to PyPI yet.

`infrawatch doctor` checks local readiness: Python, Docker, Docker Compose, kubectl, Minikube, Terraform, Kustomize rendering, Kubernetes context, and the InfraWatch namespace when a cluster is reachable.

Start the local Kubernetes stack:

```powershell
infrawatch start
```

This is the preferred local Kubernetes path. It uses Minikube, Terraform, Kustomize, kubectl, and the existing repository configuration. It is idempotent: running it again reconciles the same InfraWatch-owned resources instead of deleting or recreating the cluster.

If you also want the existing observability installer to run on Windows:

```powershell
infrawatch start --install-observability
```

Without that flag, `start` reconciles the core InfraWatch application stack and `status` reports observability components if they are already installed.

After InfraWatch is deployed to Kubernetes, check the running components:

```powershell
infrawatch status
```

`infrawatch status` is different from `doctor`: it checks the current runtime state of the InfraWatch namespace and components such as backend, frontend, PostgreSQL, Redis, Prometheus, Grafana, Loki, and Alertmanager.

The lifecycle command below is reserved but does not manage infrastructure yet:

```powershell
infrawatch stop
```

Continue using the manual stop scripts in this guide when you need to pause or remove the local stack.

---

## 3. Understand Demo Mode

By default, Docker Compose keeps Kubernetes execution disabled:

```text
INFRAWATCH_EXECUTE_KUBECTL=false
INFRAWATCH_ALLOW_MOCK_OBSERVABILITY=true
REDIS_URL=redis://redis:6379/0
REDIS_CACHE_TTL=5
```

That means:

- the dashboard is usable immediately;
- deployments are recorded safely;
- no real app workload is created;
- metrics and logs may use realistic sample data.

This is intentional for first-time users.

---

## 4. Try real local Kubernetes

After the Docker Compose stack works, use a local Kubernetes cluster for real local Kubernetes deployment.

Linux/macOS Bash path:

```bash
bash scripts/setup.sh
```

This checks prerequisites, applies the Terraform-managed local Kubernetes foundation, creates the local PostgreSQL secret when missing, applies the Kubernetes manifests, and waits for rollouts.

Read-only health check:

```bash
bash scripts/health-check.sh
```

Windows PowerShell Minikube path:

```powershell
.\scripts\start-k8s.ps1
```

This is the script-backed path used by the CLI workflow. It deploys InfraWatch into a local Minikube cluster and prints the dashboard URL. The script uses a 3072 MB Minikube default so it works on Docker Desktop installs with limited memory.

What is real in this mode:

- Terraform-managed namespace, RBAC, ResourceQuota, and backend ConfigMap;
- Kubernetes services, deployments, StatefulSet, HPA, and rollout checks;
- backend deployment actions when `INFRAWATCH_EXECUTE_KUBECTL=true`;
- PostgreSQL-backed state inside the cluster.
- Redis-backed short TTL cache for repeated Prometheus dashboard metric requests.

What still uses fallback data by default:

- metrics/log responses, until Prometheus, Loki, and Grafana Alloy are installed.

If fallback remains enabled, InfraWatch uses `source: mock` only when required Prometheus/Loki data is missing. A service with real Prometheus CPU, memory, and request-rate data but zero 5xx errors should report `source: prometheus` with a zero error-rate series.

Redis does not replace Prometheus. It caches only real Prometheus metric responses for a few seconds. If Redis is down, the backend bypasses Redis and queries Prometheus directly.

Install the observability stack:

```powershell
.\scripts\start-observability.ps1
```

This installs a lightweight local observability stack into Minikube: Prometheus, Grafana, Alertmanager, and kube-state-metrics as plain Kubernetes manifests, plus Loki and Grafana Alloy through Helm. It also switches the backend to strict Prometheus/Loki mode unless you pass:

```powershell
.\scripts\start-observability.ps1 -KeepFallback
```

After it finishes, open:

| Tool | URL |
|---|---|
| Grafana | http://localhost:3001 |
| Prometheus | http://localhost:9090 |
| Alertmanager | http://localhost:9093 |
| Loki | http://localhost:3100 |

To pause observability and stop the local port-forwards:

```powershell
.\scripts\stop-observability.ps1
```

To fully remove the observability stack:

```powershell
.\scripts\stop-observability.ps1 -UninstallStack
```

If you have already installed the observability stack separately and want strict Prometheus/Loki behavior:

```powershell
.\scripts\start-k8s.ps1 -StrictObservability
```

If you publish forked images under your own DockerHub account:

```powershell
.\scripts\start-k8s.ps1 -ImageRepository your-dockerhub-username
```

To pause the Kubernetes workloads but keep local data:

```powershell
.\scripts\stop-k8s.ps1
```

To delete the InfraWatch Kubernetes namespace, secrets, and local PVC data safely:

```powershell
.\scripts\stop-k8s.ps1 -RemoveData
```

This removes Helm/Kustomize-owned workloads first and then destroys the Terraform-owned foundation. Use this instead of running raw `terraform destroy` while workloads still exist in the namespace.

Manual path:

```powershell
minikube start
minikube addons enable metrics-server
cd terraform
terraform init
terraform fmt
terraform validate
terraform apply -var="kube_context=minikube"
cd ..
kubectl create secret generic infrawatch-secrets `
  --namespace infrawatch `
  --from-literal=POSTGRES_PASSWORD=use-a-strong-password `
  --from-literal=DATABASE_URL=postgresql://infrawatch:use-a-strong-password@infrawatch-postgres:5432/infrawatch
kubectl apply -k k8s
```

Then open InfraWatch from Kubernetes:

```powershell
minikube service infrawatch-frontend --namespace infrawatch
```

---

## 5. What to test first

Start with an already-built public image:

```text
Service name: nginx-demo
Image: nginx:1.27-alpine
Replicas: 2
Port: 80
```

In Demo Mode, this creates a simulated record.

In Kubernetes mode, InfraWatch applies a real Kubernetes Deployment and Service.

---

## 6. What InfraWatch is not yet

InfraWatch is not a full SaaS platform yet.

Current limitations:

- no user accounts;
- no multi-tenant authorization;
- no GitHub OAuth app onboarding;
- no automatic build of arbitrary user repositories from the UI;
- no production Ingress/TLS setup by default.

The current goal is simple: make local Kubernetes deployment and observability easier for developers, students, and SRE learners.
