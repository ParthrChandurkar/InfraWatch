# Security Policy

InfraWatch is an early local-first developer project for Docker, Kubernetes, and observability workflows. It is not a hosted service or production security product.

## Supported versions

Security reports are currently accepted for the `main` branch and the latest committed developer-preview code. Formal version support will be defined before a stable release.

## Reporting a vulnerability

Please do not publish exploitable details publicly before maintainers have had a chance to review the issue.

Use GitHub private vulnerability reporting for security vulnerabilities in this repository. If that channel is unavailable, open a public issue only to ask maintainers to establish a private coordination path; do not include exploitable details, secrets, kubeconfig contents, tokens, passwords, private logs, or exploit payloads in a public issue.

## Useful report details

Helpful reports include:

- affected commit or version;
- operating system and local Kubernetes environment;
- Docker, Minikube, kubectl, Terraform, and Helm versions when relevant;
- affected component, such as CLI, FastAPI backend, React frontend, Docker Compose, Kubernetes manifests, Terraform, RBAC, Prometheus, Loki, Alertmanager, or Grafana Alloy;
- clear reproduction steps using sanitized examples;
- expected versus actual behavior;
- impact and suggested mitigation, if known.

## Security scope

Security-relevant areas for InfraWatch include:

- handling of local `.env` files and Kubernetes Secrets;
- kubeconfig and Kubernetes API access;
- Terraform-managed RBAC and namespace boundaries;
- Docker image usage and local Docker daemon access;
- local port-forwards and dashboard/API exposure;
- Prometheus, Loki, Alertmanager, Grafana, and Alloy configuration;
- generated artifacts that could accidentally include secrets.

Never commit real credentials, DockerHub tokens, kubeconfig files, Terraform state, or local environment files.
