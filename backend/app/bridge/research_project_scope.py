"""Prepare isolated per-run file capabilities from an already frozen contract.

This is a file preparation step, not execution admission. In particular, data
references, commands, model budgets and remote environments remain ungranted.
"""
from __future__ import annotations

import fnmatch
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

import yaml

from app.bridge.research_contract_service import validate_frozen_research_task
from app.bridge.research_run_service import load_run_research_contract
from app.harness.discovery.snapshots import (
    SnapshotPolicy, create_snapshot, materialize_candidate_workspace,
)
from app.harness.persistence import atomic_write_json, path_lock
from app.harness.runtime.project_scope import (
    ProjectScope, forbidden_source_path, normalized_scope_name, safe_scope_path, verify_candidate_scope,
)
from app.storage.run_store import RunHandle


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) or name == pattern.rstrip("/")
               or name.startswith(pattern.rstrip("/") + "/") for pattern in patterns)


def _safe_directory(root: Path, relative: str) -> Path:
    path = root
    for part in Path(relative).parts:
        path /= part
        if path.is_symlink() or path.exists() and not path.is_dir():
            raise ValueError("Project scope storage cannot be redirected")
        path.mkdir(exist_ok=True)
    return path


def _canonical_input(path: Path) -> Path:
    # Frozen contract paths are absolute. Canonical OS ancestor aliases may be
    # resolved at freeze time, but no user-declared input may traverse a link.
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("Declared scope input cannot traverse symbolic links")
    return path.resolve(strict=True)


def _relative_to_actual_root(path: Path, root: Path) -> str | None:
    """Also detect case aliases on filesystems whose names are case-insensitive."""
    if not root.exists():
        return None
    for parent in (path, *path.parents):
        if parent.exists() and parent.samefile(root):
            return path.relative_to(parent).as_posix()
    return None


def _source_files(code: Path, policy: SnapshotPolicy, excluded: tuple[str, ...]) -> tuple[str, ...]:
    files: list[str] = []
    denied = tuple(normalized_scope_name(item) for item in (*excluded, *policy.ignore_patterns, *policy.forbidden_paths))
    for directory, dirs, names in os.walk(code, followlinks=False):
        base = Path(directory)
        kept: list[str] = []
        for name in sorted(dirs):
            path = base / name
            rel = path.relative_to(code).as_posix()
            if forbidden_source_path(rel) or _matches(normalized_scope_name(rel), denied):
                continue
            if path.is_symlink():
                raise ValueError("Source read scope contains a symbolic directory")
            kept.append(name)
        dirs[:] = kept
        for name in sorted(names):
            path = base / name
            rel = path.relative_to(code).as_posix()
            if forbidden_source_path(rel) or _matches(normalized_scope_name(rel), denied):
                continue
            if not _matches(rel, policy.allowed_paths):
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError("Source read scope must contain only regular non-symbolic files")
            if path.stat().st_nlink != 1:
                raise ValueError("Source read scope cannot contain hard links")
            files.append(rel)
            if len(files) > policy.max_files:
                raise ValueError("Source read scope exceeds snapshot file limits")
    return tuple(sorted(files))


def _knowledge(paths: tuple[str, ...], policy: SnapshotPolicy) -> tuple[str, list[dict[str, Any]]]:
    """Normalize only declared text; unsupported material explicitly blocks preparation."""
    chunks: list[str] = []
    records: list[dict[str, Any]] = []
    total = 0
    for index, value in enumerate(paths):
        root = _canonical_input(Path(value))
        if forbidden_source_path(root.as_posix()):
            raise ValueError("Knowledge root is an excluded private/control scope")
        entries = iter([root]) if root.is_file() else root.rglob("*")
        for path in entries:
            relative = path.relative_to(root).as_posix() if root.is_dir() else path.name
            if forbidden_source_path(relative):
                raise ValueError("Knowledge reference contains an excluded private/control scope")
            if path.is_symlink():
                raise ValueError("Knowledge reference cannot contain symbolic links")
            if path.is_dir():
                continue
            if not path.is_file() or path.suffix.lower() not in {".md", ".txt"} or forbidden_source_path(path.name):
                raise ValueError("Knowledge must be normalized declared .md/.txt files before binding")
            if path.stat().st_nlink != 1:
                raise ValueError("Knowledge reference cannot be a hard link")
            if len(records) >= policy.max_files or path.stat().st_size > policy.max_file_bytes:
                raise ValueError("Knowledge reference exceeds snapshot file limits")
            with path.open("rb") as stream:
                data = stream.read(policy.max_file_bytes + 1)
            total += len(data)
            if len(data) > policy.max_file_bytes or total > policy.max_total_bytes:
                raise ValueError("Knowledge reference exceeds snapshot byte limits")
            text = data.decode("utf-8")
            label = path.relative_to(root).as_posix() if root.is_dir() else path.name
            identifier = f"knowledge_{index}/{label}"
            records.append({"reference": identifier, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)})
            chunks.append(f"## {identifier}\n\n{text}")
    # Filesystem iteration order is not evidence identity.
    ordered = sorted(zip(records, chunks), key=lambda item: str(item[0]["reference"]))
    return "\n\n".join(item[1] for item in ordered), [item[0] for item in ordered]


