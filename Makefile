# Developer and deployment commands for InfraWatch.
SHELL := /bin/sh

K8S_NAMESPACE ?= infrawatch

.PHONY: up terraform-init terraform-apply deploy monitor logs clean test

up:
	docker compose up --build

terraform-init:
	terraform -chdir=terraform init
	terraform -chdir=terraform fmt -check
	terraform -chdir=terraform validate

terraform-apply: terraform-init
	terraform -chdir=terraform apply

deploy: terraform-apply
	kubectl apply -k k8s

monitor:
	kubectl port-forward --namespace $(K8S_NAMESPACE) svc/infrawatch-grafana 3000:80

logs:
	kubectl logs --namespace $(K8S_NAMESPACE) deployment/infrawatch-backend --tail=100

test:
	cd backend && pytest
	cd frontend && npm run build

clean:
	docker compose down --volumes --remove-orphans
	kubectl delete -k k8s --ignore-not-found=true
