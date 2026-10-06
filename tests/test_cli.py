"""Tests for the InfraWatch package CLI foundation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from infrawatch import __version__
from infrawatch.checks import CommandResult, SubprocessRunner, run_doctor, run_status
from infrawatch.runtime_resources import (
    ResourceError,
    materialize_package_resources,
    resolve_runtime_root,
    validate_runtime_root,
)
from infrawatch.start import StartOptions, run_start
from infrawatch.stop import StopOptions, run_stop


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the CLI module from the current repository checkout."""

    return subprocess.run(
        [sys.executable, "-m", "infrawatch", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_help_command_shows_available_lifecycle_commands() -> None:
    """The package exposes useful help output."""

    result = run_cli("--help")

    assert result.returncode == 0
    assert "InfraWatch local Kubernetes deployment and observability CLI" in result.stdout
    assert "start" in result.stdout
    assert "stop" in result.stdout
    assert "status" in result.stdout
    assert "doctor" in result.stdout


def test_version_command_uses_package_version() -> None:
    """The console version should match the package version."""

    result = run_cli("--version")

    assert result.returncode == 0
    assert result.stdout.strip() == f"InfraWatch {__version__}"


def test_subprocess_runner_uses_utf8_replace_decoding(monkeypatch) -> None:
    """Captured subprocess output should not depend on the Windows console code page."""

    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="ok �", stderr="bad �")

    monkeypatch.setattr("infrawatch.checks.subprocess.run", fake_run)

    result = SubprocessRunner().run(["fake-tool", "status"], timeout=7)

    assert result.returncode == 1
    assert result.stdout == "ok �"
    assert result.stderr == "bad �"
    assert captured["text"] is True
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
    assert captured["timeout"] == 7


def test_stop_help_describes_safe_shutdown() -> None:
    """Stop help should describe the safe ownership-preserving shutdown."""

    result = run_cli("stop", "--help")

    assert result.returncode == 0
    assert "preserving Minikube" in result.stdout
    assert "--remove-observability" in result.stdout


class FakeRunner:
    """Command runner with deterministic responses for doctor/status tests."""

    def __init__(self, responses: dict[tuple[str, ...], CommandResult | list[CommandResult]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, ...]] = []
        self.calls_with_cwd: list[tuple[tuple[str, ...], Path | None]] = []

    def run(self, args: list[str], *, cwd: Path | None = None, timeout: int = 15) -> CommandResult:
        """Return a canned command result."""

        key = tuple(args)
        self.calls.append(key)
        self.calls_with_cwd.append((key, cwd))
        response = self.responses.get(key, CommandResult(1, "", f"unexpected command: {' '.join(args)}"))
        if isinstance(response, list):
            if len(response) > 1:
                return response.pop(0)
            return response[0]
        return response


def deployment_json(replicas: int = 1, ready: int = 1, available: int = 1) -> str:
    """Return a minimal Kubernetes workload JSON payload."""

    return (
        "{"
        f'"spec": {{"replicas": {replicas}}}, '
        f'"status": {{"readyReplicas": {ready}, "availableReplicas": {available}}}'
        "}"
    )


def statefulset_json(replicas: int = 1, ready: int = 1) -> str:
    """Return a minimal Kubernetes StatefulSet JSON payload."""

    return "{" f'"spec": {{"replicas": {replicas}}}, ' f'"status": {{"readyReplicas": {ready}}}' "}"


def empty_pods_json() -> str:
    """Return an empty pod list."""

    return '{"items": []}'


def all_commands_available(monkeypatch) -> None:
    """Pretend all checked external commands are installed."""

    monkeypatch.setattr("infrawatch.checks.command_exists", lambda _command: True)


def test_doctor_all_checks_passing(monkeypatch, capsys, tmp_path) -> None:
    """Doctor returns success when prerequisites and config checks pass."""

    all_commands_available(monkeypatch)
    repo = tmp_path
    (repo / "terraform" / ".terraform").mkdir(parents=True)
    (repo / "terraform" / "main.tf").write_text("", encoding="utf-8")
    (repo / "terraform" / "variables.tf").write_text("", encoding="utf-8")
    (repo / "k8s").mkdir()
    (repo / "k8s" / "kustomization.yaml").write_text("resources: []")
    runner = FakeRunner(
        {
            ("docker", "--version"): CommandResult(0, "Docker version 1"),
            ("docker", "info"): CommandResult(0),
            ("docker", "compose", "version"): CommandResult(0, "Docker Compose version 2"),
            ("kubectl", "version", "--client"): CommandResult(0),
            ("kubectl", "config", "current-context"): CommandResult(0, "minikube"),
            ("kubectl", "cluster-info", "--request-timeout=5s"): CommandResult(0),
            ("kubectl", "get", "namespace", "infrawatch"): CommandResult(0),
            ("minikube", "version"): CommandResult(0, "minikube version: v1"),
            ("minikube", "status", "--format={{.Host}}/{{.APIServer}}"): CommandResult(0, "Running/Running"),
            ("terraform", "version"): CommandResult(0, "Terraform v1.9.0"),
            ("terraform", "-chdir=terraform", "validate"): CommandResult(0),
            ("kubectl", "kustomize", "k8s"): CommandResult(0, "apiVersion: v1"),
        }
    )

    exit_code = run_doctor(runner=runner, repo_root=repo)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Result: Ready" in output
    assert "[PASS] Docker daemon: reachable" in output


def test_doctor_missing_prerequisite_fails(monkeypatch, capsys, tmp_path) -> None:
    """Doctor returns failure when a required prerequisite is missing."""

    monkeypatch.setattr("infrawatch.checks.command_exists", lambda command: command != "docker")

    exit_code = run_doctor(runner=FakeRunner({}), repo_root=tmp_path)

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "[FAIL] Docker CLI: not installed" in output
    assert "Result: Not ready" in output


def test_doctor_unavailable_docker_daemon_fails(monkeypatch, capsys, tmp_path) -> None:
    """Doctor distinguishes an installed Docker CLI from an unavailable daemon."""

    all_commands_available(monkeypatch)
    repo = tmp_path
    (repo / "terraform" / ".terraform").mkdir(parents=True)
    (repo / "terraform" / "main.tf").write_text("", encoding="utf-8")
    (repo / "terraform" / "variables.tf").write_text("", encoding="utf-8")
    (repo / "k8s").mkdir()
    (repo / "k8s" / "kustomization.yaml").write_text("resources: []")
    runner = FakeRunner(
        {
            ("docker", "--version"): CommandResult(0, "Docker version 1"),
            ("docker", "info"): CommandResult(1, "", "daemon unavailable"),
            ("docker", "compose", "version"): CommandResult(0, "Docker Compose version 2"),
            ("kubectl", "version", "--client"): CommandResult(0),
            ("kubectl", "config", "current-context"): CommandResult(0, "minikube"),
            ("kubectl", "cluster-info", "--request-timeout=5s"): CommandResult(0),
            ("kubectl", "get", "namespace", "infrawatch"): CommandResult(0),
            ("minikube", "version"): CommandResult(0, "minikube version: v1"),
            ("minikube", "status", "--format={{.Host}}/{{.APIServer}}"): CommandResult(0, "Running/Running"),
            ("terraform", "version"): CommandResult(0, "Terraform v1.9.0"),
            ("terraform", "-chdir=terraform", "validate"): CommandResult(0),
            ("kubectl", "kustomize", "k8s"): CommandResult(0),
        }
    )

    exit_code = run_doctor(runner=runner, repo_root=repo)

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "[FAIL] Docker daemon: not reachable" in output
    assert "Start Docker Desktop" in output


def test_doctor_warning_does_not_fail(monkeypatch, capsys, tmp_path) -> None:
    """Doctor warnings should be visible but non-fatal."""

    all_commands_available(monkeypatch)
    repo = tmp_path
    (repo / "terraform" / ".terraform").mkdir(parents=True)
    (repo / "terraform" / "main.tf").write_text("", encoding="utf-8")
    (repo / "terraform" / "variables.tf").write_text("", encoding="utf-8")
    (repo / "k8s").mkdir()
    (repo / "k8s" / "kustomization.yaml").write_text("resources: []")
    runner = FakeRunner(
        {
            ("docker", "--version"): CommandResult(0, "Docker version 1"),
            ("docker", "info"): CommandResult(0),
            ("docker", "compose", "version"): CommandResult(0),
            ("kubectl", "version", "--client"): CommandResult(0),
            ("kubectl", "config", "current-context"): CommandResult(0, "minikube"),
            ("kubectl", "cluster-info", "--request-timeout=5s"): CommandResult(1),
            ("minikube", "version"): CommandResult(0, "minikube version: v1"),
            ("minikube", "status", "--format={{.Host}}/{{.APIServer}}"): CommandResult(0, "Stopped/Stopped"),
            ("terraform", "version"): CommandResult(0, "Terraform v1.9.0"),
            ("terraform", "-chdir=terraform", "validate"): CommandResult(0),
            ("kubectl", "kustomize", "k8s"): CommandResult(0),
        }
    )

    exit_code = run_doctor(runner=runner, repo_root=repo)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "[WARN] Kubernetes API: not reachable" in output
    assert "Result: Ready" in output


def healthy_status_runner() -> FakeRunner:
    """Return a fake runner for a healthy InfraWatch Kubernetes environment."""

    return FakeRunner(
        {
            ("kubectl", "config", "current-context"): CommandResult(0, "minikube"),
            ("kubectl", "cluster-info", "--request-timeout=5s"): CommandResult(0),
            ("minikube", "status", "--format={{.Host}}/{{.APIServer}}"): CommandResult(0, "Running/Running"),
            ("kubectl", "get", "namespace", "infrawatch", "-o", "json"): CommandResult(0, "{}"),
            (
                "kubectl",
                "get",
                "deployment",
                "infrawatch-backend",
                "--namespace",
                "infrawatch",
                "-o",
                "json",
            ): CommandResult(0, deployment_json()),
            (
                "kubectl",
                "get",
                "deployment",
                "infrawatch-frontend",
                "--namespace",
                "infrawatch",
                "-o",
                "json",
            ): CommandResult(0, deployment_json()),
            (
                "kubectl",
                "get",
                "statefulset",
                "infrawatch-postgres",
                "--namespace",
                "infrawatch",
                "-o",
                "json",
            ): CommandResult(0, statefulset_json()),
            (
                "kubectl",
                "get",
                "deployment",
                "infrawatch-redis",
                "--namespace",
                "infrawatch",
                "-o",
                "json",
            ): CommandResult(0, deployment_json()),
            (
                "kubectl",
                "get",
                "deployment",
                "infrawatch-prometheus",
                "--namespace",
                "infrawatch",
                "-o",
                "json",
            ): CommandResult(0, deployment_json()),
            (
                "kubectl",
                "get",
                "deployment",
                "infrawatch-grafana",
                "--namespace",
                "infrawatch",
                "-o",
                "json",
            ): CommandResult(0, deployment_json()),
            (
                "kubectl",
                "get",
                "statefulset",
                "infrawatch-loki",
                "--namespace",
                "infrawatch",
                "-o",
                "json",
            ): CommandResult(0, statefulset_json()),
            (
                "kubectl",
                "get",
                "deployment",
                "infrawatch-alertmanager",
                "--namespace",
                "infrawatch",
                "-o",
                "json",
            ): CommandResult(0, deployment_json()),
            ("kubectl", "get", "pods", "--namespace", "infrawatch", "-o", "json"): CommandResult(0, empty_pods_json()),
        }
    )


def test_status_healthy_environment(monkeypatch, capsys) -> None:
    """Status returns success for healthy core and observability workloads."""

    all_commands_available(monkeypatch)

    exit_code = run_status(runner=healthy_status_runner())

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Overall: Running" in output
    assert "[PASS] Backend: ready (1/1)" in output
    assert "[PASS] Redis: ready (1/1)" in output


def test_status_partial_environment_fails_for_core_workload(monkeypatch, capsys) -> None:
    """Status fails when a required component is present but not ready."""

    all_commands_available(monkeypatch)
    runner = healthy_status_runner()
    runner.responses[
        ("kubectl", "get", "deployment", "infrawatch-backend", "--namespace", "infrawatch", "-o", "json")
    ] = CommandResult(0, deployment_json(replicas=1, ready=0, available=0))

    exit_code = run_status(runner=runner)

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "[FAIL] Backend: not ready" in output
    assert "Overall: Needs attention" in output


def test_status_unavailable_kubernetes(monkeypatch, capsys) -> None:
    """Status reports Kubernetes API failures cleanly."""

    all_commands_available(monkeypatch)
    runner = FakeRunner(
        {
            ("kubectl", "config", "current-context"): CommandResult(0, "minikube"),
            ("kubectl", "cluster-info", "--request-timeout=5s"): CommandResult(1),
        }
    )

    exit_code = run_status(runner=runner)

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "[FAIL] Kubernetes API: not reachable" in output


def test_status_missing_namespace(monkeypatch, capsys) -> None:
    """Status fails clearly when the InfraWatch namespace does not exist."""

    all_commands_available(monkeypatch)
    runner = FakeRunner(
        {
            ("kubectl", "config", "current-context"): CommandResult(0, "minikube"),
            ("kubectl", "cluster-info", "--request-timeout=5s"): CommandResult(0),
            ("minikube", "status", "--format={{.Host}}/{{.APIServer}}"): CommandResult(0, "Running/Running"),
            ("kubectl", "get", "namespace", "infrawatch", "-o", "json"): CommandResult(1),
        }
    )

    exit_code = run_status(runner=runner)

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "namespace 'infrawatch' does not exist" in output


def start_repo(tmp_path: Path) -> Path:
    """Create the minimal repository structure start needs."""

    (tmp_path / "terraform").mkdir()
    (tmp_path / "terraform" / "main.tf").write_text("", encoding="utf-8")
    (tmp_path / "terraform" / "variables.tf").write_text("", encoding="utf-8")
    (tmp_path / "k8s").mkdir()
    (tmp_path / "k8s" / "kustomization.yaml").write_text("resources: []", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    return tmp_path


def start_responses() -> dict[tuple[str, ...], CommandResult]:
    """Return successful command responses for the start workflow."""

    responses: dict[tuple[str, ...], CommandResult] = {
        ("docker", "info"): CommandResult(0),
        ("minikube", "status", "--format={{.Host}}/{{.APIServer}}"): CommandResult(0, "Running/Running"),
        ("kubectl", "config", "use-context", "minikube"): CommandResult(0),
        ("kubectl", "cluster-info", "--request-timeout=10s"): CommandResult(0),
        ("minikube", "addons", "enable", "metrics-server"): CommandResult(0),
        ("terraform", "-chdir=terraform", "init"): CommandResult(0),
        ("terraform", "-chdir=terraform", "fmt", "-check"): CommandResult(0),
        ("terraform", "-chdir=terraform", "validate"): CommandResult(0),
        ("terraform", "-chdir=terraform", "state", "list"): CommandResult(1, "", "No state file was found!"),
        ("kubectl", "get", "namespace", "infrawatch"): CommandResult(1),
        ("kubectl", "get", "serviceaccount", "infrawatch-backend", "--namespace", "infrawatch"): CommandResult(1),
        ("kubectl", "get", "clusterrole", "infrawatch-local-reader"): CommandResult(1),
        ("kubectl", "get", "clusterrolebinding", "infrawatch-local-reader"): CommandResult(1),
        ("kubectl", "get", "role", "infrawatch-deployer", "--namespace", "infrawatch"): CommandResult(1),
        ("kubectl", "get", "rolebinding", "infrawatch-deployer", "--namespace", "infrawatch"): CommandResult(1),
        ("kubectl", "get", "resourcequota", "infrawatch-quota", "--namespace", "infrawatch"): CommandResult(1),
        ("kubectl", "get", "configmap", "infrawatch-backend-config", "--namespace", "infrawatch"): CommandResult(1),
        ("terraform", "-chdir=terraform", "plan", "-detailed-exitcode", "-var=kube_context=minikube"): CommandResult(
            2, "Plan: 8 to add, 0 to change, 0 to destroy."
        ),
        ("terraform", "-chdir=terraform", "apply", "-auto-approve", "-var=kube_context=minikube"): CommandResult(0),
        ("kubectl", "get", "secret", "infrawatch-secrets", "--namespace", "infrawatch"): CommandResult(0),
        ("kubectl", "apply", "-k", "k8s"): CommandResult(0),
        (
            "kubectl",
            "patch",
            "configmap",
            "infrawatch-backend-config",
            "--namespace",
            "infrawatch",
            "--type",
            "merge",
            "-p",
            '{"data":{"INFRAWATCH_ALLOW_MOCK_OBSERVABILITY":"true"}}',
        ): CommandResult(0),
        (
            "kubectl",
            "set",
            "image",
            "deployment/infrawatch-backend",
            "backend=docker.io/parthchn178/infrawatch-backend:latest",
            "--namespace",
            "infrawatch",
        ): CommandResult(0),
        (
            "kubectl",
            "set",
            "image",
            "deployment/infrawatch-frontend",
            "frontend=docker.io/parthchn178/infrawatch-frontend:latest",
            "--namespace",
            "infrawatch",
        ): CommandResult(0),
        (
            "kubectl",
            "rollout",
            "restart",
            "deployment/infrawatch-backend",
            "--namespace",
            "infrawatch",
        ): CommandResult(0),
        (
            "kubectl",
            "rollout",
            "status",
            "statefulset/infrawatch-postgres",
            "--namespace",
            "infrawatch",
            "--timeout=240s",
        ): CommandResult(0),
        (
            "kubectl",
            "rollout",
            "status",
            "deployment/infrawatch-redis",
            "--namespace",
            "infrawatch",
            "--timeout=180s",
        ): CommandResult(0),
        (
            "kubectl",
            "rollout",
            "status",
            "deployment/infrawatch-backend",
            "--namespace",
            "infrawatch",
            "--timeout=240s",
        ): CommandResult(0),
        (
            "kubectl",
            "rollout",
            "status",
            "deployment/infrawatch-frontend",
            "--namespace",
            "infrawatch",
            "--timeout=240s",
        ): CommandResult(0),
        ("minikube", "service", "infrawatch-frontend", "--namespace", "infrawatch", "--url"): CommandResult(
            0, "http://127.0.0.1:3000"
        ),
    }
    responses.update(healthy_status_runner().responses)
    return responses


def patch_start_environment(monkeypatch, *, missing: set[str] | None = None) -> None:
    """Patch command and platform discovery for start tests."""

    missing = missing or set()
    monkeypatch.setattr("infrawatch.start.command_exists", lambda command: command not in missing)
    monkeypatch.setattr("infrawatch.checks.command_exists", lambda command: command not in missing)
    monkeypatch.setattr("infrawatch.start.platform_label", lambda: "linux")


def terraform_foundation_state() -> str:
    """Return the expected Terraform-owned foundation resource addresses."""

    return "\n".join(
        [
            "kubernetes_namespace_v1.infrawatch",
            "kubernetes_service_account_v1.backend",
            "kubernetes_cluster_role_v1.local_reader",
            "kubernetes_cluster_role_binding_v1.local_reader",
            "kubernetes_role_v1.deployer",
            "kubernetes_role_binding_v1.deployer",
            "kubernetes_resource_quota_v1.infrawatch",
            "kubernetes_config_map_v1.backend_config",
        ]
    )


def stop_responses() -> dict[tuple[str, ...], CommandResult | list[CommandResult]]:
    """Return successful command responses for the safe stop workflow."""

    return {
        ("kubectl", "config", "current-context"): CommandResult(0, "minikube"),
        ("kubectl", "cluster-info", "--request-timeout=10s"): CommandResult(0),
        ("minikube", "status", "--format={{.Host}}/{{.APIServer}}"): [
            CommandResult(0, "Running/Running"),
            CommandResult(0, "Running/Running"),
        ],
        ("terraform", "-chdir=terraform", "state", "list"): [
            CommandResult(0, terraform_foundation_state()),
            CommandResult(0, terraform_foundation_state()),
        ],
        ("kubectl", "get", "namespace", "infrawatch"): CommandResult(0),
        ("kubectl", "delete", "-k", "k8s", "--ignore-not-found=true"): CommandResult(0),
        (
            "kubectl",
            "wait",
            "--for=delete",
            "pod",
            "-l",
            "app.kubernetes.io/part-of=infrawatch",
            "--namespace",
            "infrawatch",
            "--timeout=180s",
        ): CommandResult(0),
        ("terraform", "-chdir=terraform", "plan", "-detailed-exitcode", "-var=kube_context=minikube"): CommandResult(
            0, "No changes. Your infrastructure matches the configuration."
        ),
    }


def patch_stop_environment(monkeypatch, *, missing: set[str] | None = None) -> None:
    """Patch command discovery for stop tests."""

    missing = missing or set()
    monkeypatch.setattr("infrawatch.stop.command_exists", lambda command: command not in missing)


def test_stop_removes_app_resources_and_preserves_foundation(monkeypatch, capsys, tmp_path) -> None:
    """Stop deletes only app-owned resources and verifies Terraform state remains intact."""

    patch_stop_environment(monkeypatch)
    repo = start_repo(tmp_path)
    runner = FakeRunner(stop_responses())

    exit_code = run_stop(StopOptions(), runner=runner, repo_root=repo)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "[PASS] Kustomize delete: removed app-owned Deployments" in output
    assert "[PASS] Runtime secret: preserved infrawatch-secrets" in output
    assert "[PASS] Terraform state after stop: 8 foundation resources tracked" in output
    assert "[PASS] Terraform plan after stop: no foundation changes required" in output
    assert ("kubectl", "delete", "-k", "k8s", "--ignore-not-found=true") in runner.calls
    assert ("kubectl", "delete", "secret", "infrawatch-secrets", "--namespace", "infrawatch") not in runner.calls
    assert ("kubectl", "delete", "namespace", "infrawatch") not in runner.calls
    assert ("minikube", "delete") not in runner.calls
    assert not any(call[:3] == ("terraform", "-chdir=terraform", "destroy") for call in runner.calls)
    terraform_calls = [(args, cwd) for args, cwd in runner.calls_with_cwd if args and args[0] == "terraform"]
    assert terraform_calls
    assert all(cwd == repo for _args, cwd in terraform_calls)


def test_stop_is_idempotent_when_app_resources_are_absent(monkeypatch, capsys, tmp_path) -> None:
    """Stop succeeds when Kustomize app resources and pods are already absent."""

    patch_stop_environment(monkeypatch)
    responses = stop_responses()
    responses[
        (
            "kubectl",
            "wait",
            "--for=delete",
            "pod",
            "-l",
            "app.kubernetes.io/part-of=infrawatch",
            "--namespace",
            "infrawatch",
            "--timeout=180s",
        )
    ] = CommandResult(1, "", "error: no matching resources found")

    exit_code = run_stop(StopOptions(), runner=FakeRunner(responses), repo_root=start_repo(tmp_path))

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "[PASS] Application pods: already absent" in output


def test_stop_fails_before_delete_when_kubernetes_api_unavailable(monkeypatch, capsys, tmp_path) -> None:
    """Stop must not delete anything when the Kubernetes API cannot be reached."""

    patch_stop_environment(monkeypatch)
    responses = stop_responses()
    responses[("kubectl", "cluster-info", "--request-timeout=10s")] = CommandResult(1, "", "connection refused")
    runner = FakeRunner(responses)

    exit_code = run_stop(StopOptions(), runner=runner, repo_root=start_repo(tmp_path))

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "[FAIL] Kubernetes API: not reachable; no resources were deleted" in output
    assert ("kubectl", "delete", "-k", "k8s", "--ignore-not-found=true") not in runner.calls


def test_stop_fails_before_delete_when_terraform_state_is_incomplete(monkeypatch, capsys, tmp_path) -> None:
    """Stop must preserve the foundation boundary and fail before deletion if Terraform state is incomplete."""

    patch_stop_environment(monkeypatch)
    responses = stop_responses()
    responses[("terraform", "-chdir=terraform", "state", "list")] = CommandResult(
        0, "kubernetes_namespace_v1.infrawatch"
    )
    runner = FakeRunner(responses)

    exit_code = run_stop(StopOptions(), runner=runner, repo_root=start_repo(tmp_path))

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "[FAIL] Terraform state before stop: Terraform foundation state is incomplete" in output
    assert ("kubectl", "delete", "-k", "k8s", "--ignore-not-found=true") not in runner.calls


def test_stop_noops_when_namespace_absent_after_state_check(monkeypatch, capsys, tmp_path) -> None:
    """Stop handles an absent namespace without deleting unrelated cluster resources."""

    patch_stop_environment(monkeypatch)
    responses = stop_responses()
    responses[("kubectl", "get", "namespace", "infrawatch")] = CommandResult(1, "", "not found")
    runner = FakeRunner(responses)

    exit_code = run_stop(StopOptions(), runner=runner, repo_root=start_repo(tmp_path))

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "[PASS] InfraWatch namespace: infrawatch is absent; nothing to delete" in output
    assert ("kubectl", "delete", "-k", "k8s", "--ignore-not-found=true") not in runner.calls


def test_stop_can_remove_observability_when_explicitly_requested(monkeypatch, capsys, tmp_path) -> None:
    """Observability removal is explicit and still avoids Terraform-owned resources."""

    patch_stop_environment(monkeypatch)
    responses = stop_responses()
    responses[("helm", "status", "infrawatch-alloy", "--namespace", "infrawatch")] = CommandResult(0)
    responses[("helm", "uninstall", "infrawatch-alloy", "--namespace", "infrawatch", "--timeout", "5m")] = (
        CommandResult(0)
    )
    responses[("helm", "status", "infrawatch-loki", "--namespace", "infrawatch")] = CommandResult(1)
    responses[("kubectl", "delete", "-k", "k8s/observability", "--ignore-not-found=true")] = CommandResult(0)
    for resource in (
        "configmap/infrawatch-prometheus-config",
        "configmap/infrawatch-alertmanager-config",
        "configmap/infrawatch-grafana-datasources",
        "configmap/infrawatch-grafana-dashboard-provider",
        "configmap/infrawatch-grafana-dashboards",
        "secret/infrawatch-grafana-admin",
    ):
        responses[
            (
                "kubectl",
                "delete",
                resource,
                "--namespace",
                "infrawatch",
                "--ignore-not-found=true",
            )
        ] = CommandResult(0)
    runner = FakeRunner(responses)

    exit_code = run_stop(StopOptions(remove_observability=True), runner=runner, repo_root=start_repo(tmp_path))

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "[PASS] Helm infrawatch-alloy: uninstalled" in output
    assert "[PASS] Observability manifests: removed if present" in output


def test_start_successful_sequence(monkeypatch, capsys, tmp_path) -> None:
    """Start runs the local Kubernetes orchestration sequence successfully."""

    patch_start_environment(monkeypatch)
    monkeypatch.setattr("infrawatch.start.Starter._url_reachable", lambda _self, _url: True)
    runner = FakeRunner(start_responses())
    repo = start_repo(tmp_path)

    exit_code = run_start(StartOptions(skip_observability=True), runner=runner, repo_root=repo)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "InfraWatch is running." in output
    assert "Dashboard: http://127.0.0.1:3000" in output
    assert ("terraform", "-chdir=terraform", "apply", "-auto-approve", "-var=kube_context=minikube") in runner.calls
    assert ("kubectl", "apply", "-k", "k8s") in runner.calls
    terraform_calls = [(args, cwd) for args, cwd in runner.calls_with_cwd if args and args[0] == "terraform"]
    assert terraform_calls
    assert all(cwd == repo for _args, cwd in terraform_calls)


def test_start_imports_existing_foundation_into_runtime_state(monkeypatch, capsys, tmp_path) -> None:
    """Existing foundation resources are imported into the same Terraform state used for plan/apply."""

    patch_start_environment(monkeypatch)
    monkeypatch.setattr("infrawatch.start.Starter._url_reachable", lambda _self, _url: True)
    responses = start_responses()
    resources = [
        "kubernetes_namespace_v1.infrawatch",
        "kubernetes_service_account_v1.backend",
        "kubernetes_cluster_role_v1.local_reader",
        "kubernetes_cluster_role_binding_v1.local_reader",
        "kubernetes_role_v1.deployer",
        "kubernetes_role_binding_v1.deployer",
        "kubernetes_resource_quota_v1.infrawatch",
        "kubernetes_config_map_v1.backend_config",
    ]
    responses[("terraform", "-chdir=terraform", "state", "list")] = [
        CommandResult(1, "", "No state file was found!"),
        CommandResult(0, "\n".join(resources)),
    ]
    responses[("kubectl", "get", "namespace", "infrawatch")] = CommandResult(0)
    responses[("kubectl", "get", "serviceaccount", "infrawatch-backend", "--namespace", "infrawatch")] = CommandResult(
        0
    )
    responses[("kubectl", "get", "clusterrole", "infrawatch-local-reader")] = CommandResult(0)
    responses[("kubectl", "get", "clusterrolebinding", "infrawatch-local-reader")] = CommandResult(0)
    responses[("kubectl", "get", "role", "infrawatch-deployer", "--namespace", "infrawatch")] = CommandResult(0)
    responses[("kubectl", "get", "rolebinding", "infrawatch-deployer", "--namespace", "infrawatch")] = CommandResult(0)
    responses[("kubectl", "get", "resourcequota", "infrawatch-quota", "--namespace", "infrawatch")] = CommandResult(0)
    responses[("kubectl", "get", "configmap", "infrawatch-backend-config", "--namespace", "infrawatch")] = (
        CommandResult(0)
    )
    import_commands = [
        (
            "terraform",
            "-chdir=terraform",
            "import",
            "-var=kube_context=minikube",
            "kubernetes_namespace_v1.infrawatch",
            "infrawatch",
        ),
        (
            "terraform",
            "-chdir=terraform",
            "import",
            "-var=kube_context=minikube",
            "kubernetes_service_account_v1.backend",
            "infrawatch/infrawatch-backend",
        ),
        (
            "terraform",
            "-chdir=terraform",
            "import",
            "-var=kube_context=minikube",
            "kubernetes_cluster_role_v1.local_reader",
            "infrawatch-local-reader",
        ),
        (
            "terraform",
            "-chdir=terraform",
            "import",
            "-var=kube_context=minikube",
            "kubernetes_cluster_role_binding_v1.local_reader",
            "infrawatch-local-reader",
        ),
        (
            "terraform",
            "-chdir=terraform",
            "import",
            "-var=kube_context=minikube",
            "kubernetes_role_v1.deployer",
            "infrawatch/infrawatch-deployer",
        ),
        (
            "terraform",
            "-chdir=terraform",
            "import",
            "-var=kube_context=minikube",
            "kubernetes_role_binding_v1.deployer",
            "infrawatch/infrawatch-deployer",
        ),
        (
            "terraform",
            "-chdir=terraform",
            "import",
            "-var=kube_context=minikube",
            "kubernetes_resource_quota_v1.infrawatch",
            "infrawatch/infrawatch-quota",
        ),
        (
            "terraform",
            "-chdir=terraform",
            "import",
            "-var=kube_context=minikube",
            "kubernetes_config_map_v1.backend_config",
            "infrawatch/infrawatch-backend-config",
        ),
    ]
    for command in import_commands:
        responses[command] = CommandResult(0)
    responses[("terraform", "-chdir=terraform", "plan", "-detailed-exitcode", "-var=kube_context=minikube")] = (
        CommandResult(0, "No changes. Your infrastructure matches the configuration.")
    )
    repo = start_repo(tmp_path)
    runner = FakeRunner(responses)

    exit_code = run_start(StartOptions(skip_observability=True), runner=runner, repo_root=repo)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "[PASS] Terraform imports: imported 8 existing foundation resource(s)" in output
    assert "[PASS] Terraform plan: no foundation changes required" in output
    for command in import_commands:
        assert command in runner.calls
    terraform_calls = [(args, cwd) for args, cwd in runner.calls_with_cwd if args and args[0] == "terraform"]
    assert all(cwd == repo for _args, cwd in terraform_calls)


def test_start_fails_when_prerequisite_missing(monkeypatch, capsys, tmp_path) -> None:
    """Start fails before mutation when a required command is missing."""

    patch_start_environment(monkeypatch, missing={"docker"})

    exit_code = run_start(StartOptions(), runner=FakeRunner({}), repo_root=start_repo(tmp_path))

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "[FAIL] docker: docker was not found on PATH" in output


def test_start_minikube_start_failure(monkeypatch, capsys, tmp_path) -> None:
    """Start reports Minikube startup failures cleanly."""

    patch_start_environment(monkeypatch)
    responses = start_responses()
    responses[("kubectl", "config", "current-context")] = CommandResult(0, "other-context")
    responses[("kubectl", "cluster-info", "--request-timeout=10s")] = CommandResult(1)
    responses[("minikube", "status", "--format={{.Host}}/{{.APIServer}}")] = CommandResult(0, "Stopped/Stopped")
    responses[("minikube", "start", "--memory=3072", "--cpus=2")] = CommandResult(1, "", "start failed")

    exit_code = run_start(StartOptions(), runner=FakeRunner(responses), repo_root=start_repo(tmp_path))

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "[FAIL] Minikube: failed to start Minikube" in output


def test_start_terraform_failure(monkeypatch, capsys, tmp_path) -> None:
    """Start stops and reports Terraform failures."""

    patch_start_environment(monkeypatch)
    responses = start_responses()
    responses[("terraform", "-chdir=terraform", "apply", "-auto-approve", "-var=kube_context=minikube")] = (
        CommandResult(1, "", "apply failed")
    )

    exit_code = run_start(StartOptions(), runner=FakeRunner(responses), repo_root=start_repo(tmp_path))

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "[FAIL] Terraform apply: Terraform apply failed" in output


def test_start_terraform_failure_shows_meaningful_stderr(monkeypatch, capsys, tmp_path) -> None:
    """Terraform errors should show the useful stderr line, not only the box footer."""

    patch_start_environment(monkeypatch)
    responses = start_responses()
    responses[("terraform", "-chdir=terraform", "plan", "-detailed-exitcode", "-var=kube_context=minikube")] = (
        CommandResult(
            1,
            "",
            '╷\n│ Error: namespaces "infrawatch" already exists\n│ import required before apply\n╵',
        )
    )

    exit_code = run_start(StartOptions(), runner=FakeRunner(responses), repo_root=start_repo(tmp_path))

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "[FAIL] Terraform plan: Terraform plan failed" in output
    assert 'namespaces "infrawatch" already exists' in output
    assert "â•µ" not in output


def test_start_kustomize_failure(monkeypatch, capsys, tmp_path) -> None:
    """Start reports Kustomize apply failures."""

    patch_start_environment(monkeypatch)
    responses = start_responses()
    responses[("kubectl", "apply", "-k", "k8s")] = CommandResult(1, "", "apply -k failed")

    exit_code = run_start(StartOptions(), runner=FakeRunner(responses), repo_root=start_repo(tmp_path))

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "[FAIL] Kustomize apply: Kustomize apply failed" in output


def test_start_readiness_timeout(monkeypatch, capsys, tmp_path) -> None:
    """Start reports rollout timeout/failure for required components."""

    patch_start_environment(monkeypatch)
    responses = start_responses()
    responses[
        (
            "kubectl",
            "rollout",
            "status",
            "deployment/infrawatch-backend",
            "--namespace",
            "infrawatch",
            "--timeout=240s",
        )
    ] = CommandResult(1, "", "timed out waiting for backend")
    responses[("kubectl", "get", "deployment", "infrawatch-backend", "--namespace", "infrawatch", "-o", "json")] = (
        CommandResult(0, deployment_json(replicas=1, ready=0, available=0))
    )

    exit_code = run_start(StartOptions(), runner=FakeRunner(responses), repo_root=start_repo(tmp_path))

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "[FAIL] Backend: Backend failed" in output


def test_start_is_idempotent_when_minikube_already_running(monkeypatch, tmp_path) -> None:
    """Start should not recreate Minikube when it is already running."""

    patch_start_environment(monkeypatch)
    monkeypatch.setattr("infrawatch.start.Starter._url_reachable", lambda _self, _url: True)
    runner = FakeRunner(start_responses())

    exit_code = run_start(StartOptions(skip_observability=True), runner=runner, repo_root=start_repo(tmp_path))

    assert exit_code == 0
    assert ("minikube", "start", "--memory=3072", "--cpus=2") not in runner.calls


def test_start_command_not_found_handling(monkeypatch, capsys, tmp_path) -> None:
    """Start handles command-not-found through the prerequisite check."""

    patch_start_environment(monkeypatch, missing={"terraform"})

    exit_code = run_start(StartOptions(), runner=FakeRunner({}), repo_root=start_repo(tmp_path))

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "[FAIL] terraform: terraform was not found on PATH" in output


def test_packaged_resources_materialize_to_filesystem(tmp_path) -> None:
    """Packaged Terraform and Kustomize resources can be exposed as real files."""

    root = materialize_package_resources(tmp_path / "runtime")

    assert (root / "terraform" / "main.tf").exists()
    assert (root / "terraform" / "variables.tf").exists()
    assert (root / "k8s" / "kustomization.yaml").exists()
    assert (root / "monitoring" / "prometheus" / "prometheus.yml").exists()
    validate_runtime_root(root)


def test_runtime_resolution_works_without_repository(monkeypatch, tmp_path) -> None:
    """Resource resolution should not depend on the current working directory being the repo."""

    monkeypatch.setattr("infrawatch.runtime_resources.find_repo_root", lambda: None)
    monkeypatch.setenv("INFRAWATCH_RESOURCE_CACHE", str(tmp_path / "cache"))

    root = resolve_runtime_root(prefer_repo=True)

    assert root.is_dir()
    assert root.is_relative_to(tmp_path / "cache")
    assert (root / "terraform" / "main.tf").exists()
    assert (root / "k8s" / "kustomization.yaml").exists()


def test_runtime_resolution_prefers_repository_when_available(monkeypatch, tmp_path) -> None:
    """Development checkouts should continue to use source files directly."""

    repo = tmp_path / "repo"
    (repo / "terraform").mkdir(parents=True)
    (repo / "terraform" / "main.tf").write_text("", encoding="utf-8")
    (repo / "terraform" / "variables.tf").write_text("", encoding="utf-8")
    (repo / "k8s").mkdir()
    (repo / "k8s" / "kustomization.yaml").write_text("", encoding="utf-8")
    monkeypatch.setattr("infrawatch.runtime_resources.find_repo_root", lambda: repo)

    assert resolve_runtime_root(prefer_repo=True) == repo


def test_missing_runtime_resources_are_reported(tmp_path) -> None:
    """Missing required runtime resources should fail clearly."""

    with pytest.raises(ResourceError):
        validate_runtime_root(tmp_path)
