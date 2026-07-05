"""Adapter for delegating the coding loop to opencode under MARS governance."""
from __future__ import annotations

import asyncio
import difflib
import json
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from app.agents.base import ContextPack, RunRequest
from app.harness.tools.project_repo import (
    TEXT_SUFFIXES,
    ProjectRepo,
    load_project_repo,
    resolve_allowed_path,
    validate_repo_writable,
)
from app.settings import get_settings


_OPENCODE_TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class OpenCodeResult:
    backend: str
    status: str
    task_packet_path: str
    transcript_path: str
    diff_path: str = ""
    files_changed: list[dict[str, str]] = field(default_factory=list)
    checks: list[dict[str, str]] = field(default_factory=list)
    diff_stats: dict[str, int] = field(default_factory=dict)
    error: str = ""


class OpenCodeAdapter:
    """Create a controlled task packet and invoke opencode when available."""

    @staticmethod
    def is_available() -> bool:
        return shutil.which("opencode") is not None

    async def run(self, request: RunRequest, context: ContextPack) -> OpenCodeResult:
        fallback_root = Path(tempfile.gettempdir()) / "mars-opencode"
        run_root = Path(str(request.extra.get("run_root") or fallback_root))
        coding_dir = Path(str(request.extra.get("agent_dir") or run_root / "coding"))
        coding_dir.mkdir(parents=True, exist_ok=True)
        node_key = str(request.extra.get("node_key") or "coding")
        packet_path = coding_dir / f"opencode_task_packet.{node_key}.json"
        transcript_path = coding_dir / f"opencode_transcript.{node_key}.md"
        diff_path = coding_dir / f"patch.{_version_for(request)}.diff"
        snapshot_path = coding_dir / f"opencode_before_snapshot.{node_key}.json"
        project_repo = load_project_repo(request.project)
        validate_repo_writable(project_repo)
        before_snapshot = _snapshot_allowed_files(project_repo)
        snapshot_path.write_text(
            json.dumps(before_snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        packet = _task_packet(request=request, context=context, project_repo=project_repo)
        packet_path.write_text(
            json.dumps(packet, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        executable = shutil.which("opencode")
        settings = get_settings()
        if executable is None:
            if settings.is_production or settings.mars_mock_mode == "never":
                raise RuntimeError("MARS_CODING_BACKEND=opencode but opencode is not installed")
            transcript_path.write_text(_mock_transcript(packet), encoding="utf-8")
            return OpenCodeResult(
                backend="opencode",
                status="mock_fallback",
                task_packet_path=_rel(run_root, packet_path),
                transcript_path=_rel(run_root, transcript_path),
                checks=[{"name": "opencode.available", "status": "skipped"}],
            )

        prompt = _prompt_from_packet()
        try:
            proc = await asyncio.create_subprocess_exec(
                *_opencode_command(
                    executable=executable,
                    packet_path=packet_path,
                    project_root=project_repo.root,
                    prompt=prompt,
                ),
                cwd=str(project_repo.root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:
            transcript_path.write_text(
                "# opencode transcript\n\n"
                f"- exit_code: adapter_error\n"
                f"- generated_at: {datetime.now(tz=timezone.utc).isoformat()}\n\n"
                f"## Adapter Error\n\n{type(exc).__name__}: {exc!s}\n",
                encoding="utf-8",
            )
            return OpenCodeResult(
                backend="opencode",
                status="failed",
                task_packet_path=_rel(run_root, packet_path),
                transcript_path=_rel(run_root, transcript_path),
                checks=[{"name": "opencode.spawn", "status": "failed", "detail": f"{type(exc).__name__}: {exc!s}"}],
                error=f"{type(exc).__name__}: {exc!s}",
            )

        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=_OPENCODE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            timed_out = True
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            stdout, stderr = await proc.communicate()

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        after_snapshot = _snapshot_allowed_files(project_repo)
        snapshot_diff = _diff_snapshots(before_snapshot, after_snapshot, project_repo)
        transcript_path.write_text(
            "# opencode transcript\n\n"
            f"- exit_code: {'timeout' if timed_out else proc.returncode}\n"
            f"- generated_at: {datetime.now(tz=timezone.utc).isoformat()}\n\n"
            + (f"- timeout_seconds: {_OPENCODE_TIMEOUT_SECONDS}\n\n" if timed_out else "")
            + "## stdout\n\n```text\n"
            f"{out}\n```\n\n## stderr\n\n```text\n{err}\n```\n",
            encoding="utf-8",
        )
        diff_result = snapshot_diff
        diff_source = "snapshot"
        if not diff_result.text and proc.returncode == 0:
            diff_result = _worktree_diff_for_opencode_edits(project_repo, out)
            diff_source = "opencode_edited_worktree"
        if not diff_result.text and proc.returncode == 0:
            diff_result = _worktree_diff_for_opencode_references(project_repo, out)
            diff_source = "opencode_referenced_worktree"
        if not diff_result.text and proc.returncode == 0:
            diff_result = _worktree_diff_for_allowed_changes(project_repo)
            diff_source = "allowed_worktree"
        diff = diff_result.text or _extract_unified_diff(out)
        files_changed = diff_result.files_changed or _diff_files(diff)
        if diff:
            diff_path.write_text(diff, encoding="utf-8")
        completed = proc.returncode == 0 and bool(files_changed)
        completed_with_timeout = timed_out and bool(files_changed)
        completed_with_worktree_fallback = completed and diff_source in {
            "opencode_referenced_worktree",
            "allowed_worktree",
        }
        status = (
            "completed"
            if completed and not completed_with_worktree_fallback
            else "completed_with_warnings"
            if completed_with_timeout or completed_with_worktree_fallback
            else "failed"
        )
        checks = [{"name": "opencode.exit_code", "status": "timeout" if timed_out else str(proc.returncode)}]
        if diff_source in {"opencode_referenced_worktree", "allowed_worktree"} and files_changed:
            checks.append(
                {
                    "name": "opencode.diff_source",
                    "status": "warning",
                    "detail": f"no fresh edit/write diff in this run; generated patch from {diff_source}",
                }
            )
        if timed_out:
            checks.append(
                {
                    "name": "opencode.timeout",
                    "status": "warning" if files_changed else "failed",
                    "detail": f"timed out after {_OPENCODE_TIMEOUT_SECONDS}s",
                }
            )
        return OpenCodeResult(
            backend="opencode",
            status=status,
            task_packet_path=_rel(run_root, packet_path),
            transcript_path=_rel(run_root, transcript_path),
            diff_path=_rel(run_root, diff_path) if diff else "",
            files_changed=files_changed,
            checks=checks,
            diff_stats={
                "files_changed": len(files_changed),
                "insertions": diff_result.insertions,
                "deletions": diff_result.deletions,
            },
            error=(
                f"opencode timed out after {_OPENCODE_TIMEOUT_SECONDS}s"
                if timed_out
                else err if proc.returncode
                else ("" if completed else "opencode completed without code changes")
            ),
        )


def _task_packet(
    *,
    request: RunRequest,
    context: ContextPack,
    project_repo: ProjectRepo,
) -> dict[str, Any]:
    return {
        "schema": "mars_code_task_packet.v1",
        "project": request.project,
        "run_id": request.extra.get("run_id", ""),
        "node_key": request.extra.get("node_key", "coding"),
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "constraints": {
            "direct_code_editing": True,
            "baseline_compatibility_gate": "ToolRegistry.dispatch(code.apply_patch)",
            "allowed_paths_only": True,
            "do_not_modify_baseline_protected_files_directly": True,
        },
        "project_repository": {
            "root": ".",
            "repo_mode": project_repo.repo_mode,
            "read_only": project_repo.read_only,
            "allowed_paths": list(project_repo.allowed_paths),
            "protected_paths": list(project_repo.protected_paths),
            "ignore_patterns": list(project_repo.ignore_patterns),
        },
        "user_request": request.user_request,
        "system": context.system,
        "upstream_artifacts": request.upstream_artifacts,
        "context_metadata": context.metadata,
        "expected_output": {
            "diff": "unified diff, if a patch is needed",
            "checks": "commands and results",
            "notes": "implementation rationale without private chain-of-thought",
        },
    }


def _prompt_from_packet() -> str:
    return (
        "You are an external coding agent called by MARS. Produce a concise "
        "implementation transcript and modify the project repository directly. "
        "The process working directory is the project repository root; use only "
        "relative paths from that directory. Read the attached JSON task packet, "
        "inspect and edit only allowed paths, preserve protected baseline "
        "interfaces, then summarize changed files, tests to run, and residual risks."
    )


def _opencode_command(
    *,
    executable: str,
    packet_path: Path,
    project_root: Path,
    prompt: str,
) -> list[str]:
    return [
        executable,
        "run",
        prompt,
        "--dir",
        str(project_root),
        "--format",
        "json",
        "--auto",
        f"--file={packet_path}",
    ]


@dataclass(frozen=True)
class _SnapshotDiff:
    text: str
    files_changed: list[dict[str, str]]
    insertions: int
    deletions: int


def _snapshot_allowed_files(project_repo: ProjectRepo) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for allowed in project_repo.allowed_paths:
        try:
            root = resolve_allowed_path(project_repo, allowed, require_exists=False)
        except ValueError:
            continue
        if root.is_file():
            _snapshot_file(project_repo, root, snapshot)
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                _snapshot_file(project_repo, path, snapshot)
    return snapshot


def _snapshot_file(project_repo: ProjectRepo, path: Path, snapshot: dict[str, str]) -> None:
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return
    try:
        rel = path.relative_to(project_repo.root).as_posix()
        resolve_allowed_path(project_repo, rel, require_exists=True, require_text=True)
        snapshot[rel] = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return


def _diff_snapshots(
    before: dict[str, str],
    after: dict[str, str],
    project_repo: ProjectRepo,
) -> _SnapshotDiff:
    files_changed: list[dict[str, str]] = []
    chunks: list[str] = []
    insertions = 0
    deletions = 0
    for path in sorted(set(before) | set(after)):
        old = before.get(path)
        new = after.get(path)
        if old == new:
            continue
        change_type = "modified"
        original = old
        updated = new
        if old is None:
            change_type = "added"
            original = ""
        if new is None:
            change_type = "deleted"
            updated = ""
        files_changed.append({"path": path, "type": change_type, "risk": _risk_for(path, project_repo)})
        lines = _git_style_file_diff(
            path=path,
            old_text=original or "",
            new_text=updated or "",
            change_type=change_type,
        )
        for line in lines:
            if line.startswith("+") and not line.startswith("+++"):
                insertions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1
        chunks.extend(lines)
    text = "\n".join(chunks).strip()
    return _SnapshotDiff(
        text=f"{text}\n" if text else "",
        files_changed=files_changed,
        insertions=insertions,
        deletions=deletions,
    )


def _git_style_file_diff(
    *,
    path: str,
    old_text: str,
    new_text: str,
    change_type: str,
) -> list[str]:
    fromfile = "/dev/null" if change_type == "added" else f"a/{path}"
    tofile = "/dev/null" if change_type == "deleted" else f"b/{path}"
    header = [f"diff --git a/{path} b/{path}"]
    if change_type == "added":
        header.append("new file mode 100644")
    elif change_type == "deleted":
        header.append("deleted file mode 100644")
    body = list(
        difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
    )
    return header + body


def _worktree_diff_for_opencode_edits(
    project_repo: ProjectRepo,
    stdout: str,
) -> _SnapshotDiff:
    before: dict[str, str] = {}
    after: dict[str, str] = {}
    for rel_path in _opencode_edited_paths(stdout, project_repo):
        try:
            path = resolve_allowed_path(
                project_repo,
                rel_path,
                require_exists=True,
                require_text=True,
            )
        except ValueError:
            continue
        old = _git_head_text(project_repo.root, rel_path)
        if old is not None:
            before[rel_path] = old
        try:
            after[rel_path] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return _diff_snapshots(before, after, project_repo)


def _worktree_diff_for_opencode_references(
    project_repo: ProjectRepo,
    stdout: str,
) -> _SnapshotDiff:
    before: dict[str, str] = {}
    after: dict[str, str] = {}
    for rel_path in _opencode_referenced_paths(stdout, project_repo):
        try:
            path = resolve_allowed_path(
                project_repo,
                rel_path,
                require_exists=True,
                require_text=True,
            )
        except ValueError:
            continue
        old = _git_head_text(project_repo.root, rel_path)
        if old is not None:
            before[rel_path] = old
        try:
            after[rel_path] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return _diff_snapshots(before, after, project_repo)


def _worktree_diff_for_allowed_changes(project_repo: ProjectRepo) -> _SnapshotDiff:
    after = _snapshot_allowed_files(project_repo)
    before: dict[str, str] = {}
    for rel_path in after:
        old = _git_head_text(project_repo.root, rel_path)
        if old is not None:
            before[rel_path] = old
    return _diff_snapshots(before, after, project_repo)


def _opencode_referenced_paths(stdout: str, project_repo: ProjectRepo) -> list[str]:
    paths: set[str] = set(_opencode_edited_paths(stdout, project_repo))
    for line in stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        part = event.get("part")
        if not isinstance(part, dict) or part.get("type") != "tool":
            continue
        state = part.get("state")
        if not isinstance(state, dict):
            continue
        tool_input = state.get("input")
        if not isinstance(tool_input, dict):
            continue
        raw_file = tool_input.get("filePath")
        if isinstance(raw_file, str):
            paths.update(_candidate_paths_from_token(raw_file, project_repo))
        command = tool_input.get("command")
        if isinstance(command, str):
            try:
                tokens = shlex.split(command)
            except ValueError:
                tokens = command.split()
            for token in tokens:
                paths.update(_candidate_paths_from_token(token, project_repo))
    return sorted(paths)


def _candidate_paths_from_token(token: str, project_repo: ProjectRepo) -> set[str]:
    cleaned = token.strip().strip("`'\"")
    if not cleaned or cleaned.startswith("-"):
        return set()
    path = Path(cleaned)
    if path.is_absolute():
        try:
            rel = path.resolve().relative_to(project_repo.root).as_posix()
        except (OSError, ValueError):
            return set()
    else:
        rel = path.as_posix()
    try:
        target = resolve_allowed_path(project_repo, rel, require_exists=True)
    except ValueError:
        return set()
    if target.is_file():
        try:
            resolve_allowed_path(
                project_repo,
                rel,
                require_exists=True,
                require_text=True,
            )
        except ValueError:
            return set()
        return {rel}
    if target.is_dir():
        paths: set[str] = set()
        for child in target.rglob("*"):
            if not child.is_file() or child.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                child_rel = child.relative_to(project_repo.root).as_posix()
                resolve_allowed_path(
                    project_repo,
                    child_rel,
                    require_exists=True,
                    require_text=True,
                )
            except ValueError:
                continue
            paths.add(child_rel)
        return paths
    return set()


def _opencode_edited_paths(stdout: str, project_repo: ProjectRepo) -> list[str]:
    paths: set[str] = set()
    for line in stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        part = event.get("part")
        if not isinstance(part, dict) or part.get("type") != "tool":
            continue
        if part.get("tool") not in {"edit", "write"}:
            continue
        state = part.get("state")
        if not isinstance(state, dict):
            continue
        tool_input = state.get("input")
        if not isinstance(tool_input, dict):
            continue
        raw_path = tool_input.get("filePath")
        if not isinstance(raw_path, str):
            continue
        try:
            rel_path = Path(raw_path).resolve().relative_to(project_repo.root).as_posix()
            resolve_allowed_path(
                project_repo,
                rel_path,
                require_exists=True,
                require_text=True,
            )
        except (OSError, ValueError):
            continue
        paths.add(rel_path)
    return sorted(paths)


def _git_head_text(repo_root: Path, rel_path: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "show", f"HEAD:{rel_path}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _risk_for(path: str, project_repo: ProjectRepo) -> str:
    for protected in project_repo.protected_paths:
        protected_path = protected.split(":", 1)[0]
        if protected_path and path == protected_path:
            return "high"
    return "medium" if path.endswith(".py") else "low"


def _mock_transcript(packet: dict[str, Any]) -> str:
    return (
        "# opencode mock transcript\n\n"
        "opencode was not available, so MARS generated a governed mock coding "
        "packet for end-to-end validation.\n\n"
        "```json\n"
        + json.dumps(packet, ensure_ascii=False, indent=2, default=str)
        + "\n```\n"
    )


def _extract_unified_diff(text: str) -> str:
    lines = text.splitlines()
    start = next((index for index, line in enumerate(lines) if line.startswith("diff --git ") or line.startswith("--- ")), -1)
    if start < 0:
        return ""
    return "\n".join(lines[start:]).strip() + "\n"


def _diff_files(diff: str) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for line in diff.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        path = parts[3][2:] if parts[3].startswith("b/") else parts[3]
        files.append({"path": path, "type": "modified", "risk": "medium"})
    return files


def _version_for(request: RunRequest) -> str:
    attempt = request.extra.get("attempt", 1)
    try:
        return f"v{int(attempt)}"
    except (TypeError, ValueError):
        return "v1"


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
