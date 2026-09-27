# 🚀 InfraWatch

**Local K8s deployment and observability platform for lightweight applications.**

InfraWatch helps developers test containerized apps on a local Kubernetes cluster and view deployment health, logs, metrics, and rollout activity from one simple dashboard.

Think of it as **CloudWatch-style visibility for local Kubernetes labs** — useful for learning, testing, demos, and validating small services before moving to real cloud infrastructure.

> InfraWatch is local-first. Browser Demo Mode is simulated and does not run a real Kubernetes cluster.

---

## ✨ What InfraWatch Does

- 🚀 Deploy or update an already-built container image.
- ☸️ Generate and apply Kubernetes `Deployment` and `Service` manifests.
- ✅ Check rollout status and ready/available replicas.
- 🔁 Roll back a managed Kubernetes deployment.
- 📊 Show CPU, memory, request-rate, and error-rate charts.
- 📜 Show recent service logs.
- 🧾 Keep an audit trail for deployment and delete actions.
- 🐘 Store state in PostgreSQL when configured.
- 🧪 Provide safe demo/mock data when no real cluster metrics exist.
- 🔁 Use GitHub Actions for optional Docker image publishing and deployment automation.

---

## 🎯 Project Direction

InfraWatch is being shaped as:

```text
Local app image → Local Kubernetes → InfraWatch dashboard → health, logs, metrics, rollback
```

The goal is simple:

> Help developers run and observe lightweight applications locally before paying for or depending on AWS, Azure, or GCP.

Good use cases:

- 🧑‍💻 Learning Kubernetes with a real dashboard.
- 🧪 Testing small services before cloud deployment.
- 🔍 Checking pod health, service status, logs, and metrics locally.
- 🎓 Showing SRE/DevOps concepts in a portfolio project.
- 💸 Practicing infrastructure workflows without cloud cost.

InfraWatch is currently best for lightweight apps that already have a container image or can be containerized with Docker.

New users should start with [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md).

---

## ✅ What Is Real vs Demo

| Feature | Docker Compose Local | Local Kubernetes / Minikube | Browser Demo Mode |
|---|---:|---:|---:|
| React dashboard | ✅ Real | ✅ Real | ✅ Real |
| FastAPI backend | ✅ Real | ✅ Real | ✅ Real |
| PostgreSQL state | ✅ Real | ✅ Real when configured | ❌ Uses sample/browser state |
| Kubernetes workload creation | 🧪 Off by default | ✅ Real when enabled | ❌ Simulated |
| Metrics and logs | 🧪 Mock fallback unless data exists | ✅ Prometheus/Loki when configured | 🧪 Simulated |
| Grafana dashboards | ✅ Local | ✅ Local/cluster setup | ❌ Not included |
| GitHub Actions CI/CD | ✅ Optional | ✅ Optional | ❌ Not automatic |

Demo Mode keeps the dashboard interactive without asking visitors to install Kubernetes. It is useful for screenshots, portfolio review, and quick UI exploration.

---

## 🧱 Architecture

```mermaid
flowchart LR
    User[Developer] --> UI[React Dashboard]
    UI --> API[FastAPI Control Plane]
    API --> Store[(PostgreSQL or JSON State)]
    API --> Kube[kubectl / Kubernetes API]
    Kube --> Apps[Local App Workloads]
    Apps --> Prom[Prometheus]
    Apps --> Loki[Loki]
    Prom --> API
    Loki --> API
    Prom --> Grafana[Grafana]
```

In Demo Mode, Kubernetes, Prometheus, and Loki calls are replaced by safe simulated responses.

---

## 🧰 Tech Stack

| Area | Tools |
|---|---|
| Frontend | React, Vite, TypeScript, Nginx |
| Backend | Python, FastAPI, Pydantic |
| State | PostgreSQL, JSON fallback |
| Containers | Docker, Docker Compose |
| Local Kubernetes | Minikube-compatible Kubernetes manifests |
| Observability | Prometheus, Grafana, Loki, Grafana Alloy, Alertmanager |
| Automation | GitHub Actions, DockerHub image publishing |
| Infra setup | Terraform and Helm values for observability components |

---

## 📁 Project Structure

