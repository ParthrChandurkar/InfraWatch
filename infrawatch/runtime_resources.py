"""Runtime resource discovery for source and installed InfraWatch CLIs."""

from __future__ import annotations

import os
import shutil
from importlib import resources
from pathlib import Path

from . import __version__
from .orchestration import find_repo_root

RESOURCE_DIR_NAMES = ("terraform", "k8s", "monitoring", "logging", "scripts")
RESOURCE_PACKAGE = "infrawatch.resources"


class ResourceError(RuntimeError):
    """Raised when packaged runtime resources cannot be resolved."""


def user_cache_root() -> Path:
    """Return the InfraWatch CLI cache root without needing extra dependencies."""

    override = os.environ.get("INFRAWATCH_RESOURCE_CACHE")
    if override:
        return Path(override).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "InfraWatch" / "resources"
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        return Path(xdg_cache) / "infrawatch" / "resources"
    return Path.home() / ".cache" / "infrawatch" / "resources"


def resolve_runtime_root(*, prefer_repo: bool = True) -> Path:
    """Return a filesystem root containing Terraform/Kustomize runtime resources."""

    if prefer_repo:
        repo_root = find_repo_root()
        if repo_root is not None:
            return repo_root
    return materialize_package_resources()


def materialize_package_resources(destination: Path | None = None) -> Path:
    """Copy packaged runtime resources to a deterministic writable directory."""

    target = destination or user_cache_root() / __version__
    target.mkdir(parents=True, exist_ok=True)
    package_root = resources.files(RESOURCE_PACKAGE)
    for name in RESOURCE_DIR_NAMES:
        source = package_root / name
        if not source.is_dir():
            raise ResourceError(f"Packaged resource directory is missing: {name}")
        _copy_tree(source, target / name)
    marker = target / ".infrawatch-resource-version"
    marker.write_text(__version__, encoding="utf-8")
    return target


def validate_runtime_root(root: Path) -> None:
    """Ensure a runtime root contains the directories needed by the CLI."""

    required = (
        root / "terraform" / "main.tf",
        root / "terraform" / "variables.tf",
        root / "k8s" / "kustomization.yaml",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise ResourceError("Missing InfraWatch runtime resources: " + ", ".join(missing))


def _copy_tree(source, destination: Path) -> None:
    """Copy an importlib resource tree to the filesystem."""

    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        child_destination = destination / child.name
        if child.is_dir():
            _copy_tree(child, child_destination)
        else:
            with resources.as_file(child) as child_file:
                if not _same_file(child_file, child_destination):
                    shutil.copy2(child_file, child_destination)


def _same_file(source: Path, destination: Path) -> bool:
    """Return True when destination appears to already contain the same file."""

    if not destination.exists():
        return False
    try:
        return destination.stat().st_size == source.stat().st_size
    except OSError:
        return False
