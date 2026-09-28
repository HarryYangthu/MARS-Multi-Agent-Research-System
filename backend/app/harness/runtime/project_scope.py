"""Host-bound project capabilities; never constructed from model/tool arguments.

Binding narrows file access. It does not authorize a model call or command, and
it does not replace the owner's contract admission or resource ledger.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
from fnmatch import fnmatchcase
from pathlib import Path
import re
from typing import Literal
import unicodedata

from app.harness.discovery.snapshots import _hard_forbidden, verify_snapshot
from app.harness.runtime.research_contract import relative_scope


def safe_scope_path(root: Path, relative: str, *, must_exist: bool = False) -> Path:
    """No symlink components, directories-as-files, or lexical escape."""
    name = relative_scope(relative)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Project scope root is unavailable or symbolic")
    base = root.resolve(strict=True)
    path = base
    for part in Path(name).parts:
        path /= part
        if path.is_symlink():
            raise ValueError("Project scope cannot traverse symbolic links")
    if not path.resolve().is_relative_to(base):
        raise ValueError("Path escapes the bound project scope")
    if path.exists() and not path.is_file():
        raise ValueError("Project file must be a regular file")
    if path.is_file() and path.stat().st_nlink != 1:
        raise ValueError("Project file cannot be a hard link")
    if must_exist and not path.is_file():
        raise ValueError("Project file is unavailable")
    return path


def forbidden_source_path(name: str) -> bool:
    """Keep the snapshot hard exclusions and exclude runtime/control surfaces."""
    parts = Path(name.casefold()).parts
    return (_hard_forbidden(name.casefold())
            or any(part in {".venv", "venv", "node_modules", ".ssh", ".mars"} for part in parts)
            or any(part.startswith(".env") for part in parts)
            or any(part in {"snapshot_manifest.json", ".mars_candidate_workspace.json"} for part in parts))


def normalized_scope_name(name: str) -> str:
    """Conservative denial comparison across macOS/Windows path aliases."""
    return unicodedata.normalize("NFC", name).casefold()


def _covered(name: str, scopes: tuple[str, ...], *, conservative_case: bool = False) -> bool:
    if conservative_case:
        name, scopes = normalized_scope_name(name), tuple(normalized_scope_name(item) for item in scopes)
    return any(name == item or name.startswith(item.rstrip("/") + "/") for item in scopes)


@dataclass(frozen=True)
class ProjectScope:
    run_id: str
    project: str
    task_sha256: str
    run_root: Path
    metadata_root: Path
    snapshot_root: Path
    candidate_root: Path
    snapshot_id: str
    readable_files: tuple[str, ...]
    allowed_write_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    excluded_paths: tuple[str, ...] = ()
    # References are declarations only. No data read grant or data copy occurs.
    data_references: tuple[str, ...] = ()
    metadata_hashes: tuple[tuple[str, str], ...] = ()
    forbidden_patterns: tuple[str, ...] = ()

    def validate_identity(self, project: str, run_id: str | None = None) -> None:
        if self.project != project or run_id is not None and self.run_id != run_id:
            raise ValueError("Bound project scope belongs to a different run or project")
        root = self.run_root
        if root.is_symlink() or not root.is_dir() or root.resolve() != root:
            raise ValueError("Bound run root is unavailable or redirected")
        for child in (self.metadata_root, self.snapshot_root, self.candidate_root):
            if not child.is_relative_to(root):
                raise ValueError("Project capability paths must remain in their run")
            current = root
            for part in child.relative_to(root).parts:
                current /= part
                if current.is_symlink():
                    raise ValueError("Project capability directory cannot be symbolic")
            if not child.is_dir():
                raise ValueError("Project capability directory is unavailable")
        for name, expected in self.metadata_hashes:
            path = safe_scope_path(self.metadata_root, name, must_exist=True)
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("Bound project context fingerprint changed")

    def resolve_file(self, relative: str, *, write: bool = False, must_exist: bool = False) -> Path:
        self.validate_identity(self.project, self.run_id)
        name = relative_scope(relative)
        if self.excludes(name):
            raise ValueError("Path is excluded from the bound source scope")
        if write:
            if _covered(name, self.protected_paths, conservative_case=True):
                raise ValueError("Path is baseline-protected in the frozen project contract")
            if not _covered(name, self.allowed_write_paths):
                raise ValueError("Path is outside the frozen contract's allowed write scope")
        elif name not in self.readable_files and not _covered(name, self.allowed_write_paths):
            raise ValueError("Path is outside the bound source read scope")
        return safe_scope_path(self.candidate_root, name, must_exist=must_exist)

    def excludes(self, name: str) -> bool:
        return (forbidden_source_path(name) or _covered(name, self.excluded_paths, conservative_case=True)
                or any(fnmatchcase(normalized_scope_name(name), normalized_scope_name(pattern))
                       or normalized_scope_name(name).startswith(normalized_scope_name(pattern).rstrip("/") + "/")
                       for pattern in self.forbidden_patterns))

    def run_file(self, relative: str, *, must_exist: bool = False) -> Path:
        self.validate_identity(self.project, self.run_id)
        return safe_scope_path(self.run_root, relative, must_exist=must_exist)


@dataclass(frozen=True)
class CandidateChange:
    path: str
    kind: Literal["created", "modified", "deleted"]
    before_sha256: str | None
    after_sha256: str | None
    before_executable: bool | None
    after_executable: bool | None


def verify_candidate_scope(scope: ProjectScope) -> tuple[CandidateChange, ...]:
    """Independent whole-tree audit; never trust a tool's claimed changed paths.

    This verifies source integrity and authorization, not tests/metrics or
    scientific acceptance. The owner must call it at the candidate boundary.
    """
    scope.validate_identity(scope.project, scope.run_id)
    snapshot = verify_snapshot(scope.snapshot_root)
    if (snapshot.manifest.snapshot_id != scope.snapshot_id or snapshot.manifest.project != scope.project
            or snapshot.manifest.source_ref != scope.task_sha256
            or tuple(item.path for item in snapshot.manifest.files) != scope.readable_files):
        raise ValueError("Candidate source snapshot differs from its bound identity")
    marker = safe_scope_path(scope.candidate_root, ".mars_candidate_workspace.json", must_exist=True)
    if json.loads(marker.read_text(encoding="utf-8")) != {
            "schema_id": "candidate_workspace.v1", "candidate_id": scope.candidate_root.name,
            "snapshot_id": scope.snapshot_id}:
        raise ValueError("Candidate control identity changed")
    expected = {item.path: item for item in snapshot.manifest.files}
    directories = {parent.as_posix() for item in snapshot.manifest.files
                   for parent in Path(item.path).parents if parent.as_posix() != "."}
    actual: dict[str, tuple[str, bool]] = {}
    for path in sorted(scope.candidate_root.rglob("*")):
        relative = path.relative_to(scope.candidate_root).as_posix()
        if path.is_symlink():
            raise ValueError("Candidate contains a symbolic link")
        if relative == ".mars_candidate_workspace.json":
            continue
        if path.is_dir():
            if scope.excludes(relative):
                raise ValueError("Candidate contains an excluded directory")
            if relative not in directories:
                # A new directory is valid only within a writable scope or
                # when necessary to reach an explicitly granted descendant.
                if (_covered(relative, scope.protected_paths, conservative_case=True)
                        or not (_covered(relative, scope.allowed_write_paths)
                                or any(item.startswith(relative + "/") for item in scope.allowed_write_paths))):
                    raise ValueError("Candidate contains an unauthorized directory")
            continue
        target = scope.resolve_file(relative, must_exist=True)
        with target.open("rb") as stream:
            value = hashlib.file_digest(stream, "sha256").hexdigest()
        actual[relative] = (value, bool(target.stat().st_mode & 0o111))
    changes: list[CandidateChange] = []
    for name in sorted(expected.keys() | actual.keys()):
        old = expected.get(name)
        new = actual.get(name)
        before_hash = old.sha256.removeprefix("sha256:") if old else None
        before_mode = old.executable if old else None
        after_hash, after_mode = new if new is not None else (None, None)
        if (before_hash, before_mode) == (after_hash, after_mode):
            continue
        scope.resolve_file(name, write=True)
        changes.append(CandidateChange(path=name,
            kind="created" if old is None else "deleted" if new is None else "modified",
            before_sha256=before_hash, after_sha256=after_hash,
            before_executable=before_mode, after_executable=after_mode))
    return tuple(changes)


_PROJECT_SCOPE: ContextVar[ProjectScope | None] = ContextVar("mars_host_project_scope", default=None)


def current_project_scope(project: str, run_id: str | None = None) -> ProjectScope | None:
    scope = _PROJECT_SCOPE.get()
    if scope is not None:
        scope.validate_identity(project, run_id)
    return scope


@contextmanager
def bind_project_scope(scope: ProjectScope) -> Iterator[ProjectScope]:
    """Trusted owner only; asyncio child tasks inherit and sibling tasks isolate."""
    scope.validate_identity(scope.project, scope.run_id)
    previous = _PROJECT_SCOPE.get()
    if previous is not None and previous != scope:
        raise ValueError("Cannot replace a bound project capability inside its execution")
    token = _PROJECT_SCOPE.set(scope)
    try:
        yield scope
    finally:
        _PROJECT_SCOPE.reset(token)


def validated_diff_paths(diff: str) -> tuple[str, ...]:
    """Accept ordinary textual unified patches only; fail closed on git extensions.

    Parsing hunk lengths prevents content beginning with +++/--- being mistaken
    for a path. Renames, binary patches, symlink modes and quoted paths require a
    separate reviewed adapter and are deliberately unsupported here.
    """
    paths: list[str] = []
    before = after = 0
    old: str | None = None
    pending_old = False
    pair: tuple[str, str] | None = None
    headers: tuple[str, str] | None = None
    hunks = 0
    for line in diff.splitlines():
        if before or after:
            if line == "\\ No newline at end of file":
                continue
            if not line or line[0] not in {" ", "+", "-"}:
                raise ValueError("Malformed unified patch hunk")
            before -= int(line[0] in {" ", "-"})
            after -= int(line[0] in {" ", "+"})
            if before < 0 or after < 0:
                raise ValueError("Malformed unified patch hunk length")
            continue
        if line == "\\ No newline at end of file":
            continue
        if line.startswith("diff --git "):
            if pending_old or headers is not None and hunks == 0:
                raise ValueError("Patch section has no complete content")
            match = re.fullmatch(r"diff --git a/(\S+) b/(\S+)", line)
            if match is None or match[1] != match[2]:
                raise ValueError("Renames and quoted patch paths are unsupported")
            pair = (relative_scope(match[1]), relative_scope(match[2]))
            headers, hunks = None, 0
        elif re.fullmatch(r"(?:new file mode|deleted file mode) 100(?:644|755)", line):
            if headers is not None:
                raise ValueError("Unexpected patch mode")
        elif re.fullmatch(r"index [0-9a-f]+\.\.[0-9a-f]+(?: 100(?:644|755))?", line):
            if headers is not None:
                raise ValueError("Unexpected patch index")
        elif line.startswith("--- "):
            if pending_old or headers is not None and hunks == 0:
                raise ValueError("Incomplete patch section")
            value = line[4:]
            old = _diff_path(value, "a/")
            pending_old = True
            headers, hunks = None, 0
        elif line.startswith("+++ ") and pending_old:
            new = _diff_path(line[4:], "b/")
            if old is None and new is None or old is not None and new is not None and old != new:
                raise ValueError("Patch paths must identify one file per section")
            name = old or new
            assert name is not None
            if pair is not None and pair != (name, name):
                raise ValueError("Patch headers disagree")
            headers = (old or "/dev/null", new or "/dev/null")
            if name not in paths:
                paths.append(name)
            pending_old = False
            pair = None
        elif line.startswith("@@") and headers is not None:
            match = re.fullmatch(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@(?: .*)?", line)
            if match is None:
                raise ValueError("Malformed unified patch hunk header")
            before, after = int(match[1] or "1"), int(match[2] or "1")
            if not (before or after):
                raise ValueError("Empty patch hunk")
            hunks += 1
        else:
            raise ValueError("Unsupported or ambiguous patch syntax")
    if not paths or before or after or pending_old or pair is not None or not hunks:
        raise ValueError("Patch has no complete textual change")
    return tuple(paths)


def _diff_path(value: str, prefix: str) -> str | None:
    if value == "/dev/null":
        return None
    if not value.startswith(prefix) or any(char.isspace() for char in value) or '"' in value:
        raise ValueError("Unsupported patch path")
    return relative_scope(value[len(prefix):])
