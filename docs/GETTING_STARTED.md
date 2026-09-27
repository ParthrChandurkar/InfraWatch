# Getting Started with InfraWatch

This guide is for new users who want to run InfraWatch locally and understand what it can do.

InfraWatch is a local-first Kubernetes deployment and observability platform. It is useful for testing lightweight containerized apps, learning SRE workflows, and viewing logs, metrics, health, and rollout status without using a paid cloud account.

---

## 1. Start with Docker Compose

Use Docker Compose first. It starts the InfraWatch dashboard, API, database, Prometheus, Grafana, Loki, Grafana Alloy, and Alertmanager.

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

## 2. Understand Demo Mode

By default, Docker Compose keeps Kubernetes execution disabled:

```text
INFRAWATCH_EXECUTE_KUBECTL=false
INFRAWATCH_ALLOW_MOCK_OBSERVABILITY=true
```

That means:

- the dashboard is usable immediately;
- deployments are recorded safely;
- no real app workload is created;
- metrics and logs may use realistic sample data.

This is intentional for first-time users.

---

## 3. Try real local Kubernetes

After the Docker Compose stack works, use Minikube for real local Kubernetes deployment.

Fastest path:

```powershell
.\scripts\start-k8s.ps1
```

This deploys InfraWatch into a local Minikube cluster and prints the dashboard URL.

What is real in this mode:

- Kubernetes namespace, services, deployments, StatefulSet, HPA, and rollout checks;
- backend deployment actions when `INFRAWATCH_EXECUTE_KUBECTL=true`;
- PostgreSQL-backed state inside the cluster.

What still uses fallback data by default:

- metrics/log responses, until Prometheus, Loki, and Grafana Alloy are installed.

If you have already installed the observability stack and want strict Prometheus/Loki behavior:

```powershell
.\scripts\start-k8s.ps1 -StrictObservability
```

If you publish forked images under your own DockerHub account:

```powershell
.\scripts\start-k8s.ps1 -ImageRepository your-dockerhub-username
```

Manual path:

```powershell
minikube start
minikube addons enable metrics-server
kubectl apply -f k8s/namespace.yaml
kubectl apply -k k8s
```

Then open InfraWatch from Kubernetes:

```powershell
minikube service infrawatch-frontend --namespace infrawatch
```

---

## 4. What to test first

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

## 5. What InfraWatch is not yet

InfraWatch is not a full SaaS platform yet.

Current limitations:

- no user accounts;
- no multi-tenant authorization;
- no GitHub OAuth app onboarding;
- no automatic build of arbitrary user repositories from the UI;
- no production Ingress/TLS setup by default.

The current goal is simple: make local Kubernetes deployment and observability easier for developers, students, and SRE learners.
