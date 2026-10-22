"""Restore explicitly bundled project history without invoking any agent."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
from typing import Any
from uuid import uuid4
import zipfile

from loguru import logger

from app.harness.project_workspace import folder_project, open_folder


def _native_path(path: Path) -> str:
    value = str(path.absolute())
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    return value


def _checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def restore_bundled_project(root: Path, entry: dict[str, Any], *, registry: Path | None = None) -> Path:
    """Extract once, preserve identity and user edits, then register the current path."""
    name, identity = entry.get("folder"), entry.get("project_id")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("Invalid bundled project folder")
    if not isinstance(identity, str) or not re.fullmatch(r"folder_[0-9a-f]{32}", identity):
        raise ValueError("Invalid bundled project identity")
    try:
        existing = folder_project(identity, registry=registry)
    except (OSError, ValueError):
        existing = None
    if existing is not None:
        return existing.root
    destination = root / "workspace" / "projects" / name
    if not destination.exists():
        archive_name, expected = entry.get("archive"), entry.get("sha256")
        if not isinstance(archive_name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+\.zip", archive_name):
            raise ValueError("Invalid bundled project archive")
        archive = root / "bundled-projects" / archive_name
        if archive.is_symlink() or _checksum(archive) != expected:
            raise ValueError("Bundled project checksum mismatch")
        temporary = destination.parent / ("." + name + "-import-" + uuid4().hex)
        temporary.mkdir(parents=True)
        try:
            with zipfile.ZipFile(archive) as packed:
                seen: set[str] = set()
                for info in packed.infolist():
                    parts = PurePosixPath(info.filename).parts
                    if (not parts or parts[0] != name or info.filename.startswith("/")
                            or "\\" in info.filename or any(p in {".", ".."} or ":" in p for p in parts)
                            or stat.S_ISLNK(info.external_attr >> 16)):
                        raise ValueError("Unsafe bundled project archive path")
                    relative = PurePosixPath(*parts).as_posix().rstrip("/").casefold()
                    if relative in seen:
                        raise ValueError("Duplicate bundled project archive path")
                    seen.add(relative)
                for info in packed.infolist():
                    target = temporary.joinpath(*PurePosixPath(info.filename).parts)
                    if info.is_dir():
                        os.makedirs(_native_path(target), exist_ok=True)
                    else:
                        os.makedirs(_native_path(target.parent), exist_ok=True)
                        with packed.open(info) as source, open(_native_path(target), "xb") as copied:
                            shutil.copyfileobj(source, copied, 1024 * 1024)
            marker = temporary / name / ".mars" / "project.yaml"
            import yaml
            if yaml.safe_load(marker.read_text(encoding="utf-8")).get("name") != identity:
                raise ValueError("Bundled project identity mismatch")
            (temporary / name).rename(destination)
        finally:
            shutil.rmtree(_native_path(temporary), ignore_errors=True)
    import yaml
    if destination.is_symlink() or yaml.safe_load(
            (destination / ".mars/project.yaml").read_text(encoding="utf-8")).get("name") != identity:
        raise ValueError("Existing destination belongs to another project")
    # open_folder validates the existing identity and never overwrites project history.
    restored = open_folder(str(destination), registry=registry)
    if restored.name != identity:
        raise ValueError("Existing destination belongs to another project")
    return restored.root


def bootstrap_bundled_projects(root: Path, *, registry: Path | None = None) -> list[Path]:
    manifest = root / "bundled-projects" / "manifest.json"
    if not manifest.is_file():
        return []
    restored = []
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        if raw.get("schema") != "mars.bundled_projects.v1" or not isinstance(raw.get("projects"), list):
            raise ValueError("Invalid bundled projects manifest")
        for entry in raw["projects"]:
            try:
                path = restore_bundled_project(root, entry, registry=registry)
                restored.append(path)
                logger.info("Bundled project history available: {}", path.name)
            except (OSError, ValueError, zipfile.BadZipFile) as error:
                logger.error("Bundled project import failed: {}", type(error).__name__)
    except (OSError, ValueError) as error:
        logger.error("Bundled projects manifest unavailable: {}", type(error).__name__)
    return restored