def prepare_project_scope(run: RunHandle, *, candidate_id: str,
                          snapshot_policy: SnapshotPolicy,
                          request_extra: dict[str, Any] | None = None) -> ProjectScope:
    """Copy a complete admitted read tree, with independent contract write limits.

    snapshot_policy is a trusted host read policy, never the contract's allowed
    WRITE paths. All baseline/entrypoint/rule inputs must survive its exclusions.
    The capability is freshly constructed; serialized metadata cannot grant it.
    """
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", candidate_id) is None:
        raise ValueError("Invalid candidate identity")
    frozen = load_run_research_contract(run, request_extra)
    if frozen is None:
        raise ValueError("A frozen run research contract is required")
    validate_frozen_research_task(frozen, check_live_files=True)
    if run.root.is_symlink():
        raise ValueError("Run scope cannot be symbolic")
    root = run.root.resolve(strict=True)
    project = frozen.task.project
    code = _canonical_input(Path(project.paths.code))
    if root.is_relative_to(code) or code.is_relative_to(root):
        raise ValueError("Run storage must be separate from the source repository")
    excluded: list[str] = []
    for value in (*project.paths.data, project.paths.output):
        path = Path(value)
        if value in project.paths.data:
            path = _canonical_input(path)
        if _relative_to_actual_root(code, path) is not None:
            raise ValueError("Data/output scope cannot contain the source repository")
        relative = _relative_to_actual_root(path, code)
        if relative is not None:
            excluded.append(relative)
    source_files = _source_files(code, snapshot_policy, tuple(excluded))
    required = {item.path: item.sha256 for item in frozen.task.input_fingerprints}
    if not set(required).issubset(source_files):
        raise ValueError("Snapshot read policy excludes a baseline or declared command dependency")
    if (code / "AGENTS.md").exists() and "AGENTS.md" not in source_files:
        raise ValueError("Snapshot read policy excludes project rules")
    knowledge, knowledge_records = _knowledge(project.paths.knowledge, snapshot_policy)
    _safe_directory(root, "context/project_scope")
    lock = safe_scope_path(root, "context/project_scope/prepare.lock")
    with path_lock(lock):
        snapshots = _safe_directory(root, "context/project_scope/snapshots")
        snapshot = create_snapshot(source_root=code, cache_root=snapshots, project=project.project_id,
            source_ref=frozen.task_sha256, policy=snapshot_policy.model_copy(update={"allowed_paths": source_files}))
        actual = {item.path: item.sha256.removeprefix("sha256:") for item in snapshot.manifest.files}
        if any(actual.get(name) != sha for name, sha in required.items()):
            raise ValueError("Declared source changed while preparing the snapshot")
        candidates = _safe_directory(root, "context/project_scope/candidates")
        candidate_path = candidates / candidate_id
        if candidate_path.is_symlink():
            raise ValueError("Candidate directory cannot be symbolic")
        if candidate_path.exists():
            safe_scope_path(candidate_path, ".mars_candidate_workspace.json", must_exist=True)
        candidate = materialize_candidate_workspace(snapshot_root=snapshot.root,
            workspaces_root=candidates, candidate_id=candidate_id)
        metadata = _safe_directory(root, f"context/project_scope/projects/{candidate_id}")
        protected = tuple(sorted(set((*project.baseline_files, *project.protected_paths, "AGENTS.md"))))
        scope = ProjectScope(run_id=run.run_id, project=run.project, task_sha256=frozen.task_sha256,
            run_root=root, metadata_root=metadata, snapshot_root=snapshot.root, candidate_root=candidate,
            snapshot_id=snapshot.manifest.snapshot_id, readable_files=tuple(actual),
            allowed_write_paths=project.allowed_paths, protected_paths=protected,
            excluded_paths=tuple(excluded), data_references=project.paths.data,
            forbidden_patterns=(*snapshot_policy.forbidden_paths, *snapshot_policy.ignore_patterns))
        scope.validate_identity(run.project, run.run_id)
        verify_candidate_scope(scope)
        rules = (snapshot.root / "AGENTS.md").read_text(encoding="utf-8") if "AGENTS.md" in actual else "No project-specific rules supplied.\n"
        values = {"AGENTS.md": rules,
            "project.yaml": yaml.safe_dump({"name": project.project_id, "display_name": project.display_name,
                **({"knowledge_file": "knowledge.md"} if knowledge else {})}, allow_unicode=True),
            "repo_link.yaml": yaml.safe_dump({"repo_mode": "run_bound", "repo_path": str(candidate),
                "allowed_paths": list(project.allowed_paths), "protected_paths": list(protected)}, allow_unicode=True)}
        if knowledge:
            values["knowledge.md"] = knowledge
        for name, content in values.items():
            path = safe_scope_path(metadata, name)
            if path.exists():
                if path.read_text(encoding="utf-8") != content:
                    raise ValueError("Saved project context differs; prepare a new candidate")
            else:
                with path.open("x", encoding="utf-8") as stream:
                    stream.write(content)
        scope = replace(scope, metadata_hashes=tuple(sorted(
            (name, hashlib.sha256(content.encode("utf-8")).hexdigest()) for name, content in values.items())))
        record = {"schema_id": "run_project_scope.v1", "run_id": run.run_id, "project": run.project,
            "task_sha256": frozen.task_sha256, "candidate_id": candidate_id, "snapshot_id": scope.snapshot_id,
            "source_read_policy": snapshot_policy.model_dump(mode="json"), "source_files": list(actual),
            "allowed_write_paths": list(project.allowed_paths), "protected_paths": list(protected),
            "excluded_source_paths": excluded, "knowledge": knowledge_records,
            "data": [{"reference": f"data_{index}", "access": "not_granted", "fingerprint": None}
                     for index, _value in enumerate(project.paths.data)],
            "commands": "not_granted", "execution_admission": "blocked"}
        record_path = scope.run_file(f"context/project_scope/{candidate_id}.json")
        if record_path.exists():
            if json.loads(record_path.read_text(encoding="utf-8")) != record:
                raise ValueError("Saved project scope differs; prepare a new candidate")
        else:
            atomic_write_json(record_path, record)
        return scope
