# Changelog

All notable project changes will be summarized here.

## Unreleased - developer preview

InfraWatch is currently an early local-first developer preview. It is not a formal stable release.

Current capabilities:

- local Kubernetes deployment workflow with Minikube, Terraform, and Kustomize;
- installable Python CLI with `doctor`, `start`, `status`, and `stop`;
- React dashboard for deployment, workload, metrics, logs, and alerts views;
- FastAPI backend for deployment orchestration and observability APIs;
- Prometheus-backed service metrics;
- short-lived Redis caching for repeated Prometheus dashboard queries;
- Kubernetes workload health summaries;
- Loki-backed Kubernetes logs through Grafana Alloy labels;
- Alertmanager-backed alert visibility;
- Docker Compose development stack;
- packaged runtime resources for local CLI usage outside the repository checkout.

Known development status:

- designed for local development, testing, and SRE learning;
- not a hosted SaaS or multi-tenant production control plane;
- not yet published to PyPI;
- public contribution and security processes are being prepared.