```text
backend/                  FastAPI API, deployment logic, observability clients
frontend/                 React dashboard served by Nginx
k8s/                      Kubernetes namespace, services, deployments, HPA examples
terraform/                Helm-based monitoring/logging setup
monitoring/               Prometheus, Grafana, and Alertmanager config
logging/                  Loki and Grafana Alloy config
docs/                     Getting started, architecture, and troubleshooting guides
scripts/                  Utility scripts
.github/workflows/        CI/CD pipeline
docker-compose.yml        Local full-stack runtime
```

Dockerfiles are service-specific:

- `backend/Dockerfile`
- `frontend/Dockerfile`

There is no root `Dockerfile` because InfraWatch runs multiple services.

---

## ⚙️ Prerequisites

For the easiest local run:

- Git
- Docker Desktop

For source development:

- Python 3.12+
- Node.js 22+

For Kubernetes mode:

- kubectl
- Minikube or another reachable Kubernetes cluster
- Helm 3
- Terraform 1.6+
- DockerHub or another image registry

---

## 🐳 Run the Full Local Stack

From the project root:

```powershell
.\scripts\start-local.ps1
```

This checks Docker, creates `.env` from `.env.example` if needed, validates Docker Compose, starts the full local stack, and prints every local dashboard URL.

To start in the background:

```powershell
.\scripts\start-local.ps1 -Detached
```

To check prerequisites and print URLs without starting containers:

```powershell
.\scripts\start-local.ps1 -CheckOnly
```

The script starts:

- InfraWatch React dashboard
- FastAPI backend
- PostgreSQL
- Prometheus
- Grafana
- Loki
- Grafana Alloy
- Alertmanager

Manual setup is also supported:

```powershell
copy .env.example .env
```

Edit `.env` and set local passwords:

```text
POSTGRES_PASSWORD=your-local-password
GRAFANA_ADMIN_PASSWORD=your-local-password
```

Start the stack:

```powershell
docker compose up --build
```

Open:

| Service | URL |
|---|---|
| 🚀 InfraWatch dashboard | http://localhost:3000 |
| 🧩 FastAPI docs | http://localhost:8000/docs |
| 📈 Prometheus | http://localhost:9090 |
| 📊 Grafana | http://localhost:3001 |
| 📜 Loki | http://localhost:3100 |
| 🔎 Grafana Alloy | http://localhost:12345 |
| 🚨 Alertmanager | http://localhost:9093 |

Grafana login:

```text
Username: admin
Password: value from GRAFANA_ADMIN_PASSWORD in .env
```

Stop the stack:

```powershell
.\scripts\stop-local.ps1
```

Stop and remove local volumes:

```powershell
.\scripts\stop-local.ps1 -Volumes
```

---

## 🩺 Check Your Local Setup

Run the doctor script when something does not start or when you want to confirm your machine is ready:

```powershell
.\scripts\doctor.ps1
```

It checks:

- required InfraWatch files
- `.env` presence
- Docker and Docker Compose
- running InfraWatch containers
- `kubectl`, Kustomize rendering, and current context
- Minikube status
- Kubernetes namespace, pods, and services when a cluster is reachable
- local dashboard/API/Grafana/Prometheus URLs

The script does not change local infrastructure. It reports clear `PASS`, `WARN`, and `FAIL` lines.

---

## 🔌 If Ports Are Already Used

If another project already uses `3000`, `8000`, or `5432`, set alternate ports in `.env`:

```text
FRONTEND_PORT=13000
BACKEND_PORT=18000
POSTGRES_PORT=15432
PROMETHEUS_PORT=19090
GRAFANA_PORT=13001
LOKI_PORT=13100
ALLOY_PORT=12345
ALERTMANAGER_PORT=19093
```

Then run:

```powershell
docker compose up --build
```

With the values above, open:

- Dashboard: http://localhost:13000
- API docs: http://localhost:18000/docs
- Prometheus: http://localhost:19090
- Grafana: http://localhost:13001
- Grafana Alloy: http://localhost:12345

---

## 🖥️ Main Local Interfaces

InfraWatch has one main application dashboard and a few supporting observability tools.

| Interface | Default URL | Alternate URL example |
|---|---|---|
| 🚀 InfraWatch app | http://localhost:3000 | http://localhost:13000 |
| 🧩 FastAPI docs | http://localhost:8000/docs | http://localhost:18000/docs |
| 📈 Prometheus | http://localhost:9090 | http://localhost:19090 |
| 📊 Grafana | http://localhost:3001 | http://localhost:13001 |
| 🔎 Grafana Alloy | http://localhost:12345 | http://localhost:12345 |
| 🚨 Alertmanager | http://localhost:9093 | http://localhost:19093 |

