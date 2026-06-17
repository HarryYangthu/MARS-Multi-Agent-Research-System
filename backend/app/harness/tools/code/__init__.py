"""Code tools registered through the generic tool registry."""
from __future__ import annotations

import asyncio
import difflib
import fnmatch
import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.harness.tools.config import check_commands, command_timeout_seconds, tool_config
from app.harness.tools.registry import ToolContext, ToolResult
from app.settings import repo_root

_MAX_READ_BYTES = 20_000
_TEXT_SUFFIXES = {
    ".c",
    ".cfg",
    ".cpp",
    ".cu",
    ".h",
    ".hpp",
    ".ini",
    ".json",
    ".m",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    existed: bool
    sha256: str
    content: str


async def repo_reader_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Read a source file from the configured project repo."""
    path = str(args.get("path", "")).strip()
    if not path:
        return ToolResult(ok=False, error="path is required")
    resolved = _resolve_project_file(ctx, path, must_exist=True)
    if isinstance(resolved, ToolResult):
        return resolved
    root, rel, target = resolved
    if target.suffix.lower() not in _TEXT_SUFFIXES:
        return ToolResult(ok=False, error=f"unsupported code file type '{target.suffix}'")
    raw = target.read_text(encoding="utf-8", errors="replace")
    return ToolResult(
        ok=True,
        output={
            "repo_root": str(root),
            "path": rel,
            "truncated": len(raw) > _MAX_READ_BYTES,
            "content": raw[:_MAX_READ_BYTES],
        },
        evidence_refs=[rel],
    )


async def patch_generator_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Generate or normalize a unified diff for a project file."""
    diff = str(args.get("diff", ""))
    if diff:
        return ToolResult(
            ok=True,
            output={"diff": diff, "files": [{"path": p} for p in _extract_diff_paths(diff)]},
            evidence_refs=_extract_diff_paths(diff),
        )
    path = str(args.get("path", "")).strip()
    content = args.get("content")
    if not path or not isinstance(content, str):
        return ToolResult(ok=False, error="path and content are required when diff is omitted")
    resolved = _resolve_project_file(ctx, path, must_exist=False)
    if isinstance(resolved, ToolResult):
        return resolved
    _root, rel, target = resolved
    before = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if target.exists() else []
    after = content.splitlines(keepends=True)
    rendered = "".join(
        difflib.unified_diff(
            before,
            after,
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
    )
    return ToolResult(
        ok=True,
        output={"diff": rendered, "files": [{"path": rel}]},
        evidence_refs=[rel],
    )


async def write_file_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Write a text file under the configured project repo."""
    read_only_error = _read_only_error(ctx)
    if read_only_error:
        return ToolResult(ok=False, error=read_only_error)
    path = str(args.get("path", "")).strip()
    content = args.get("content")
    if not path or not isinstance(content, str):
        return ToolResult(ok=False, error="path and string content are required")
    resolved = _resolve_project_file(ctx, path, must_exist=False)
    if isinstance(resolved, ToolResult):
        return resolved
    _root, rel, target = resolved
    snapshots = [_snapshot(rel, target)]
    if ctx.dry_run:
        return ToolResult(ok=True, output={"dry_run": True, "path": rel})
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    rollback_ref = _write_rollback(ctx, "code.write_file", snapshots)
    return ToolResult(
        ok=True,
        output={
            "path": rel,
            "sha256": _sha_text(content),
            "bytes": len(content.encode("utf-8")),
        },
        rollback_ref=rollback_ref,
        evidence_refs=[rel],
    )


async def delete_file_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Delete a file under the configured project repo.

    The registry normally returns ``requires_approval`` before this function is
    reached. This implementation is kept for an explicitly approved path.
    """
    read_only_error = _read_only_error(ctx)
    if read_only_error:
        return ToolResult(ok=False, error=read_only_error)
    path = str(args.get("path", "")).strip()
    if not path:
        return ToolResult(ok=False, error="path is required")
    resolved = _resolve_project_file(ctx, path, must_exist=True)
    if isinstance(resolved, ToolResult):
        return resolved
    _root, rel, target = resolved
    snapshots = [_snapshot(rel, target)]
    if ctx.dry_run:
        return ToolResult(ok=True, output={"dry_run": True, "path": rel})
    target.unlink()
    rollback_ref = _write_rollback(ctx, "code.delete_file", snapshots)
    return ToolResult(ok=True, output={"deleted": rel}, rollback_ref=rollback_ref, evidence_refs=[rel])


async def apply_patch_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Apply a unified diff to the configured project repo using ``git apply``."""
    version = str(args.get("version", "v1"))
    read_only_error = _read_only_error(ctx)
    if read_only_error:
        payload = _patch_result_payload(ctx, version, applied=False, error=read_only_error)
        _write_patch_result(ctx, version, payload)
        return ToolResult(ok=False, output=payload, error=read_only_error)
    diff = str(args.get("diff", ""))
    if not diff and args.get("patch_path"):
        patch_path = Path(str(args["patch_path"]))
        if not patch_path.is_absolute():
            patch_path = repo_root() / patch_path
        if not patch_path.is_file():
            return ToolResult(ok=False, error=f"patch not found: {patch_path}")
        diff = patch_path.read_text(encoding="utf-8", errors="replace")
    if not diff:
        return ToolResult(ok=False, error="diff or patch_path is required")
    root = _project_root(ctx)
    if root is None:
        return ToolResult(ok=False, error=f"project repo for '{ctx.project}' is not connected")
    files = _extract_diff_paths(diff)
    for rel in files:
        resolved = _resolve_project_file(ctx, rel, must_exist=False)
        if isinstance(resolved, ToolResult):
            return resolved
    snapshots = [_snapshot(rel, root / rel) for rel in files]
    if ctx.dry_run:
        ok = await _git_apply(root, diff, check_only=True)
        return ToolResult(ok=ok.ok, output=ok.output, error=ok.error, evidence_refs=files)
    check = await _git_apply(root, diff, check_only=True)
    if not check.ok:
        payload = _patch_result_payload(
            ctx,
            version,
            applied=False,
            error=check.error,
            command_result=check.output if isinstance(check.output, dict) else None,
            files=files,
            repo_root=root,
        )
        _write_patch_result(ctx, version, payload)
        return check
    applied = await _git_apply(root, diff, check_only=False)
    if not applied.ok:
        payload = _patch_result_payload(
            ctx,
            version,
            applied=False,
            error=applied.error,
            command_result=applied.output if isinstance(applied.output, dict) else None,
            files=files,
            repo_root=root,
        )
        _write_patch_result(ctx, version, payload)
        return applied
    rollback_ref = _write_rollback(ctx, "code.apply_patch", snapshots)
    payload = _patch_result_payload(
        ctx,
        version,
        applied=True,
        command_result=applied.output if isinstance(applied.output, dict) else None,
        files=files,
        repo_root=root,
        rollback_ref=rollback_ref,
    )
    _write_patch_result(ctx, version, payload)
    return ToolResult(
        ok=True,
        output={"applied": True, "files": [{"path": p} for p in files]},
        rollback_ref=rollback_ref,
        evidence_refs=files,
    )


async def rollback_patch_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Restore files from a rollback snapshot generated by a mutating code tool."""
    rollback_ref = str(args.get("rollback_ref", "")).strip()
    if not rollback_ref:
        return ToolResult(ok=False, error="rollback_ref is required")
    path = Path(rollback_ref)
    if not path.is_absolute():
        path = repo_root() / rollback_ref
    if not path.is_file():
        return ToolResult(ok=False, error=f"rollback snapshot not found: {rollback_ref}")
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_snapshots = data.get("snapshots", [])
    if not isinstance(raw_snapshots, list):
        return ToolResult(ok=False, error="rollback snapshot is malformed")
    root = _project_root(ctx)
    if root is None:
        return ToolResult(ok=False, error=f"project repo for '{ctx.project}' is not connected")
    restored: list[str] = []
    for item in raw_snapshots:
        if not isinstance(item, dict):
            continue
        rel = str(item.get("path", ""))
        target = (root / rel).resolve()
        if not _is_relative_to(target, root.resolve()):
            return ToolResult(ok=False, error=f"rollback path escapes repo: {rel}")
        existed = bool(item.get("existed", False))
        if existed:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(item.get("content", "")), encoding="utf-8")
        elif target.exists():
            target.unlink()
        restored.append(rel)
    return ToolResult(
        ok=True,
        output={"rolled_back": restored},
        status="success",
        events=[{"event": "tool.rolled_back", "rollback_ref": str(path)}],
        evidence_refs=restored,
    )


async def test_runner_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    return await _run_configured_commands("test", args, ctx)


async def lint_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    return await _run_configured_commands("lint", args, ctx)


async def _run_configured_commands(kind: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    root = _project_root(ctx)
    if root is None:
        return ToolResult(ok=False, error=f"project repo for '{ctx.project}' is not connected")
    tool_name = "code.test_runner" if kind == "test" else "code.lint"
    requested = str(args.get("command_id", "")).strip()
    commands = check_commands(kind)
    if requested:
        commands = tuple(cmd for cmd in commands if cmd.id == requested)
    if not commands:
        return ToolResult(ok=True, output={"commands": [], "note": f"no {kind} commands configured"})
    allowlist = tool_config(tool_name).command_allowlist
    timeout = command_timeout_seconds()
    results: list[dict[str, Any]] = []
    for command in commands:
        if not _command_allowed(command.argv, allowlist):
            return ToolResult(
                ok=False,
                error=f"{command.id} is not allowlisted for {tool_name}",
                output={"argv": list(command.argv)},
            )
        process = await asyncio.create_subprocess_exec(
            *command.argv,
            cwd=str(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return ToolResult(ok=False, error=f"{command.id} timed out after {timeout}s")
        results.append(
            {
                "id": command.id,
                "label": command.label,
                "argv": list(command.argv),
                "returncode": process.returncode,
                "stdout": stdout.decode("utf-8", errors="replace")[-4000:],
                "stderr": stderr.decode("utf-8", errors="replace")[-4000:],
            }
        )
    ok = all(item["returncode"] == 0 for item in results)
    return ToolResult(ok=ok, output={"kind": kind, "results": results})


def _command_allowed(
    argv: tuple[str, ...],
    allowlist: tuple[tuple[str, ...], ...],
) -> bool:
    if not allowlist:
        return False
    for prefix in allowlist:
        if len(argv) >= len(prefix) and argv[: len(prefix)] == prefix:
            return True
    return False


async def _git_apply(root: Path, diff: str, *, check_only: bool) -> ToolResult:
    argv = ["git", "apply", "--whitespace=nowarn"]
    if check_only:
        argv.append("--check")
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(root),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate(diff.encode("utf-8"))
    output = {
        "argv": argv,
        "returncode": process.returncode,
        "stdout": stdout.decode("utf-8", errors="replace")[-4000:],
        "stderr": stderr.decode("utf-8", errors="replace")[-4000:],
    }
    if process.returncode != 0:
        error_text = str(output["stderr"] or output["stdout"])
        return ToolResult(ok=False, error=error_text, output=output)
    return ToolResult(ok=True, output=output)


def _read_only_error(ctx: ToolContext) -> str:
    cfg = _repo_link(ctx.project)
    if bool(cfg.get("read_only", False)):
        return "project repo is read_only in repo_link.yaml"
    return ""


def _patch_result_payload(
    ctx: ToolContext,
    version: str,
    *,
    applied: bool,
    error: str | None = None,
    command_result: dict[str, Any] | None = None,
    files: list[str] | None = None,
    repo_root: Path | None = None,
    rollback_ref: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": "patch_apply_result.v1",
        "run_id": ctx.run_id,
        "project": ctx.project,
        "agent": ctx.agent,
        "version": version,
        "applied": applied,
        "mode": "git_apply",
        "repo_root": str(repo_root) if repo_root is not None else "",
        "files": files or [],
        "error": error,
        "command_result": command_result,
        "rollback_ref": rollback_ref,
        "created": datetime.now(tz=timezone.utc).isoformat(),
    }


def _write_patch_result(ctx: ToolContext, version: str, payload: dict[str, Any]) -> None:
    run_root = Path(str(ctx.extra.get("run_root"))) if ctx.extra.get("run_root") else repo_root() / "runs" / ctx.run_id
    if not ctx.run_id:
        return
    target = run_root / "coding" / f"patch.{version}.approved.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _project_root(ctx: ToolContext) -> Path | None:
    raw = ctx.project_repo_root or str(ctx.extra.get("project_repo_root", "") if ctx.extra else "")
    if raw:
        candidate = Path(raw).expanduser().resolve()
        return candidate if candidate.exists() else None
    cfg = _repo_link(ctx.project)
    repo_path = str(cfg.get("repo_path") or cfg.get("local_path") or "").strip()
    if not repo_path:
        return None
    raw_path = Path(repo_path)
    if raw_path.is_absolute():
        candidate = raw_path.resolve()
    else:
        candidate = (repo_root() / "projects" / ctx.project / raw_path).resolve()
    return candidate if candidate.exists() else None


def _resolve_project_file(
    ctx: ToolContext,
    path: str,
    *,
    must_exist: bool,
) -> tuple[Path, str, Path] | ToolResult:
    root = _project_root(ctx)
    if root is None:
        return ToolResult(ok=False, error=f"project repo for '{ctx.project}' is not connected")
    rel = Path(path)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        return ToolResult(ok=False, error="invalid project-relative path")
    rel_posix = rel.as_posix()
    if _is_ignored_path(ctx.project, rel_posix):
        return ToolResult(ok=False, error=f"path '{rel_posix}' is ignored by repo_link.yaml")
    if not _is_allowed_path(ctx.project, rel_posix):
        return ToolResult(ok=False, error=f"path '{rel_posix}' is outside allowed_paths")
    target = (root / rel).resolve()
    if not _is_relative_to(target, root.resolve()):
        return ToolResult(ok=False, error=f"path '{rel_posix}' escapes project repo")
    if must_exist and not target.is_file():
        return ToolResult(ok=False, error=f"file not found: {rel_posix}")
    return root, rel_posix, target


def _repo_link(project: str) -> dict[str, Any]:
    path = repo_root() / "projects" / project / "repo_link.yaml"
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def _is_allowed_path(project: str, rel: str) -> bool:
    allowed_raw = _repo_link(project).get("allowed_paths", [])
    if not isinstance(allowed_raw, list) or not allowed_raw:
        return True
    for item in allowed_raw:
        pattern = str(item).strip()
        if not pattern:
            continue
        if pattern.endswith("/"):
            if rel.startswith(pattern):
                return True
        elif rel == pattern or rel.startswith(pattern.rstrip("/") + "/"):
            return True
    return False


def _is_ignored_path(project: str, rel: str) -> bool:
    ignored_raw = _repo_link(project).get("ignore_patterns", [])
    if not isinstance(ignored_raw, list):
        return False
    for item in ignored_raw:
        pattern = str(item).strip()
        if not pattern:
            continue
        if pattern.endswith("/"):
            if rel.startswith(pattern):
                return True
            continue
        if fnmatch.fnmatch(rel, pattern):
            return True
    return False


def _snapshot(rel: str, target: Path) -> FileSnapshot:
    if not target.exists():
        return FileSnapshot(path=rel, existed=False, sha256="", content="")
    text = target.read_text(encoding="utf-8", errors="replace")
    return FileSnapshot(path=rel, existed=True, sha256=_sha_text(text), content=text)


def _write_rollback(ctx: ToolContext, tool_name: str, snapshots: list[FileSnapshot]) -> str | None:
    run_root = Path(str(ctx.extra.get("run_root"))) if ctx.extra.get("run_root") else repo_root() / "runs" / ctx.run_id
    if not ctx.run_id or not run_root.exists():
        return None
    target_dir = run_root / "coding" / "tool_applications"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"rollback_{uuid.uuid4().hex}.json"
    payload = {
        "schema": "tool_rollback.v1",
        "tool": tool_name,
        "run_id": ctx.run_id,
        "project": ctx.project,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "snapshots": [asdict(item) for item in snapshots],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(target)


def _extract_diff_paths(diff: str) -> list[str]:
    out: list[str] = []
    for match in re.finditer(r"^\+\+\+\s+b/(.+)$", diff, flags=re.MULTILINE):
        path = match.group(1).strip()
        if path != "/dev/null":
            out.append(path)
    for match in re.finditer(r"^---\s+a/(.+)$", diff, flags=re.MULTILINE):
        path = match.group(1).strip()
        if path != "/dev/null" and path not in out:
            out.append(path)
    return out


def _sha_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    return path == root or root in path.parents
