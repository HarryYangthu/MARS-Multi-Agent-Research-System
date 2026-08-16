"""Composition root for public Project Packs and optional private overlays."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.execution.adapters.process import ProcessAdapter
from app.execution.adapters.registry import AdapterRegistry
from app.harness.project_packs.registry import (
    LoadedProjectPack,
    ProjectPackError,
    ProjectPackRegistry,
)
from app.harness.runtime.distribution import DistributionProfile, profile_for
from app.settings import get_settings


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
) -> ExtensionRuntime:
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
            adapters.register(
                qualified_name,
                ProcessAdapter(
                    name=qualified_name,
                    argv=_expand_adapter_argv(declaration.argv),
                    timeout_seconds=declaration.timeout_seconds,
                    env=_pack_adapter_environment(pack),
                ),
            )
            bindings[(pack.manifest.project_id, alias)] = qualified_name
    return ExtensionRuntime(
        profile=profile,
        project_packs=packs,
        adapters=adapters,
        adapter_bindings=bindings,
    )


_runtime: ExtensionRuntime | None = None
_runtime_key: tuple[str, tuple[Path, ...]] | None = None


def get_extension_runtime() -> ExtensionRuntime:
    global _runtime, _runtime_key
    settings = get_settings()
    key = (settings.mars_distribution, settings.project_pack_roots)
    if _runtime is None or _runtime_key != key:
        _runtime = build_extension_runtime(
            distribution=settings.mars_distribution,
            pack_roots=settings.project_pack_roots,
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


def _expand_adapter_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    """Resolve the only Core-owned argv placeholder without invoking a shell."""

    expanded: list[str] = []
    for token in argv:
        if token == "{python}":
            expanded.append(sys.executable)
            continue
        if "{" in token or "}" in token:
            raise ProjectPackError(f"unsupported adapter argv placeholder: {token!r}")
        if any(marker in token for marker in ("\x00", "\n", "\r")):
            raise ProjectPackError("adapter argv contains an invalid control character")
        expanded.append(token)
    return tuple(expanded)


def _pack_adapter_environment(pack: LoadedProjectPack) -> dict[str, str]:
    """Make a mounted ``src/`` Pack importable without installing it into Core."""

    source_root = pack.root / "src"
    if not source_root.is_dir():
        return {}
    inherited = os.environ.get("PYTHONPATH", "").strip()
    value = str(source_root.resolve())
    if inherited:
        value = os.pathsep.join((value, inherited))
    return {"PYTHONPATH": value}