Grafana uses the credentials from your local `.env` file.

---

## 🧪 Demo Mode and Mock Data

Demo Mode exists so InfraWatch remains usable without a Kubernetes cluster.

In Demo Mode:

- Deploy actions validate input and create Kubernetes-style records.
- No real workload is created.
- Metrics and logs use realistic sample data.
- Audit events are still recorded.
- The UI clearly shows a **Demo Mode** banner.

The Docker Compose stack enables safe fallback data by default:

```text
INFRAWATCH_ALLOW_MOCK_OBSERVABILITY=true
INFRAWATCH_EXECUTE_KUBECTL=false
```

For strict real observability, set:

```text
INFRAWATCH_ALLOW_MOCK_OBSERVABILITY=false
```

If Prometheus or Loki has no data in strict mode, the API returns an error instead of hiding it with fake data.

---

## 💻 Run Backend and Frontend Without Docker

Install backend dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Start backend:

```powershell
cd backend
..\.venv\Scripts\python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Start frontend in another terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open:

- Frontend: http://localhost:5173
- Backend docs: http://localhost:8000/docs

---

## ☸️ Run Local Kubernetes Mode

Kubernetes mode is the real local deployment path. It requires an active cluster.

Fastest Minikube path:

```powershell
.\scripts\start-k8s.ps1
```

This checks Docker, `kubectl`, and Minikube, starts Minikube if needed, enables `metrics-server`, creates the `infrawatch` namespace and PostgreSQL secret, applies the Kubernetes manifests, waits for rollouts, and prints the dashboard URL.

The default script uses these public DockerHub images:

```text
docker.io/parthchn178/infrawatch-backend:latest
docker.io/parthchn178/infrawatch-frontend:latest
```

If you fork the project and publish images under your own DockerHub account, run:

```powershell
.\scripts\start-k8s.ps1 -ImageRepository your-dockerhub-username
```

By default, the script keeps mock observability fallback enabled in Kubernetes mode. That means deployments are real, but metrics/logs stay usable even before Prometheus, Loki, and Alloy are installed.

For strict real observability only:

```powershell
.\scripts\start-k8s.ps1 -StrictObservability
```

Use strict mode only after installing the monitoring/logging stack.

To pause Kubernetes workloads while preserving namespace, secrets, and data:

```powershell
.\scripts\stop-k8s.ps1
```

To remove the InfraWatch namespace and local Kubernetes data:

```powershell
.\scripts\stop-k8s.ps1 -RemoveData
```

To also stop Minikube:

```powershell
.\scripts\stop-k8s.ps1 -StopMinikube
```

Manual Kubernetes path:

Start Minikube:

```powershell
minikube start
minikube addons enable metrics-server
```

Create namespace and secrets:

```powershell
kubectl apply -f k8s/namespace.yaml
kubectl create secret generic infrawatch-secrets `
  --namespace infrawatch `
  --from-literal=POSTGRES_PASSWORD=use-a-strong-password `
  --from-literal=DATABASE_URL=postgresql://infrawatch:use-a-strong-password@infrawatch-postgres:5432/infrawatch
```

Deploy InfraWatch:

```powershell
kubectl apply -k k8s
kubectl rollout status deployment/infrawatch-backend --namespace infrawatch --timeout=180s
kubectl rollout status deployment/infrawatch-frontend --namespace infrawatch --timeout=180s
```

Open the frontend:

```powershell
minikube service infrawatch-frontend --namespace infrawatch
```

Install observability with Terraform/Helm:

```powershell
cd terraform
terraform init
terraform apply -var="grafana_admin_password=replace-with-a-strong-password"
```

Then restart strict Kubernetes mode if you want the backend to fail loudly when Prometheus/Loki are unavailable:

```powershell
.\scripts\start-k8s.ps1 -StrictObservability
```

---

## 🔁 GitHub Actions CI/CD

Workflow file:

```text
.github/workflows/ci-cd.yml
```

On push or pull request, it runs:

- ✅ Backend dependency install
- ✅ Ruff lint
- ✅ Backend tests
- ✅ Frontend dependency install
- ✅ ESLint
- ✅ Frontend production build
- ✅ Backend Docker image build
- ✅ Frontend Docker image build

On push to `main`, it can also publish and deploy, but only if these GitHub repository secrets exist:

```text
DOCKERHUB_USERNAME
DOCKERHUB_TOKEN
KUBE_CONFIG_B64
```

