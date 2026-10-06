"""Command-line interface for InfraWatch."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from . import __version__
from .checks import run_doctor, run_status
from .start import StartOptions, run_start
from .stop import StopOptions, run_stop

DESCRIPTION = """InfraWatch local Kubernetes deployment and observability CLI.

This CLI exposes local machine diagnostics, local Kubernetes startup, and
runtime status for InfraWatch.
"""


def build_parser() -> argparse.ArgumentParser:
    """Create the InfraWatch CLI argument parser."""

    parser = argparse.ArgumentParser(
        prog="infrawatch",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"InfraWatch {__version__}",
        help="Show the InfraWatch CLI version and exit.",
    )

    subcommands = parser.add_subparsers(dest="command", metavar="command")
    start = subcommands.add_parser(
        "start",
        help="Start InfraWatch on local Kubernetes with Minikube.",
        description="Start/reconcile the local Minikube, Terraform, Kustomize, and InfraWatch runtime workflow.",
    )
    start.add_argument("--namespace", default="infrawatch", help="Kubernetes namespace to manage.")
    start.add_argument("--image-repository", default="parthchn178", help="DockerHub namespace for InfraWatch images.")
    start.add_argument("--minikube-memory-mb", type=int, default=3072, help="Memory for minikube start when needed.")
    start.add_argument("--minikube-cpus", type=int, default=2, help="CPU count for minikube start when needed.")
    start.add_argument(
        "--timeout-seconds", type=int, default=240, help="Rollout timeout for backend/frontend readiness."
    )
    start.add_argument(
        "--strict-observability",
        action="store_true",
        help="Disable mock observability fallback after startup.",
    )
    start.add_argument(
        "--install-observability",
        action="store_true",
        help="Also run the existing observability installer when supported.",
    )
    start.set_defaults(handler=_start_command)

    stop = subcommands.add_parser(
        "stop",
        help="Stop InfraWatch app resources while preserving Minikube and Terraform foundation state.",
        description=(
            "Stop InfraWatch app resources while preserving Minikube, Terraform state, and Terraform-owned "
            "foundation resources."
        ),
    )
    stop.add_argument("--namespace", default="infrawatch", help="Kubernetes namespace to manage.")
    stop.add_argument(
        "--remove-observability",
        action="store_true",
        help="Also uninstall InfraWatch observability workloads. By default they are preserved.",
    )
    stop.add_argument("--timeout-seconds", type=int, default=180, help="Timeout for Kubernetes deletion waits.")
    stop.set_defaults(handler=_stop_command)
    doctor = subcommands.add_parser(
        "doctor",
        help="Check whether the local machine is ready to run InfraWatch.",
        description="Check local prerequisites, tooling, and repository configuration.",
    )
    doctor.add_argument("--namespace", default="infrawatch", help="Kubernetes namespace to inspect.")
    doctor.set_defaults(handler=_doctor_command)

    status = subcommands.add_parser(
        "status",
        help="Show the current state of running InfraWatch components.",
        description="Inspect the current Kubernetes runtime state for InfraWatch.",
    )
    status.add_argument("--namespace", default="infrawatch", help="Kubernetes namespace to inspect.")
    status.set_defaults(handler=_status_command)

    return parser


def _start_command(args: argparse.Namespace) -> int:
    """Run local Kubernetes startup."""

    return run_start(
        StartOptions(
            namespace=args.namespace,
            image_repository=args.image_repository,
            minikube_memory_mb=args.minikube_memory_mb,
            minikube_cpus=args.minikube_cpus,
            strict_observability=args.strict_observability,
            skip_observability=not args.install_observability,
            timeout_seconds=args.timeout_seconds,
        )
    )


def _stop_command(args: argparse.Namespace) -> int:
    """Run safe local Kubernetes shutdown."""

    return run_stop(
        StopOptions(
            namespace=args.namespace,
            remove_observability=args.remove_observability,
            timeout_seconds=args.timeout_seconds,
        )
    )


def _doctor_command(args: argparse.Namespace) -> int:
    """Run environment readiness checks."""

    return run_doctor(namespace=args.namespace)


def _status_command(args: argparse.Namespace) -> int:
    """Run current runtime status checks."""

    return run_status(namespace=args.namespace)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the InfraWatch CLI."""

    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)
