"""Composition root for public Project Packs and optional private overlays."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from app.bridge.code_workspace_resolver import PersistedCodeWorkspaceResolver
from app.execution.adapters.base import ProjectAdapter
from app.execution.adapters.process import ProcessAdapter
from app.execution.adapters.registry import AdapterRegistry
from app.execution.remote import (
    RemoteExecutor,
    RemoteExecutorConfig,
    RemoteJobClient,
    RemoteProjectAdapter,
    load_remote_executor_config,
)
from app.harness.project_packs.registry import (
    LoadedProjectPack,
    ProjectPackError,
    ProjectPackRegistry,
)
from app.harness.runtime.distribution import DistributionProfile, profile_for
from app.settings import get_settings, repo_root


@dataclass(frozen=True)
class ExtensionRuntime:
    profile: DistributionProfile
    project_packs: ProjectPackRegistry
    adapters: AdapterRegistry
    adapter_bindings: dict[tuple[str, str], str]

    def adapter_name(self, project_id: str, alias: str) -> str:
        try:
            return self.adapter_bindings[(project_id, alias)]
        except KeyError as exc:
            raise KeyError(
                f"project pack '{project_id}' has no adapter '{alias}'"
            ) from exc


def build_extension_runtime(
    *,
    distribution: str,
    pack_roots: tuple[Path, ...],
    execution_backend: str | None = None,
    execution_device: Literal["cpu", "gpu"] | None = None,
    remote_client: RemoteJobClient | None = None,
    remote_config: RemoteExecutorConfig | None = None,
    remote_artifact_root: Path | None = None,
    workspace_runs_root: Path | None = None,
) -> ExtensionRuntime:
    settings = get_settings()
    selected_backend = execution_backend or settings.mars_execution_backend
    if execution_device is not None:
        selected_device = execution_device
    elif execution_backend is not None:
        selected_device = "gpu" if selected_backend == "remote_gpu" else "cpu"
    else:
        selected_device = settings.effective_execution_device
    effective_remote_config: RemoteExecutorConfig | None = None
    effective_remote_client: RemoteJobClient | None = None
    workspace_resolver = PersistedCodeWorkspaceResolver(
        runs_root=workspace_runs_root or (repo_root() / "runs")
    )
    if selected_device == "gpu":
        effective_remote_config = remote_config or load_remote_executor_config()
        effective_remote_client = remote_client or RemoteExecutor(
            effective_remote_config
        )

    profile = profile_for(distribution)
    packs = ProjectPackRegistry(
        core_version=profile.core_version,
        allow_private=profile.name == "v31-wireless",
    )
    packs.load_paths(pack_roots)
    adapters = AdapterRegistry()
    bindings: dict[tuple[str, str], str] = {}
    for pack in packs.list():
        _validate_pack_payload(pack)
        for alias, declaration in sorted(pack.manifest.adapters.items()):
            if not declaration.trusted:
                raise ProjectPackError(
                    f"adapter '{pack.manifest.project_id}:{alias}' must be trusted"
                )
            qualified_name = f"{pack.manifest.project_id}:{alias}"
            adapter: ProjectAdapter
            if (
                selected_device == "gpu"
                and effective_remote_config is not None
                and effective_remote_client is not None
            ):
                adapter = RemoteProjectAdapter(
                    name=qualified_name,
                    client=effective_remote_client,
                    trusted_adapter_argv=_expand_adapter_argv(
                        declaration.argv,
                        python_executable=effective_remote_config.python,
                    ),
                    artifact_root=(remote_artifact_root or (repo_root() / "runs")),
                    workspace_resolver=workspace_resolver,
                    remote_worker_python=effective_remote_config.python,
                    adapter_timeout_seconds=declaration.timeout_seconds,
                    max_wait_seconds=(
                        declaration.timeout_seconds
                        + effective_remote_config.transfer_timeout_seconds
                        + effective_remote_config.command_timeout_seconds
                    ),
                    heartbeat_interval_seconds=max(
                        0.1,
                        min(
                            30.0,
                            effective_remote_config.heartbeat_stale_seconds / 2.0,
                        ),
                    ),
                )
            else:
                adapter = ProcessAdapter(
                    name=qualified_name,
                    argv=_expand_adapter_argv(declaration.argv),
                    timeout_seconds=declaration.timeout_seconds,
                    env=_pack_adapter_environment(pack),
                    workspace_resolver=workspace_resolver,
                )
            adapters.register(
                qualified_name,
                adapter,
            )
            bindings[(pack.manifest.project_id, alias)] = qualified_name
    return ExtensionRuntime(
        profile=profile,
        project_packs=packs,
        adapters=adapters,
        adapter_bindings=bindings,
    )


_runtime: ExtensionRuntime | None = None
_runtime_key: tuple[
    str,
    tuple[Path, ...],
    str,
    Literal["cpu", "gpu"],
    RemoteExecutorConfig | None,
] | None = None


def get_extension_runtime() -> ExtensionRuntime:
    global _runtime, _runtime_key
    settings = get_settings()
    execution_device = settings.effective_execution_device
    remote_config = (
        load_remote_executor_config()
        if execution_device == "gpu"
        else None
    )
    key = (
        settings.mars_distribution,
        settings.project_pack_roots,
        settings.mars_execution_backend,
        execution_device,
        remote_config,
    )
    if _runtime is None or _runtime_key != key:
        _runtime = build_extension_runtime(
            distribution=settings.mars_distribution,
            pack_roots=settings.project_pack_roots,
            execution_backend=settings.mars_execution_backend,
            execution_device=execution_device,
            remote_config=remote_config,
        )
        _runtime_key = key
    return _runtime


def reset_extension_runtime() -> None:
    global _runtime, _runtime_key
    _runtime = None
    _runtime_key = None


def _validate_pack_payload(pack: LoadedProjectPack) -> None:
    yaml_files = ("project", "metrics", "discovery", "workflow")
    for file_name in yaml_files:
        path = pack.file(file_name)
        if not path.is_file():
            raise ProjectPackError(
                f"project pack '{pack.manifest.project_id}' is missing {file_name}: {path}"
            )
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ProjectPackError(f"invalid pack file {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ProjectPackError(f"pack file {path} must contain a mapping")

    ui_schema_path = pack.file("ui_schema")
    if not ui_schema_path.is_file():
        raise ProjectPackError(
            f"project pack '{pack.manifest.project_id}' is missing ui_schema: "
            f"{ui_schema_path}"
        )
    try:
        ui_schema: Any = json.loads(ui_schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectPackError(f"invalid UI schema {ui_schema_path}: {exc}") from exc
    if not isinstance(ui_schema, dict):
        raise ProjectPackError(f"UI schema {ui_schema_path} must contain an object")


def _expand_adapter_argv(
    argv: tuple[str, ...],
    *,
    python_executable: str = sys.executable,
) -> tuple[str, ...]:
    """Resolve the only Core-owned argv placeholder without invoking a shell."""

    expanded: list[str] = []
    for token in argv:
        if token == "{python}":
            expanded.append(python_executable)
            continue
        if "{" in token or "}" in token:
            raise ProjectPackError(f"unsupported adapter argv placeholder: {token!r}")
        if any(marker in token for marker in ("\x00", "\n", "\r")):
            raise ProjectPackError("adapter argv contains an invalid control character")
        expanded.append(token)
    return tuple(expanded)


def _pack_adapter_environment(pack: LoadedProjectPack) -> dict[str, str]:
    """Build an explicit, trusted import path without forwarding ambient paths."""

    direct_source = pack.root / "src"
    overlay_source = pack.root.parent.parent / "src"
    paths: list[Path] = []
    if direct_source.is_dir():
        paths.append(direct_source.resolve())
    elif overlay_source.is_dir():
        # Private removable overlays keep ``project_packs/<id>`` and ``src/``
        # as siblings below one repository root.
        paths.append(overlay_source.resolve())
    core_backend = Path(__file__).resolve().parents[2]
    paths.append(core_backend)
    return {"PYTHONPATH": os.pathsep.join(str(path) for path in paths)}
