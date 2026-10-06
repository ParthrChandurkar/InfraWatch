"""Safe local Kubernetes shutdown orchestration for the InfraWatch CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .checks import CommandResult, Runner, SubprocessRunner, command_exists
from .runtime_resources import ResourceError, resolve_runtime_root, validate_runtime_root
from .start import DEFAULT_NAMESPACE, FoundationResource, Starter, Step


@dataclass(frozen=True)
class StopOptions:
    """User-configurable stop options."""

    namespace: str = DEFAULT_NAMESPACE
    remove_observability: bool = False
    timeout_seconds: int = 180


class StopError(RuntimeError):
    """Normal user-facing stop failure."""

    def __init__(self, step: str, message: str, hint: str = "") -> None:
        self.step = step
        self.hint = hint
        super().__init__(message)


class Stopper:
    """Remove InfraWatch app runtime resources without touching the Terraform foundation."""

    OBSERVABILITY_HELM_RELEASES = ("infrawatch-alloy", "infrawatch-loki")
    OBSERVABILITY_GENERATED_OBJECTS = (
        "configmap/infrawatch-prometheus-config",
        "configmap/infrawatch-alertmanager-config",
        "configmap/infrawatch-grafana-datasources",
        "configmap/infrawatch-grafana-dashboard-provider",
        "configmap/infrawatch-grafana-dashboards",
        "secret/infrawatch-grafana-admin",
    )

    def __init__(self, runner: Runner | None = None, repo_root: Path | None = None) -> None:
        self.runner = runner or SubprocessRunner()
        try:
            self.repo_root = repo_root if repo_root is not None else resolve_runtime_root(prefer_repo=True)
        except ResourceError:
            self.repo_root = None
        self.steps: list[Step] = []

    def stop(self, options: StopOptions) -> int:
        """Run the stop workflow and print a concise report."""

        print("InfraWatch Stop\n")
        try:
            self._stop(options)
        except StopError as exc:
            self._record(False, exc.step, str(exc), exc.hint)
            self._print_steps()
            return 1

        self._print_steps()
        print("\nInfraWatch application resources are stopped.")
        print("Terraform foundation, Terraform state, and the Minikube cluster were preserved.")
        return 0

    def _stop(self, options: StopOptions) -> None:
        self._validate_runtime_resources()
        self._check_prerequisites()
        self._check_kubernetes_api()
        self._check_minikube()
        self._verify_terraform_state(options.namespace, "Terraform state before stop")

        namespace = self.runner.run(["kubectl", "get", "namespace", options.namespace], cwd=self.repo_root, timeout=20)
        if namespace.returncode != 0:
            self._record(True, "InfraWatch namespace", f"{options.namespace} is absent; nothing to delete")
            self._verify_terraform_state(options.namespace, "Terraform state after stop")
            return
        self._record(True, "InfraWatch namespace", f"{options.namespace} exists")

        if options.remove_observability:
            self._remove_observability(options.namespace)
        else:
            self._record(
                True,
                "Observability stack",
                "preserved",
                "Use --remove-observability if you intentionally want to uninstall Prometheus/Grafana/Loki/Alloy.",
            )

        self._delete_application_resources(options)
        self._wait_for_application_pods(options)
        self._verify_terraform_state(options.namespace, "Terraform state after stop")
        self._verify_terraform_plan()
        self._check_minikube(after_stop=True)

    def _validate_runtime_resources(self) -> None:
        if self.repo_root is None:
            raise StopError(
                "Runtime resources",
                "InfraWatch runtime resources could not be found",
                "Run from a source checkout or reinstall the InfraWatch package.",
            )
        try:
            validate_runtime_root(self.repo_root)
        except ResourceError as exc:
            raise StopError("Runtime resources", str(exc)) from exc
        self._record(True, "Runtime resources", str(self.repo_root))

    def _check_prerequisites(self) -> None:
        for command in ("kubectl", "terraform"):
            if not command_exists(command):
                raise StopError(command, f"{command} was not found on PATH")
            self._record(True, command, "available")
        if command_exists("minikube"):
            self._record(True, "minikube", "available")
        else:
            self._record(
                True,
                "minikube",
                "not found; continuing with kubectl safety checks",
                "Install minikube if you want the CLI to verify the local cluster process.",
            )

    def _check_kubernetes_api(self) -> None:
        context = self.runner.run(["kubectl", "config", "current-context"], cwd=self.repo_root, timeout=10)
        if context.returncode == 0 and context.stdout:
            self._record(True, "Kubernetes context", context.stdout.strip())
        else:
            raise StopError(
                "Kubernetes context",
                "could not read current kubectl context",
                self._command_output(context),
            )

        cluster = self.runner.run(
            ["kubectl", "cluster-info", "--request-timeout=10s"], cwd=self.repo_root, timeout=15
        )
        if cluster.returncode != 0:
            raise StopError(
                "Kubernetes API",
                "not reachable; no resources were deleted",
                self._command_output(cluster),
            )
        self._record(True, "Kubernetes API", "reachable")

    def _check_minikube(self, *, after_stop: bool = False) -> None:
        if not command_exists("minikube"):
            return
        result = self.runner.run(
            ["minikube", "status", "--format={{.Host}}/{{.APIServer}}"], cwd=self.repo_root, timeout=20
        )
        step_name = "Minikube after stop" if after_stop else "Minikube"
        if result.returncode == 0 and "Running/Running" in result.stdout:
            self._record(True, step_name, "running")
            return
        self._record(
            True,
            step_name,
            "status could not be verified",
            self._command_output(result),
        )

    def _delete_application_resources(self, options: StopOptions) -> None:
        deleted = self.runner.run(
            ["kubectl", "delete", "-k", "k8s", "--ignore-not-found=true"],
            cwd=self.repo_root,
            timeout=options.timeout_seconds,
        )
        if deleted.returncode != 0:
            raise StopError(
                "Kustomize delete",
                "could not delete InfraWatch app resources",
                self._command_output(deleted),
            )
        self._record(
            True,
            "Kustomize delete",
            "removed app-owned Deployments, Services, StatefulSet, HPA, and app ConfigMaps",
        )

        self._record(
            True,
            "Runtime secret",
            "preserved infrawatch-secrets",
            "The PostgreSQL PVC is preserved, so the database password Secret must be preserved too.",
        )

    def _wait_for_application_pods(self, options: StopOptions) -> None:
        result = self.runner.run(
            [
                "kubectl",
                "wait",
                "--for=delete",
                "pod",
                "-l",
                "app.kubernetes.io/part-of=infrawatch",
                "--namespace",
                options.namespace,
                f"--timeout={options.timeout_seconds}s",
            ],
            cwd=self.repo_root,
            timeout=options.timeout_seconds + 15,
        )
        if result.returncode == 0:
            self._record(True, "Application pods", "deleted")
            return
        output = self._command_output(result)
        if "no matching resources" in output.lower() or "not found" in output.lower():
            self._record(True, "Application pods", "already absent")
            return
        raise StopError("Application pods", "pods did not finish deleting", output)

    def _remove_observability(self, namespace: str) -> None:
        if command_exists("helm"):
            for release in self.OBSERVABILITY_HELM_RELEASES:
                status = self.runner.run(["helm", "status", release, "--namespace", namespace], cwd=self.repo_root)
                if status.returncode != 0:
                    self._record(True, f"Helm {release}", "absent")
                    continue
                removed = self.runner.run(
                    ["helm", "uninstall", release, "--namespace", namespace, "--timeout", "5m"],
                    cwd=self.repo_root,
                    timeout=360,
                )
                if removed.returncode != 0:
                    raise StopError(
                        f"Helm {release}",
                        "could not uninstall observability release",
                        self._command_output(removed),
                    )
                self._record(True, f"Helm {release}", "uninstalled")
        else:
            self._record(
                True,
                "Helm",
                "not found; skipped Helm-owned observability releases",
                "Install Helm and rerun with --remove-observability if Loki/Alloy releases remain.",
            )

        deleted = self.runner.run(
            ["kubectl", "delete", "-k", "k8s/observability", "--ignore-not-found=true"],
            cwd=self.repo_root,
            timeout=180,
        )
        if deleted.returncode != 0:
            raise StopError(
                "Observability manifests",
                "could not delete observability manifests",
                self._command_output(deleted),
            )
        self._record(True, "Observability manifests", "removed if present")

        for resource in self.OBSERVABILITY_GENERATED_OBJECTS:
            result = self.runner.run(
                [
                    "kubectl",
                    "delete",
                    resource,
                    "--namespace",
                    namespace,
                    "--ignore-not-found=true",
                ],
                cwd=self.repo_root,
                timeout=30,
            )
            if result.returncode != 0:
                raise StopError(
                    "Observability generated objects",
                    f"could not delete {resource}",
                    self._command_output(result),
                )
        self._record(True, "Observability generated objects", "removed if present")

    def _verify_terraform_state(self, namespace: str, step_name: str) -> None:
        expected = {resource.address for resource in self._foundation_resources(namespace)}
        tracked = self._terraform_state_addresses()
        missing = sorted(expected - tracked)
        if missing:
            raise StopError(
                step_name,
                "Terraform foundation state is incomplete; no resources were deleted",
                "Missing state addresses: " + ", ".join(missing),
            )
        self._record(True, step_name, f"{len(expected)} foundation resources tracked")

    def _verify_terraform_plan(self) -> None:
        result = self._run_terraform(["plan", "-detailed-exitcode", "-var=kube_context=minikube"], timeout=300)
        if result.returncode == 0:
            self._record(True, "Terraform plan after stop", "no foundation changes required")
            return
        if result.returncode == 2:
            raise StopError(
                "Terraform plan after stop",
                "Terraform foundation drift detected after stop",
                self._command_output(result),
            )
        raise StopError("Terraform plan after stop", "Terraform plan failed", self._command_output(result))

    def _foundation_resources(self, namespace: str) -> tuple[FoundationResource, ...]:
        if namespace == DEFAULT_NAMESPACE:
            return Starter.FOUNDATION_RESOURCES
        return tuple(
            FoundationResource(
                resource.address,
                resource.import_id.replace(DEFAULT_NAMESPACE, namespace),
                [part.replace(DEFAULT_NAMESPACE, namespace) for part in resource.kubectl_args],
            )
            for resource in Starter.FOUNDATION_RESOURCES
        )

    def _terraform_state_addresses(self) -> set[str]:
        result = self._run_terraform(["state", "list"], timeout=30)
        if result.returncode != 0:
            raise StopError("Terraform state", "could not list Terraform state", self._command_output(result))
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}

    def _run_terraform(self, args: list[str], *, timeout: int) -> CommandResult:
        return self.runner.run(["terraform", "-chdir=terraform", *args], cwd=self.repo_root, timeout=timeout)

    def _record(self, ok: bool, name: str, message: str, hint: str = "") -> None:
        self.steps.append(Step(ok, name, message, hint))

    def _print_steps(self) -> None:
        for step in self.steps:
            label = "PASS" if step.ok else "FAIL"
            print(f"[{label}] {step.name}: {step.message}")
            if step.hint:
                print(f"       Hint: {step.hint}")

    def _command_output(self, result: CommandResult, *, limit: int = 2000) -> str:
        output = (result.stderr or result.stdout or "").strip()
        if not output:
            return f"exit code {result.returncode}"
        lines = []
        for line in output.splitlines():
            stripped = line.strip()
            if stripped and not all(char in "╷╵│─┌┐└┘┬┴├┤┼" for char in stripped):
                lines.append(stripped)
        clean_output = "\n".join(lines[-12:]).strip() or output
        return clean_output[:limit]


def run_stop(options: StopOptions, runner: Runner | None = None, repo_root: Path | None = None) -> int:
    """Run the public stop command."""

    return Stopper(runner=runner, repo_root=repo_root).stop(options)
