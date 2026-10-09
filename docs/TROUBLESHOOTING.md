# InfraWatch Troubleshooting

Use this as an SRE-style operations runbook for diagnosing the local or Kubernetes deployment.

## Application unavailable

First command:

```bash
kubectl get pods,svc,endpoints -n infrawatch
```

Look for:

- Pods not `Running`
- Services without endpoints
- Frontend service not exposed
- Backend service missing endpoints

Next:

```bash
kubectl describe pod <pod> -n infrawatch
kubectl get events -n infrawatch --sort-by=.lastTimestamp
```

Fix:

- Correct image/tag.
- Fix probes.
- Create missing Secret.
- Restart rollout after config change.

## Backend pod cannot start

First command:

```bash
kubectl describe pod -l app.kubernetes.io/name=infrawatch-backend -n infrawatch
```

Look for:

- `CreateContainerConfigError`
- missing `infrawatch-secrets`
- database connection failures

Next:

```bash
kubectl logs deployment/infrawatch-backend -n infrawatch
```

Fix:

```bash
kubectl create secret generic infrawatch-secrets \
  --namespace infrawatch \
  --from-literal=POSTGRES_PASSWORD=use-a-strong-password \
  --from-literal=DATABASE_URL=postgresql://infrawatch:use-a-strong-password@infrawatch-postgres:5432/infrawatch
```

## Frontend loads but API fails

First command:

```bash
kubectl exec -n infrawatch deployment/infrawatch-frontend -- wget -qO- http://infrawatch-backend:8000/healthz
```

Look for:

- DNS failure
- connection refused
- 502 through Nginx

Next:

```bash
kubectl logs deployment/infrawatch-frontend -n infrawatch
kubectl get svc infrawatch-backend -n infrawatch
kubectl get endpoints infrawatch-backend -n infrawatch
```

Fix:

- Ensure backend Service exists.
- Ensure backend pods are Ready.
- Ensure frontend Nginx uses `/api` proxy.

## CrashLoopBackOff

Trigger:

```bash
kubectl apply -f k8s/failure-demos/crashloop.yaml
```

Diagnose:

```bash
kubectl get pods -n infrawatch
kubectl describe pod -l app.kubernetes.io/name=crashloop-demo -n infrawatch
kubectl logs -l app.kubernetes.io/name=crashloop-demo -n infrawatch --previous
kubectl get events -n infrawatch --sort-by=.lastTimestamp
```

Look for:

- `CrashLoopBackOff`
- non-zero exit code
- previous logs showing the crash

Recover:

```bash
kubectl delete -f k8s/failure-demos/crashloop.yaml
```

## ImagePullBackOff

Trigger:

```bash
kubectl apply -f k8s/failure-demos/imagepullbackoff.yaml
```

Diagnose:

```bash
kubectl describe pod -l app.kubernetes.io/name=imagepull-demo -n infrawatch
```

Look for:

- bad image name
- missing tag
- private registry auth failure

Recover:

```bash
kubectl delete -f k8s/failure-demos/imagepullbackoff.yaml
```

## Readiness probe failure

Trigger:

```bash
kubectl apply -f k8s/failure-demos/readiness-failure.yaml
```

Diagnose:

```bash
kubectl get pods -n infrawatch
kubectl describe pod -l app.kubernetes.io/name=readiness-failure-demo -n infrawatch
kubectl get endpoints -n infrawatch
```

Look for:

- Pod running but not Ready
- readiness probe HTTP 404
- Service endpoint absent

Recover:

```bash
kubectl delete -f k8s/failure-demos/readiness-failure.yaml
```

## HPA not scaling

First command:

```bash
kubectl get hpa -n infrawatch
```

Next:

```bash
kubectl describe hpa infrawatch-backend -n infrawatch
kubectl top pods -n infrawatch
kubectl get apiservice v1beta1.metrics.k8s.io
```

Look for:

- metrics-server unavailable
- CPU shown as `<unknown>`
- no CPU requests on target pods
- not enough load

Fix:

```bash
minikube addons enable metrics-server
kubectl rollout restart deployment/infrawatch-backend -n infrawatch
```

## Prometheus has no data

First command:

```bash
kubectl port-forward -n infrawatch svc/infrawatch-prometheus 9090:9090
```

Open:

```text
http://localhost:9090/targets
```

Look for:

- target down
- wrong path
- wrong port
- pod annotation missing

Useful queries:

```promql
up
container_cpu_usage_seconds_total{namespace="infrawatch"}
http_requests_total
```

## Loki has no logs

First command:

```bash
kubectl get pods -n infrawatch | grep alloy
```

Next:

```bash
kubectl logs -n infrawatch -l app.kubernetes.io/name=alloy
```

Grafana/Loki query:

```logql
{namespace="infrawatch"}
{app="infrawatch-backend"}
```

Look for:

- Grafana Alloy cannot reach Loki
- labels do not match FastAPI query
- selected service emits no logs

## Alertmanager has no alerts or is unavailable

First command:

```bash
kubectl get deployment/infrawatch-alertmanager svc/infrawatch-alertmanager endpoints/infrawatch-alertmanager -n infrawatch
```

Check readiness:

```bash
kubectl port-forward -n infrawatch svc/infrawatch-alertmanager 9093:9093
curl http://localhost:9093/-/ready
curl http://localhost:9093/api/v2/alerts
```

How to read the result:

- `[]` from `/api/v2/alerts` means Alertmanager is reachable but has no active alerts.
- Connection failures, HTTP 5xx responses, or a failed readiness endpoint mean the Alertmanager API is unavailable.
- In real local mode, the InfraWatch backend should report an availability error instead of replacing an unavailable Alertmanager with fake alerts.

Inspect Prometheus rule and target health:

```bash
kubectl port-forward -n infrawatch svc/infrawatch-prometheus 9090:9090
```

Open:

```text
http://localhost:9090/rules
http://localhost:9090/targets
```

Look for:

- `infrawatch.rules` loaded from `infrawatch-alert-rules`;
- Alertmanager target configured and healthy;
- rule expressions with no matching series;
- Prometheus unable to scrape backend, kube-state-metrics, or Kubernetes/cAdvisor metrics.

Collect sanitized logs:

```bash
kubectl logs deployment/infrawatch-alertmanager -n infrawatch --tail=100
kubectl logs deployment/infrawatch-prometheus -n infrawatch --tail=100
kubectl logs deployment/infrawatch-backend -n infrawatch --tail=100
```

Do not paste credentials, kubeconfig content, private URLs, or sensitive application logs into public issues.

## Rollout stuck or failed

First command:

```bash
kubectl rollout status deployment/<name> -n infrawatch --timeout=180s
```

Next:

```bash
kubectl rollout history deployment/<name> -n infrawatch
kubectl describe deployment/<name> -n infrawatch
kubectl describe pod -l app.kubernetes.io/name=<name> -n infrawatch
```

Rollback:

```bash
kubectl rollout undo deployment/<name> -n infrawatch
kubectl rollout status deployment/<name> -n infrawatch
```