Important:

- `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` are required to publish images.
- `KUBE_CONFIG_B64` must point to a reachable Kubernetes cluster.
- A laptop-only Minikube cluster is not reachable from GitHub-hosted runners by default.
- If DockerHub secrets exist but `KUBE_CONFIG_B64` is missing, the workflow still publishes Docker images and clearly skips Kubernetes rollout.

Optional local Minikube deployment:

- Register a self-hosted GitHub Actions runner on your machine with the custom label `local-k8s`.
- Keep Docker Desktop and Minikube running.
- Add this repository variable only when the runner is online:

```text
INFRAWATCH_DEPLOY_TARGET=local-minikube
```

With that variable enabled, the workflow publishes DockerHub images first, then deploys those images to your local Minikube cluster through the self-hosted runner.

---

## 🧭 Main API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/healthz` | Health and active mode summary |
| `POST` | `/deploy` | Create/update a deployment record or Kubernetes workload |
| `GET` | `/deployments` | List known deployments |
| `DELETE` | `/deployment/{name}` | Delete a deployment |
| `POST` | `/deployment/{name}/rollback` | Roll back a Kubernetes deployment |
| `GET` | `/metrics/{service}` | Read service metrics |
| `GET` | `/logs/{service}` | Read service logs |
| `GET` | `/audit-logs` | Read recent deployment/audit events |
| `GET` | `/metrics` | Prometheus scrape endpoint |
| `GET` | `/internal/metrics` | Alternate Prometheus scrape endpoint |

Example deploy request:

```powershell
Invoke-RestMethod `
  -Uri "http://localhost:8000/deploy" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"name":"catalog-api","image":"docker.io/example/catalog-api:latest","replicas":2,"port":8080}'
```

---

## 🧪 Validate the Project

Backend:

```powershell
cd backend
..\.venv\Scripts\python -m ruff check app tests
..\.venv\Scripts\python -m pytest
```

Frontend:

```powershell
cd frontend
npm ci
npm run lint
npm run build
```

Docker Compose config:

```powershell
docker compose config --quiet
```

Kubernetes manifest output:

```powershell
kubectl kustomize k8s
```

---

## 🛠️ Troubleshooting

| Problem | Check |
|---|---|
| Docker stack does not start | Open Docker Desktop and run `docker compose ps` |
| Port already allocated | Set alternate ports in `.env` |
| Dashboard loads but API fails | Confirm backend health at `/healthz` |
| Charts show simulated telemetry | Expected when no real Prometheus/Loki workload data exists |
| Deployments do not create real pods | Set `INFRAWATCH_EXECUTE_KUBECTL=true` in a valid Kubernetes environment |
| GitHub Actions publish fails | Add `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` secrets |
| GitHub Actions deploy fails | Add `KUBE_CONFIG_B64` for a reachable cluster |

More operational troubleshooting is in:

- `docs/ARCHITECTURE.md`
- `docs/TROUBLESHOOTING.md`

---

## 🚧 In Progress

InfraWatch is being improved to become easier for everyone to use as an open-source local Kubernetes tool.

Current focus:

> Making InfraWatch available for everyone who wants a simple local K8s deployment and observability setup.

Planned improvements:

- 🧩 Simple app onboarding form.
- 🐳 Better Docker image guidance for new users.
- ☸️ Easier Minikube/kind setup instructions.
- 📊 Cleaner default Grafana dashboards.
- 🔐 Safer secret setup documentation.
- 🔁 Optional GitHub repo workflow generation in a future version.

---

## 🧾 Honest Limitations

InfraWatch is a local-first SRE/DevOps project, not an enterprise SaaS product.

Not implemented yet:

- user accounts/authentication
- multi-tenant authorization
- GitHub OAuth/App repo onboarding
- automatic builds for arbitrary user repositories from the UI
- TLS/Ingress production exposure
- canary or blue-green release strategy
- WebSocket log streaming

Current deployment scope:

- The UI/API can deploy an already-built container image to the connected Kubernetes cluster.
- The GitHub Actions workflow deploys the InfraWatch app itself when registry and Kubernetes secrets are configured.
- It does not yet let any random user connect their GitHub repository and deploy that repository automatically.

---

## 📌 One-Line Description

InfraWatch is a local-first Kubernetes deployment and observability platform that gives lightweight apps simple health, logs, metrics, rollout, and audit visibility.
