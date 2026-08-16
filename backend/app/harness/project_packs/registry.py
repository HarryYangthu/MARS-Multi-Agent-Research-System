"""Fail-closed filesystem registry for Project Pack manifests."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, cast

import yaml
from pydantic import ValidationError

from app.harness.project_packs.models import ProjectPackManifest


class ProjectPackError(ValueError):
    """Raised when a configured pack cannot be trusted or loaded."""


@dataclass(frozen=True)
class LoadedProjectPack:
    manifest: ProjectPackManifest
    root: Path

    def file(self, name: str) -> Path:
        relative = cast(str, getattr(self.manifest.files, name))
        target = (self.root / relative).resolve()
        if not _is_relative_to(target, self.root):
            raise ProjectPackError(f"pack file '{relative}' escapes {self.root}")
        return target


class ProjectPackRegistry:
    def __init__(self, *, core_version: str) -> None:
        self.core_version = core_version
        self._packs: dict[str, LoadedProjectPack] = {}

    def register(self, pack: LoadedProjectPack) -> None:
        project_id = pack.manifest.project_id
        if project_id in self._packs:
            raise ProjectPackError(f"duplicate project pack '{project_id}'")
        if not version_satisfies(self.core_version, pack.manifest.requires_core):
            raise ProjectPackError(
                f"project pack '{project_id}' requires core "
                f"{pack.manifest.requires_core}; active core is {self.core_version}"
            )
        self._packs[project_id] = pack

    def load_paths(self, roots: Iterable[Path]) -> None:
        for root in roots:
            for manifest_path in _manifest_paths(root):
                self.register(load_project_pack(manifest_path))

    def get(self, project_id: str) -> LoadedProjectPack:
        try:
            return self._packs[project_id]
        except KeyError as exc:
            raise ProjectPackError(f"unknown project pack '{project_id}'") from exc

    def list(self) -> tuple[LoadedProjectPack, ...]:
        return tuple(self._packs[key] for key in sorted(self._packs))


def load_project_pack(manifest_path: Path) -> LoadedProjectPack:
    path = manifest_path.resolve()
    if not path.is_file():
        raise ProjectPackError(f"project pack manifest not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        manifest = ProjectPackManifest.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ProjectPackError(f"invalid project pack manifest {path}: {exc}") from exc
    return LoadedProjectPack(manifest=manifest, root=path.parent)


def version_satisfies(version: str, constraint: str) -> bool:
    current = _version_tuple(version)
    for item in (part.strip() for part in constraint.split(",")):
        if not item:
            continue
        match = re.fullmatch(r"(>=|<=|==|>|<)\s*(\d+\.\d+\.\d+)", item)
        if match is None:
            raise ProjectPackError(f"unsupported core version constraint '{item}'")
        expected = _version_tuple(match.group(2))
        operator = match.group(1)
        passed = {
            ">=": current >= expected,
            "<=": current <= expected,
            "==": current == expected,
            ">": current > expected,
            "<": current < expected,
        }[operator]
        if not passed:
            return False
    return True


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", value.strip())
    if match is None:
        raise ProjectPackError(f"invalid semantic version '{value}'")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _manifest_paths(root: Path) -> tuple[Path, ...]:
    resolved = root.resolve()
    if (resolved / "project_pack.yaml").is_file():
        return (resolved / "project_pack.yaml",)
    if not resolved.is_dir():
        raise ProjectPackError(f"project pack root not found: {resolved}")
    return tuple(
        path
        for path in sorted(resolved.glob("*/project_pack.yaml"))
        if path.is_file()
    )


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
