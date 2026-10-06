"""Local Kubernetes startup orchestration for the InfraWatch CLI."""

from __future__ import annotations

import json
import secrets
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .checks import CommandResult, Level, Runner, SubprocessRunner, command_exists, status_results
from .orchestration import platform_label
from .runtime_resources import ResourceError, resolve_runtime_root, validate_runtime_root

DEFAULT_IMAGE_REPOSITORY = "parthchn178"
DEFAULT_NAMESPACE = "infrawatch"


@dataclass(frozen=True)
class StartOptions:
    """User-configurable startup options."""

    namespace: str = DEFAULT_NAMESPACE
    image_repository: str = DEFAULT_IMAGE_REPOSITORY
    minikube_memory_mb: int = 3072
    minikube_cpus: int = 2
    strict_observability: bool = False
    skip_observability: bool = True
    timeout_seconds: int = 240


@dataclass(frozen=True)
class Step:
    """One startup progress line."""

    ok: bool
    name: str
    message: str
    hint: str = ""


@dataclass(frozen=True)
class FoundationResource:
    """One Terraform-owned InfraWatch foundation resource."""

    address: str
    import_id: str
    kubectl_args: list[str]


class StartError(RuntimeError):
    """Normal user-facing startup failure."""

    def __init__(self, step: str, message: str, hint: str = "") -> None:
        self.step = step
        self.hint = hint
        super().__init__(message)


