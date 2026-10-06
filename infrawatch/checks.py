"""Cross-platform doctor and status checks for the InfraWatch CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from .runtime_resources import ResourceError, resolve_runtime_root, validate_runtime_root

DEFAULT_NAMESPACE = "infrawatch"
REQUEST_TIMEOUT = "5s"


class Level(str, Enum):
    """Severity level for one CLI check."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class CheckResult:
    """One user-facing check result."""

    level: Level
    name: str
    message: str
    hint: str = ""


@dataclass(frozen=True)
class CommandResult:
    """Small command result abstraction for testable subprocess calls."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


class Runner(Protocol):
    """Protocol for command execution."""

    def run(self, args: list[str], *, cwd: Path | None = None, timeout: int = 15) -> CommandResult:
        """Run a command and capture output."""


class SubprocessRunner:
    """Subprocess-backed command runner."""

    def run(self, args: list[str], *, cwd: Path | None = None, timeout: int = 15) -> CommandResult:
        """Run a command and capture output without raising for normal command failures."""

        try:
            completed = subprocess.run(
                args,
                cwd=str(cwd) if cwd else None,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except FileNotFoundError:
            return CommandResult(127, "", f"{args[0]} was not found")
        except subprocess.TimeoutExpired as exc:
            return CommandResult(124, exc.stdout or "", exc.stderr or "command timed out")
        return CommandResult(completed.returncode, completed.stdout.strip(), completed.stderr.strip())


def command_exists(command: str) -> bool:
    """Return whether a command is available on PATH."""

    return shutil.which(command) is not None


def format_results(title: str, results: list[CheckResult], *, summary: str) -> str:
    """Render check results in a compact terminal-friendly format."""

    lines = [title, ""]
    for result in results:
        lines.append(f"[{result.level.value}] {result.name}: {result.message}")
        if result.hint:
            lines.append(f"       Hint: {result.hint}")
    lines.extend(["", summary])
    return "\n".join(lines)


def has_failures(results: list[CheckResult]) -> bool:
    """Return True if any result is a failure."""

    return any(result.level == Level.FAIL for result in results)


def run_doctor(namespace: str = DEFAULT_NAMESPACE, runner: Runner | None = None, repo_root: Path | None = None) -> int:
    """Run local machine readiness checks and print a concise report."""

    active_runner = runner or SubprocessRunner()
    try:
        root = repo_root if repo_root is not None else resolve_runtime_root(prefer_repo=True)
    except ResourceError:
        root = None
    results = doctor_results(namespace=namespace, runner=active_runner, repo_root=root)
    failures = sum(result.level == Level.FAIL for result in results)
    warnings = sum(result.level == Level.WARN for result in results)
    summary = "Result: Ready" if failures == 0 else "Result: Not ready"
    if warnings:
        summary += f" ({warnings} warning(s))"
    print(format_results("InfraWatch Doctor", results, summary=summary))
    return 1 if failures else 0


def run_status(namespace: str = DEFAULT_NAMESPACE, runner: Runner | None = None) -> int:
    """Run current InfraWatch runtime status checks and print a concise report."""

    active_runner = runner or SubprocessRunner()
    results = status_results(namespace=namespace, runner=active_runner)
    failures = sum(result.level == Level.FAIL for result in results)
    warnings = sum(result.level == Level.WARN for result in results)
    summary = "Overall: Running" if failures == 0 else "Overall: Needs attention"
    if warnings:
        summary += f" ({warnings} warning(s))"
    print(format_results("InfraWatch Status", results, summary=summary))
    return 1 if failures else 0


def doctor_results(namespace: str, runner: Runner, repo_root: Path | None) -> list[CheckResult]:
    """Return prerequisite and readiness checks for the local machine."""

    results: list[CheckResult] = [
        CheckResult(
            Level.PASS, "Python", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
    ]

    if repo_root is None:
        results.append(
            CheckResult(
                Level.FAIL,
                "Runtime resources",
                "InfraWatch runtime resources could not be found",
                "Reinstall the InfraWatch package or run from a complete source checkout.",
            )
        )
    else:
        try:
            validate_runtime_root(repo_root)
            results.append(CheckResult(Level.PASS, "Runtime resources", str(repo_root)))
        except ResourceError as exc:
            results.append(CheckResult(Level.FAIL, "Runtime resources", str(exc)))

    _check_docker(results, runner)
    _check_kubectl(results, runner, namespace)
    _check_minikube(results, runner)
    _check_terraform(results, runner, repo_root)
    _check_kustomize(results, runner, repo_root)

    return results


def status_results(namespace: str, runner: Runner) -> list[CheckResult]:
    """Return concise runtime state for the current InfraWatch environment."""

    results: list[CheckResult] = []
    if not command_exists("kubectl"):
        return [
            CheckResult(
                Level.FAIL,
                "kubectl",
                "kubectl is not installed or not on PATH",
                "Install kubectl before checking InfraWatch runtime status.",
            )
        ]

    context = runner.run(["kubectl", "config", "current-context"])
    if context.returncode == 0 and context.stdout:
        results.append(CheckResult(Level.PASS, "Kubernetes context", context.stdout))
    else:
        results.append(CheckResult(Level.FAIL, "Kubernetes context", "could not read current context"))

    cluster = runner.run(["kubectl", "cluster-info", f"--request-timeout={REQUEST_TIMEOUT}"], timeout=10)
    if cluster.returncode != 0:
        results.append(
            CheckResult(
                Level.FAIL,
                "Kubernetes API",
                "not reachable",
                "Start Minikube or switch kubectl to a reachable local cluster.",
            )
        )
        return results
    results.append(CheckResult(Level.PASS, "Kubernetes API", "reachable"))

    _append_minikube_status(results, runner)

    namespace_check = runner.run(["kubectl", "get", "namespace", namespace, "-o", "json"], timeout=10)
    if namespace_check.returncode != 0:
        results.append(
            CheckResult(
                Level.FAIL,
                "InfraWatch namespace",
                f"namespace '{namespace}' does not exist",
                "Run the Terraform foundation and Kubernetes deployment flow first.",
            )
        )
        return results
    results.append(CheckResult(Level.PASS, "InfraWatch namespace", namespace))

    for name, kind, display, required in (
        ("infrawatch-backend", "deployment", "Backend", True),
        ("infrawatch-frontend", "deployment", "Frontend", True),
        ("infrawatch-postgres", "statefulset", "PostgreSQL", True),
        ("infrawatch-redis", "deployment", "Redis", True),
        ("infrawatch-prometheus", "deployment", "Prometheus", False),
        ("infrawatch-grafana", "deployment", "Grafana", False),
        ("infrawatch-loki", "statefulset", "Loki", False),
        ("infrawatch-alertmanager", "deployment", "Alertmanager", False),
    ):
        results.append(_workload_result(runner, namespace, kind, name, display, required=required))

    problem_pods = _problem_pods(runner, namespace)
    if problem_pods:
        results.append(CheckResult(Level.FAIL, "Pods", problem_pods))
    else:
        results.append(CheckResult(Level.PASS, "Pods", "no obvious CrashLoop/ImagePull/Pending/OOMKilled pods"))

    return results


def _check_docker(results: list[CheckResult], runner: Runner) -> None:
    if not command_exists("docker"):
        results.append(
            CheckResult(Level.FAIL, "Docker CLI", "not installed", "Install Docker Desktop or Docker Engine.")
        )
        return

    version = runner.run(["docker", "--version"])
    results.append(CheckResult(Level.PASS, "Docker CLI", version.stdout or "installed"))

    daemon = runner.run(["docker", "info"], timeout=10)
    if daemon.returncode == 0:
        results.append(CheckResult(Level.PASS, "Docker daemon", "reachable"))
    else:
        results.append(CheckResult(Level.FAIL, "Docker daemon", "not reachable", "Start Docker Desktop and try again."))

    compose = runner.run(["docker", "compose", "version"], timeout=10)
    if compose.returncode == 0:
        results.append(CheckResult(Level.PASS, "Docker Compose", compose.stdout or "available"))
    else:
        results.append(CheckResult(Level.FAIL, "Docker Compose", "plugin is not available"))


def _check_kubectl(results: list[CheckResult], runner: Runner, namespace: str) -> None:
    if not command_exists("kubectl"):
        results.append(CheckResult(Level.FAIL, "kubectl", "not installed", "Install kubectl."))
        return

    client = runner.run(["kubectl", "version", "--client"], timeout=10)
    if client.returncode == 0:
        results.append(CheckResult(Level.PASS, "kubectl", "client available"))
    else:
        results.append(CheckResult(Level.FAIL, "kubectl", "client check failed"))

    context = runner.run(["kubectl", "config", "current-context"], timeout=10)
    if context.returncode == 0 and context.stdout:
        results.append(CheckResult(Level.PASS, "Kubernetes context", context.stdout))
    else:
        results.append(CheckResult(Level.WARN, "Kubernetes context", "no current context"))

    cluster = runner.run(["kubectl", "cluster-info", f"--request-timeout={REQUEST_TIMEOUT}"], timeout=10)
    if cluster.returncode == 0:
        results.append(CheckResult(Level.PASS, "Kubernetes API", "reachable"))
        namespace_check = runner.run(["kubectl", "get", "namespace", namespace], timeout=10)
        if namespace_check.returncode == 0:
            results.append(CheckResult(Level.PASS, "InfraWatch namespace", namespace))
        else:
            results.append(
                CheckResult(
                    Level.WARN,
                    "InfraWatch namespace",
                    f"namespace '{namespace}' is not present",
                    "Run the local Kubernetes deployment flow when you are ready.",
                )
            )
    else:
        results.append(
            CheckResult(
                Level.WARN,
                "Kubernetes API",
                "not reachable",
                "Start Minikube or switch kubectl context before deploying InfraWatch.",
            )
        )


def _check_minikube(results: list[CheckResult], runner: Runner) -> None:
    if not command_exists("minikube"):
        results.append(
            CheckResult(Level.WARN, "Minikube", "not installed", "Install Minikube for the documented local K8s path.")
        )
        return
    version = runner.run(["minikube", "version"], timeout=10)
    if version.returncode == 0:
        results.append(
            CheckResult(Level.PASS, "Minikube", version.stdout.splitlines()[0] if version.stdout else "installed")
        )
    else:
        results.append(CheckResult(Level.WARN, "Minikube", "installed but version check failed"))
    _append_minikube_status(results, runner)


def _append_minikube_status(results: list[CheckResult], runner: Runner) -> None:
    if not command_exists("minikube"):
        return
    status = runner.run(["minikube", "status", "--format={{.Host}}/{{.APIServer}}"], timeout=10)
    if status.returncode == 0 and status.stdout:
        level = Level.PASS if status.stdout == "Running/Running" else Level.WARN
        results.append(CheckResult(level, "Minikube status", status.stdout))
    else:
        results.append(CheckResult(Level.WARN, "Minikube status", "not running or unavailable"))


def _check_terraform(results: list[CheckResult], runner: Runner, repo_root: Path | None) -> None:
    if not command_exists("terraform"):
        results.append(CheckResult(Level.FAIL, "Terraform", "not installed", "Install Terraform 1.6+."))
        return
    version = runner.run(["terraform", "version"], timeout=10)
    results.append(
        CheckResult(Level.PASS, "Terraform", version.stdout.splitlines()[0] if version.stdout else "available")
    )
    if repo_root is None:
        return
    terraform_dir = repo_root / "terraform"
    if not terraform_dir.exists():
        results.append(CheckResult(Level.FAIL, "Terraform config", "terraform/ directory is missing"))
        return
    if not (terraform_dir / ".terraform").exists():
        results.append(
            CheckResult(
                Level.WARN,
                "Terraform config",
                "providers are not initialized",
                "Run terraform -chdir=terraform init before validate/apply.",
            )
        )
        return
    validate = runner.run(["terraform", "-chdir=terraform", "validate"], cwd=repo_root, timeout=30)
    if validate.returncode == 0:
        results.append(CheckResult(Level.PASS, "Terraform config", "valid"))
    else:
        results.append(CheckResult(Level.FAIL, "Terraform config", "validation failed"))


def _check_kustomize(results: list[CheckResult], runner: Runner, repo_root: Path | None) -> None:
    if not command_exists("kubectl"):
        return
    if repo_root is None:
        return
    if not (repo_root / "k8s" / "kustomization.yaml").exists():
        results.append(CheckResult(Level.FAIL, "Kustomize", "k8s/kustomization.yaml is missing"))
        return
    rendered = runner.run(["kubectl", "kustomize", "k8s"], cwd=repo_root, timeout=20)
    if rendered.returncode == 0:
        results.append(CheckResult(Level.PASS, "Kustomize", "k8s manifests render"))
    else:
        results.append(CheckResult(Level.FAIL, "Kustomize", "k8s manifests do not render"))


def _workload_result(
    runner: Runner,
    namespace: str,
    kind: str,
    name: str,
    display: str,
    *,
    required: bool,
) -> CheckResult:
    raw = runner.run(["kubectl", "get", kind, name, "--namespace", namespace, "-o", "json"], timeout=10)
    missing_level = Level.FAIL if required else Level.WARN
    if raw.returncode != 0:
        hint = (
            "Run scripts/start-k8s.ps1 or the documented Kubernetes setup."
            if required
            else "Run scripts/start-observability.ps1 if you need this component."
        )
        return CheckResult(missing_level, display, f"{kind}/{name} not found", hint)

    try:
        payload = json.loads(raw.stdout)
    except json.JSONDecodeError:
        return CheckResult(Level.FAIL, display, f"could not parse {kind}/{name} status")

    desired = int(payload.get("spec", {}).get("replicas", 1) or 0)
    status = payload.get("status", {})
    ready = int(status.get("readyReplicas", 0) or 0)
    available = int(status.get("availableReplicas", ready) or 0)
    if ready >= desired and available >= desired:
        return CheckResult(Level.PASS, display, f"ready ({ready}/{desired})")
    level = Level.FAIL if required else Level.WARN
    return CheckResult(level, display, f"not ready (ready={ready}/{desired}, available={available}/{desired})")


def _problem_pods(runner: Runner, namespace: str) -> str:
    raw = runner.run(["kubectl", "get", "pods", "--namespace", namespace, "-o", "json"], timeout=10)
    if raw.returncode != 0:
        return "could not inspect pods"
    try:
        payload = json.loads(raw.stdout)
    except json.JSONDecodeError:
        return "could not parse pod status"

    problems: list[str] = []
    for pod in payload.get("items", []):
        name = pod.get("metadata", {}).get("name", "unknown")
        phase = pod.get("status", {}).get("phase", "")
        if phase == "Pending":
            problems.append(f"{name}=Pending")
            continue
        for container_status in pod.get("status", {}).get("containerStatuses", []):
            waiting = container_status.get("state", {}).get("waiting")
            terminated = container_status.get("lastState", {}).get("terminated")
            if waiting and waiting.get("reason") in {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull"}:
                problems.append(f"{name}={waiting.get('reason')}")
            elif terminated and terminated.get("reason") == "OOMKilled":
                problems.append(f"{name}=OOMKilled")
    return ", ".join(problems)
