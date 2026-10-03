# InfraWatch Architecture

InfraWatch has two explicit modes.

## CLI boundary

```text
infrawatch CLI
  -> orchestration layer
  -> existing scripts / Terraform / Kustomize / Docker / Kubernetes
```

The Python package currently provides:

- `infrawatch --help`
- `infrawatch --version`
- `infrawatch doctor` for local prerequisite and readiness diagnostics
- `infrawatch status` for current Kubernetes runtime state
- `infrawatch start` for local Minikube/Terraform/Kustomize startup

The CLI does not duplicate Terraform or Kubernetes manifests. `infrawatch start` orchestrates the existing tools and resource definitions:

```text
infrawatch start
  -> prerequisite checks
  -> minikube start/use-context
  -> terraform init/fmt/validate/apply
  -> kubectl apply -k k8s
  -> rollout waits
  -> optional existing observability installer on Windows when requested
  -> status validation
```

The `stop` lifecycle operation is still intentionally left to the existing scripts until cleanup semantics are designed.

## Packaged runtime resources

The Python package includes the runtime resources required by the CLI:

```text
infrawatch.resources
  -> terraform/
  -> k8s/
  -> monitoring/
  -> logging/
  -> scripts/start-observability.ps1
```

When running from a source checkout, the CLI uses the checkout files directly. When running from an installed wheel outside the repository, it materializes the packaged resources into a versioned user cache directory and runs Terraform/Kustomize from there. Terraform state and downloaded providers are not packaged.

## Demo Mode

```text
React frontend
  -> browser sandbox store
  -> manifest simulation
  -> mock metrics/logs
```

Demo Mode is safe for local UI exploration and portfolio screenshots. It does not run Kubernetes workloads.

## Real Local Kubernetes Mode

```text
Minikube
  -> frontend Deployment
  -> Nginx /api proxy
  -> backend Service
  -> FastAPI
  -> kubectl
  -> user workload Deployment + Service
  -> Prometheus/Loki/Grafana/Alertmanager
```

Real mode is enabled by Kubernetes config:

- `INFRAWATCH_EXECUTE_KUBECTL=true`
- `INFRAWATCH_ALLOW_MOCK_OBSERVABILITY=false`
- `DATABASE_URL=postgresql://...`

## Deployment flow

```text
GitHub
  -> GitHub Actions validate job
  -> Docker build
  -> DockerHub image tagged with GITHUB_SHA
  -> Kubernetes apply
  -> kubectl set image
  -> kubectl rollout status
```

The workflow uses immutable SHA tags for the rollout. The `latest` tag is also pushed for convenience, but Kubernetes deployment uses `${GITHUB_SHA}`.

## Application flow

```text
Browser
  -> React dashboard
  -> Nginx /api proxy
  -> infrawatch-backend Service
  -> FastAPI
  -> kubectl
  -> Kubernetes Deployment/Service
```

The frontend container uses Nginx to serve static assets and proxy `/api/*` to `http://infrawatch-backend:8000/`. This avoids adding Ingress just to connect the dashboard to the API in Minikube.

## Deployment request internals

1. React sends service name, image, replicas, and port.
2. FastAPI validates with Pydantic.
3. FastAPI generates a Kubernetes `List` containing:
   - Deployment
   - Service
4. In Demo Mode, the manifest is returned and state is recorded without kubectl.
5. In real mode:
   - `kubectl apply -f -`
   - `kubectl rollout status deployment/<name>`
   - `kubectl get deployment/<name> -o json`
   - Ready and available replicas are verified.
6. The deployment is marked `Running` only after rollout verification.
7. Rollout failures are stored as `Failed` with failure details.

## Rollback flow

```text
React rollback action
  -> POST /deployment/{name}/rollback
  -> kubectl rollout undo deployment/{name}
  -> kubectl rollout status
  -> ready/available replica verification
  -> audit event
```

Rollback is intentionally simple. It uses standard Kubernetes Deployment revision history instead of a custom release database.

## Metrics flow

```text
Application/Kubernetes
  -> Prometheus scrape
  -> Redis short TTL cache
  -> FastAPI /metrics/{service}
  -> React charts
```

Prometheus sources:

- kubelet/cAdvisor metrics for CPU and memory.
- kube-state-metrics for pod/deployment/HPA state.
- FastAPI `/metrics` or `/internal/metrics` for app request metrics.
- annotated managed pods with `prometheus.io/scrape=true`.

Backend queries:

- CPU: `container_cpu_usage_seconds_total`
- Memory: `container_memory_working_set_bytes`
- Request rate: `http_requests_total`
- Error rate: `http_requests_total{status=~"5.."}`

Redis sits between FastAPI and Prometheus as a cache-aside optimization:

```text
FastAPI
  -> Redis cache lookup
    -> hit: return cached Prometheus response
    -> miss: query Prometheus, store real response briefly, return response
```

The cache key includes the service, namespace, Prometheus URL, PromQL queries, range, and step. The TTL is intentionally short so repeated dashboard refreshes avoid duplicate Prometheus API work without hiding fresh telemetry for long.

Redis is optional. If Redis is unavailable, FastAPI continues directly to Prometheus. Mock fallback responses are not cached as Prometheus truth.

In real mode, mock fallback is disabled. If Prometheus is unavailable, the API returns an error instead of fake data.

## Logs flow

```text
Application stdout/stderr
  -> Kubernetes container logs
  -> Grafana Alloy
  -> Loki
  -> FastAPI /logs/{service}
  -> React near-real-time log polling
```

The dashboard polls every 5 seconds. This is near-real-time log streaming via polling, not WebSockets.

Grafana Alloy labels pod logs with:

- `namespace`
- `pod`
- `container`
- `app`

FastAPI queries Loki with:

```logql
{app="<service>"}
```

## Alerts flow

```text
Kubernetes metrics
  -> Prometheus rules
  -> Alertmanager
  -> webhook receiver
```

Important alerts:

- `PodCrashLooping`
- `HighCpuUsage`
- `ServiceDown`
- `DeploymentUnavailable`

For local demos, run:

```bash
python scripts/alert_receiver.py
```

Then configure Alertmanager to reach `http://host.docker.internal:9999/alerts` or port-forward as needed.

## Autoscaling flow

```text
Traffic/load
  -> backend CPU usage
  -> metrics-server
  -> HPA controller
  -> desired replicas
  -> Deployment
  -> new Pods
```

InfraWatch includes an HPA for `deployment/infrawatch-backend`:

- min replicas: 2
- max replicas: 6
- CPU target: 70%

This requires Minikube metrics-server:

```bash
minikube addons enable metrics-server
kubectl get hpa -n infrawatch -w
```

## Persistence flow

```text
FastAPI
  -> DATABASE_URL configured?
    -> yes: PostgreSQL deployments/audit tables
    -> no: JSON file fallback
```

Real local Kubernetes mode should use PostgreSQL. Demo Mode and tests can safely use JSON fallback.