class Starter:
    """Coordinate the local Kubernetes startup flow."""

    FOUNDATION_RESOURCES = (
        FoundationResource(
            "kubernetes_namespace_v1.infrawatch", DEFAULT_NAMESPACE, ["get", "namespace", DEFAULT_NAMESPACE]
        ),
        FoundationResource(
            "kubernetes_service_account_v1.backend",
            f"{DEFAULT_NAMESPACE}/infrawatch-backend",
            ["get", "serviceaccount", "infrawatch-backend", "--namespace", DEFAULT_NAMESPACE],
        ),
        FoundationResource(
            "kubernetes_cluster_role_v1.local_reader",
            "infrawatch-local-reader",
            ["get", "clusterrole", "infrawatch-local-reader"],
        ),
        FoundationResource(
            "kubernetes_cluster_role_binding_v1.local_reader",
            "infrawatch-local-reader",
            ["get", "clusterrolebinding", "infrawatch-local-reader"],
        ),
        FoundationResource(
            "kubernetes_role_v1.deployer",
            f"{DEFAULT_NAMESPACE}/infrawatch-deployer",
            ["get", "role", "infrawatch-deployer", "--namespace", DEFAULT_NAMESPACE],
        ),
        FoundationResource(
            "kubernetes_role_binding_v1.deployer",
            f"{DEFAULT_NAMESPACE}/infrawatch-deployer",
            ["get", "rolebinding", "infrawatch-deployer", "--namespace", DEFAULT_NAMESPACE],
        ),
        FoundationResource(
            "kubernetes_resource_quota_v1.infrawatch",
            f"{DEFAULT_NAMESPACE}/infrawatch-quota",
            ["get", "resourcequota", "infrawatch-quota", "--namespace", DEFAULT_NAMESPACE],
        ),
        FoundationResource(
            "kubernetes_config_map_v1.backend_config",
            f"{DEFAULT_NAMESPACE}/infrawatch-backend-config",
            ["get", "configmap", "infrawatch-backend-config", "--namespace", DEFAULT_NAMESPACE],
        ),
    )

    def __init__(self, runner: Runner | None = None, repo_root: Path | None = None) -> None:
        self.runner = runner or SubprocessRunner()
        try:
            self.repo_root = repo_root if repo_root is not None else resolve_runtime_root(prefer_repo=True)
        except ResourceError:
            self.repo_root = None
        self.steps: list[Step] = []

    def start(self, options: StartOptions) -> int:
        """Run the startup workflow and print a concise report."""

        print("InfraWatch Start\n")
        try:
            dashboard_url = self._start(options)
        except StartError as exc:
            self._record(False, exc.step, str(exc), exc.hint)
            self._print_steps()
            print("\nResult: Start failed")
            return 1

        self._print_steps()
        print("\nInfraWatch is running.")
        if dashboard_url:
            print(f"Dashboard: {dashboard_url}")
        else:
            print("Dashboard: run " f"'minikube service infrawatch-frontend --namespace {options.namespace} --url'")
        print("Backend API: use 'kubectl port-forward svc/infrawatch-backend --namespace infrawatch 8000:8000'")
        return 0

    def _start(self, options: StartOptions) -> str:
        if self.repo_root is None:
            raise StartError(
                "Runtime resources",
                "InfraWatch runtime resources could not be resolved",
                "Reinstall InfraWatch or run from a complete source checkout.",
            )
        try:
            validate_runtime_root(self.repo_root)
        except ResourceError as exc:
            raise StartError("Runtime resources", str(exc)) from exc
        self._record(True, "Runtime resources", str(self.repo_root))

        self._require_command("docker", "Install Docker Desktop or Docker Engine.")
        self._require_command("kubectl", "Install kubectl.")
        self._require_command("minikube", "Install Minikube.")
        self._require_command("terraform", "Install Terraform 1.6+.")

        self._run_required(["docker", "info"], "Docker daemon", "Docker daemon reachable", timeout=15)
        self._ensure_minikube(options)
        self._run_required(["kubectl", "config", "use-context", "minikube"], "Kubernetes context", "Using minikube")
        self._run_required(
            ["kubectl", "cluster-info", "--request-timeout=10s"],
            "Kubernetes API",
            "reachable",
            timeout=15,
        )
        self._enable_metrics_server()
        self._apply_terraform(options)
        self._ensure_postgres_secret(options.namespace)
        self._apply_kustomize(options)
        self._configure_observability_mode(options)
        self._set_images(options)
        self._wait_for_core_rollouts(options)
        self._maybe_start_observability(options)
        self._validate_status(options)
        return self._dashboard_url(options.namespace)

    def _require_command(self, command: str, hint: str) -> None:
        if not command_exists(command):
            raise StartError(command, f"{command} was not found on PATH", hint)
        self._record(True, command, "available")

    def _ensure_minikube(self, options: StartOptions) -> None:
        current_context = self.runner.run(["kubectl", "config", "current-context"], timeout=10)
        cluster = self.runner.run(["kubectl", "cluster-info", "--request-timeout=10s"], timeout=15)
        if current_context.returncode == 0 and current_context.stdout == "minikube" and cluster.returncode == 0:
            self._record(True, "Minikube", "current minikube context is already reachable")
            return

        status = self.runner.run(["minikube", "status", "--format={{.Host}}/{{.APIServer}}"], timeout=15)
        if status.returncode == 0 and status.stdout == "Running/Running":
            self._record(True, "Minikube", "already running")
            return

        result = self.runner.run(
            [
                "minikube",
                "start",
                f"--memory={options.minikube_memory_mb}",
                f"--cpus={options.minikube_cpus}",
            ],
            timeout=600,
        )
        if result.returncode != 0:
            raise StartError("Minikube", "failed to start Minikube", self._short_error(result))
        self._record(True, "Minikube", "started")

    def _enable_metrics_server(self) -> None:
        result = self.runner.run(["minikube", "addons", "enable", "metrics-server"], timeout=120)
        if result.returncode == 0:
            self._record(True, "metrics-server", "enabled")
        else:
            self._record(
                True,
                "metrics-server",
                "could not be enabled automatically; continuing",
                "kubectl top/HPA metrics may be delayed.",
            )

    def _apply_terraform(self, options: StartOptions) -> None:
        terraform_dir = self.repo_root / "terraform" if self.repo_root else Path("terraform")
        if not terraform_dir.exists():
            raise StartError("Terraform", "terraform/ directory is missing")

        self._run_terraform_required(["init"], "Terraform init", "completed", timeout=180)
        self._run_terraform_required(["fmt", "-check"], "Terraform fmt", "formatted", timeout=60)
        self._run_terraform_required(["validate"], "Terraform validate", "valid", timeout=60)
        self._import_existing_foundation(options.namespace)
        self._verify_terraform_plan()
        self._run_terraform_required(
            ["apply", "-auto-approve", "-var=kube_context=minikube"],
            "Terraform apply",
            "foundation applied",
            timeout=600,
        )

    def _import_existing_foundation(self, namespace: str) -> None:
        resources = self._foundation_resources(namespace)
        state_addresses = self._terraform_state_addresses()
        tracked_addresses = {resource.address for resource in resources if resource.address in state_addresses}
        existing_addresses: set[str] = set()
        imported_addresses: list[str] = []

        for resource in resources:
            if resource.address in state_addresses:
                continue
            if self.runner.run(["kubectl", *resource.kubectl_args], cwd=self.repo_root, timeout=20).returncode == 0:
                existing_addresses.add(resource.address)
                imported = self._run_terraform(
                    ["import", "-var=kube_context=minikube", resource.address, resource.import_id], timeout=120
                )
                if imported.returncode != 0:
                    raise StartError(
                        "Terraform imports",
                        f"could not import {resource.address}",
                        self._command_output(imported),
                    )
                imported_addresses.append(resource.address)

        if existing_addresses:
            reconciled_state = self._terraform_state_addresses()
            missing = sorted(address for address in existing_addresses if address not in reconciled_state)
            if missing:
                raise StartError(
                    "Terraform imports",
                    "import verification failed",
                    "Missing from Terraform state: " + ", ".join(missing),
                )

        if imported_addresses:
            self._record(
                True, "Terraform imports", f"imported {len(imported_addresses)} existing foundation resource(s)"
            )
        elif existing_addresses:
            self._record(True, "Terraform imports", "existing foundation resources already reconciled")
        elif tracked_addresses:
            self._record(True, "Terraform imports", "foundation resources already tracked in Terraform state")
        else:
            self._record(True, "Terraform imports", "no existing foundation resources to import")

    def _foundation_resources(self, namespace: str) -> tuple[FoundationResource, ...]:
        if namespace == DEFAULT_NAMESPACE:
            return self.FOUNDATION_RESOURCES
        return tuple(
            FoundationResource(
                resource.address,
                resource.import_id.replace(DEFAULT_NAMESPACE, namespace),
                [part.replace(DEFAULT_NAMESPACE, namespace) for part in resource.kubectl_args],
            )
            for resource in self.FOUNDATION_RESOURCES
        )

    def _terraform_state_addresses(self) -> set[str]:
        result = self._run_terraform(["state", "list"], timeout=30)
        if result.returncode != 0:
            output = self._command_output(result)
            if "No state file" in output or "state file" in output:
                return set()
            raise StartError("Terraform state", "could not list Terraform state", output)
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}

    def _verify_terraform_plan(self) -> None:
        result = self._run_terraform(["plan", "-detailed-exitcode", "-var=kube_context=minikube"], timeout=300)
        if result.returncode == 0:
            self._record(True, "Terraform plan", "no foundation changes required")
            return
        if result.returncode == 2:
            self._record(True, "Terraform plan", "foundation changes ready to apply")
            return
        raise StartError("Terraform plan", "Terraform plan failed", self._command_output(result))

    def _run_terraform(self, args: list[str], *, timeout: int) -> CommandResult:
        return self.runner.run(["terraform", "-chdir=terraform", *args], cwd=self.repo_root, timeout=timeout)

    def _run_terraform_required(
        self,
        args: list[str],
        step: str,
        success_message: str,
        *,
        timeout: int,
    ) -> CommandResult:
        result = self._run_terraform(args, timeout=timeout)
        if result.returncode != 0:
            raise StartError(step, f"{step} failed", self._command_output(result))
        self._record(True, step, success_message)
        return result

    def _ensure_postgres_secret(self, namespace: str) -> None:
        exists = self.runner.run(
            ["kubectl", "get", "secret", "infrawatch-secrets", "--namespace", namespace], timeout=20
        )
        if exists.returncode == 0:
            self._record(True, "PostgreSQL secret", "already exists")
            return

        password = secrets.token_urlsafe(24)
        database_url = f"postgresql://infrawatch:{quote(password, safe='')}@infrawatch-postgres:5432/infrawatch"
        manifest = self.runner.run(
            [
                "kubectl",
                "create",
                "secret",
                "generic",
                "infrawatch-secrets",
                "--namespace",
                namespace,
                f"--from-literal=POSTGRES_PASSWORD={password}",
                f"--from-literal=DATABASE_URL={database_url}",
                "--dry-run=client",
                "-o",
                "yaml",
            ],
            timeout=30,
        )
        if manifest.returncode != 0:
            raise StartError("PostgreSQL secret", "could not build Kubernetes secret", self._short_error(manifest))

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".yaml", delete=False) as file:
            file.write(manifest.stdout)
            temp_path = Path(file.name)
        try:
            applied = self.runner.run(["kubectl", "apply", "-f", str(temp_path)], timeout=30)
        finally:
            temp_path.unlink(missing_ok=True)
        if applied.returncode != 0:
            raise StartError("PostgreSQL secret", "could not apply Kubernetes secret", self._short_error(applied))
        self._record(True, "PostgreSQL secret", "created")

    def _apply_kustomize(self, options: StartOptions) -> None:
        self._run_required(
            ["kubectl", "apply", "-k", "k8s"], "Kustomize apply", "application resources applied", timeout=180
        )
        allow_mock = "false" if options.strict_observability else "true"
        self._run_required(
            [
                "kubectl",
                "patch",
                "configmap",
                "infrawatch-backend-config",
                "--namespace",
                options.namespace,
                "--type",
                "merge",
                "-p",
                f'{{"data":{{"INFRAWATCH_ALLOW_MOCK_OBSERVABILITY":"{allow_mock}"}}}}',
            ],
            "Backend config",
            f"mock observability fallback={allow_mock}",
            timeout=30,
        )

    def _configure_observability_mode(self, options: StartOptions) -> None:
        if options.strict_observability:
            self._record(True, "Observability mode", "strict Prometheus/Loki mode requested")
        else:
            self._record(True, "Observability mode", "fallback enabled until observability data is present")

    def _set_images(self, options: StartOptions) -> None:
        repo = options.image_repository.strip().strip("/")
        if not repo:
            raise StartError("Images", "image repository cannot be empty")
        self._run_required(
            [
                "kubectl",
                "set",
                "image",
                "deployment/infrawatch-backend",
                f"backend=docker.io/{repo}/infrawatch-backend:latest",
                "--namespace",
                options.namespace,
            ],
            "Backend image",
            "configured",
            timeout=60,
        )
        self._run_required(
            [
                "kubectl",
                "set",
                "image",
                "deployment/infrawatch-frontend",
                f"frontend=docker.io/{repo}/infrawatch-frontend:latest",
                "--namespace",
                options.namespace,
            ],
            "Frontend image",
            "configured",
            timeout=60,
        )

    def _wait_for_core_rollouts(self, options: StartOptions) -> None:
        rollouts = [
            ("PostgreSQL", "statefulset", "infrawatch-postgres", "240s"),
            ("Redis", "deployment", "infrawatch-redis", "180s"),
            ("Backend", "deployment", "infrawatch-backend", f"{options.timeout_seconds}s"),
            ("Frontend", "deployment", "infrawatch-frontend", f"{options.timeout_seconds}s"),
        ]
        for display_name, kind, resource_name, timeout in rollouts:
            resource = f"{kind}/{resource_name}"
            result = self.runner.run(
                ["kubectl", "rollout", "status", resource, "--namespace", options.namespace, f"--timeout={timeout}"],
                cwd=self.repo_root,
                timeout=options.timeout_seconds + 30,
            )
            if result.returncode == 0:
                self._record(True, display_name, "ready")
                continue
            if self._workload_ready(kind, resource_name, options.namespace):
                self._record(
                    True,
                    display_name,
                    "ready after direct status check",
                    "kubectl rollout status returned a transient watch error.",
                )
                continue
            raise StartError(display_name, f"{display_name} failed", self._short_error(result))

    def _workload_ready(self, kind: str, name: str, namespace: str) -> bool:
        result = self.runner.run(["kubectl", "get", kind, name, "--namespace", namespace, "-o", "json"], timeout=30)
        if result.returncode != 0:
            return False
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False
        desired = int(payload.get("spec", {}).get("replicas", 1) or 0)
        status = payload.get("status", {})
        ready = int(status.get("readyReplicas", 0) or 0)
        available = int(status.get("availableReplicas", ready) or 0)
        return ready >= desired and available >= desired

    def _maybe_start_observability(self, options: StartOptions) -> None:
        if options.skip_observability:
            self._record(True, "Observability stack", "not installed by default")
            return
        if platform_label() != "windows":
            self._record(
                True,
                "Observability stack",
                "automatic startup is currently Windows-script based; skipped on this platform",
                "Use the documented observability setup for your platform.",
            )
            return
        script = self.repo_root / "scripts" / "start-observability.ps1" if self.repo_root else None
        if script is None or not script.exists():
            self._record(True, "Observability stack", "start-observability.ps1 not found; skipped")
            return
        powershell = "powershell"
        if not command_exists(powershell):
            self._record(True, "Observability stack", "PowerShell not found; skipped")
            return
        args = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Namespace",
            options.namespace,
            "-NoPortForward",
        ]
        if not options.strict_observability:
            args.append("-KeepFallback")
        result = self.runner.run(args, cwd=self.repo_root, timeout=900)
        if result.returncode != 0:
            raise StartError("Observability stack", "could not start observability stack", self._short_error(result))
        self._record(True, "Observability stack", "installed/reconciled without port-forwards")

    def _validate_status(self, options: StartOptions) -> None:
        last_failures: list[str] = []
        for attempt in range(1, 4):
            results = status_results(options.namespace, self.runner)
            failures = [result for result in results if result.level == Level.FAIL]
            if not failures:
                self._record(True, "Status validation", f"core components healthy after check {attempt}/3")
                return
            last_failures = [f"{result.name}: {result.message}" for result in failures[:3]]
            if attempt < 3:
                time.sleep(5)
        raise StartError("Status validation", "InfraWatch components are not healthy", "; ".join(last_failures))

    def _dashboard_url(self, namespace: str) -> str:
        result = self.runner.run(
            ["minikube", "service", "infrawatch-frontend", "--namespace", namespace, "--url"],
            timeout=30,
        )
        if result.returncode != 0 or not result.stdout:
            return ""
        url = result.stdout.splitlines()[0].strip()
        if self._url_reachable(url):
            self._record(True, "Dashboard URL", f"verified {url}")
            return url
        self._record(True, "Dashboard URL", f"reported by Minikube but not verified: {url}")
        return ""

    def _run_required(
        self,
        args: list[str],
        step: str,
        success_message: str,
        *,
        timeout: int = 60,
    ) -> CommandResult:
        result = self.runner.run(args, cwd=self.repo_root, timeout=timeout)
        if result.returncode != 0:
            raise StartError(step, f"{step} failed", self._short_error(result))
        self._record(True, step, success_message)
        return result

    def _record(self, ok: bool, name: str, message: str, hint: str = "") -> None:
        self.steps.append(Step(ok, name, message, hint))

    def _print_steps(self) -> None:
        for step in self.steps:
            label = "PASS" if step.ok else "FAIL"
            print(f"[{label}] {step.name}: {step.message}")
            if step.hint:
                print(f"       Hint: {step.hint}")

    def _short_error(self, result: CommandResult) -> str:
        return self._command_output(result, limit=240)

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

    def _url_reachable(self, url: str) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return 200 <= response.status < 500
        except (urllib.error.URLError, TimeoutError, ValueError):
            return False


def run_start(options: StartOptions, runner: Runner | None = None, repo_root: Path | None = None) -> int:
    """Run the public start command."""

    return Starter(runner=runner, repo_root=repo_root).start(options)
