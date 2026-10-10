"""Bounded repository discovery and source excerpts; no shell or code execution."""
from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.harness.runtime.project_scope import current_project_scope, forbidden_source_path
from app.harness.tools.code import _is_ignored_path, _project_root, _repo_link, _resolve_project_file
from app.harness.tools.project_repo import TEXT_SUFFIXES
from app.harness.tools.registry import ToolContext, ToolResult
from app.settings import repo_root


@dataclass(frozen=True)
class InspectionPolicy:
    page_size: int
    max_results: int
    max_scanned_entries: int
    max_depth: int
    max_file_bytes: int
    max_search_bytes: int
    max_read_lines: int
    max_read_chars: int
    snippet_chars: int
    exclude_directories: tuple[str, ...]


def inspection_policy() -> InspectionPolicy:
    raw = yaml.safe_load((repo_root() / "configs/code_inspection.yaml").read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("invalid code inspection policy")
    numbers = {key: raw.get(key) for key in InspectionPolicy.__annotations__ if key != "exclude_directories"}
    if any(type(value) is not int or value <= 0 for value in numbers.values()):
        raise ValueError("code inspection budgets must be positive integers")
    excluded = raw.get("exclude_directories")
    if not isinstance(excluded, list) or any(not isinstance(item, str) for item in excluded):
        raise ValueError("invalid code inspection exclusions")
    policy = InspectionPolicy(**{key: int(value) for key, value in numbers.items() if value is not None},
                              exclude_directories=tuple(excluded))
    if policy.page_size > policy.max_results:
        raise ValueError("code inspection page exceeds result budget")
    return policy


def _integer(args: dict[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    value = args.get(key, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return int(value)


def _directory_visible(ctx: ToolContext, relative: str, policy: InspectionPolicy) -> bool:
    parts = Path(relative).parts
    if (forbidden_source_path(relative) or any(part in policy.exclude_directories for part in parts)
            or _is_ignored_path(ctx.project, relative + "/")):
        return False
    scope = current_project_scope(ctx.project, ctx.run_id)
    if scope is not None:
        return any(name.startswith(relative + "/") for name in scope.readable_files)
    allowed = _repo_link(ctx.project).get("allowed_paths", [])
    return not allowed or any(relative == str(p).rstrip("/") or relative.startswith(str(p).rstrip("/") + "/")
        or str(p).startswith(relative + "/") for p in allowed)


def _scan(ctx: ToolContext, args: dict[str, Any], policy: InspectionPolicy,
          *, default_depth: int) -> tuple[Path, list[tuple[str, Path, bool]], bool]:
    root = _project_root(ctx)
    if root is None or not root.is_dir():
        raise ValueError("project code repository is not connected")
    root = root.resolve()
    path = str(args.get("path", ".")).replace("\\", "/")
    rel = Path(path)
    if rel.is_absolute() or ".." in rel.parts or ":" in path:
        raise ValueError("path must be relative to the connected code repository")
    folder = root / rel
    current = root
    for part in rel.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("repository inspection cannot traverse symbolic links")
    if not folder.is_dir() or not folder.resolve().is_relative_to(root):
        raise ValueError("repository directory is unavailable")
    relative = rel.as_posix()
    if relative != "." and not _directory_visible(ctx, relative, policy):
        raise ValueError("directory is excluded by project read policy")
    depth = _integer(args, "depth", default_depth, 1, policy.max_depth)
    pending = [(folder, 1)]
    found: list[tuple[str, Path, bool]] = []
    scanned = 0
    while pending:
        directory, level = pending.pop(0)
        children: list[tuple[str, Path, bool]] = []
        with os.scandir(directory) as entries:
            for entry in entries:
                scanned += 1
                if scanned > policy.max_scanned_entries:
                    return root, sorted(found, key=lambda item: item[0]), True
                if entry.is_symlink():
                    continue
                target = Path(entry.path)
                name = target.relative_to(root).as_posix()
                if forbidden_source_path(name):
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                if is_dir:
                    if not _directory_visible(ctx, name, policy):
                        continue
                elif entry.is_file(follow_symlinks=False) and target.suffix.lower() in TEXT_SUFFIXES:
                    if isinstance(_resolve_project_file(ctx, name, must_exist=True), ToolResult):
                        continue
                else:
                    continue
                children.append((name, target, is_dir))
        children.sort(key=lambda item: item[0])
        found.extend(children)
        if level < depth:
            pending.extend((target, level + 1) for _, target, is_dir in children if is_dir)
    return root, sorted(found, key=lambda item: item[0]), False


def _repo_list_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """List paths only; source contents enter context only after an explicit read."""
    try:
        policy = inspection_policy()
        root, files, scan_limited = _scan(ctx, args, policy, default_depth=1)
        glob = str(args.get("glob", "*"))
        selected = [(name, target, directory) for name, target, directory in files
                    if fnmatch.fnmatchcase(name, glob) or fnmatch.fnmatchcase(target.name, glob)]
        offset = _integer(args, "offset", 0, 0, policy.max_scanned_entries)
        limit = _integer(args, "limit", policy.page_size, 1, policy.max_results)
        page = selected[offset:offset + limit]
        more = offset + len(page) < len(selected)
        return ToolResult(ok=True, output={"repo_root": str(root),
            "entries": [{"path": name, "type": "directory" if directory else "file"} for name, _, directory in page],
            "truncated": more or scan_limited, "scan_limited": scan_limited,
            "next_offset": offset + len(page) if more else None,
            "hint": "Narrow path/glob when scan_limited is true; listing contains no source contents."},
            evidence_refs=[name for name, _, _ in page])
    except (OSError, ValueError) as exc:
        return ToolResult(ok=False, error=str(exc))


def _repo_search_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Literal, case-insensitive search with bounded line snippets and provenance."""
    try:
        policy = inspection_policy()
        query = args.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > policy.snippet_chars:
            raise ValueError("query must be a short nonempty literal string")
        root, files, scan_limited = _scan(ctx, args, policy, default_depth=policy.max_depth)
        offset = _integer(args, "offset", 0, 0, policy.max_scanned_entries)
        limit = _integer(args, "limit", policy.page_size, 1, policy.max_results)
        glob = str(args.get("glob", "*"))
        hits: list[dict[str, Any]] = []
        skipped_large = 0
        skipped_unreadable = 0
        matched = 0
        more = False
        read_bytes = 0
        for name, target, directory in files:
            if directory or not (fnmatch.fnmatchcase(name, glob) or fnmatch.fnmatchcase(target.name, glob)):
                continue
            try:
                size = target.stat().st_size
                if size > policy.max_file_bytes:
                    skipped_large += 1
                    continue
                if read_bytes + size > policy.max_search_bytes:
                    scan_limited = True
                    break
                raw = target.read_bytes()
                read_bytes += len(raw)
                text = raw.decode("utf-8")
            except (OSError, UnicodeError):
                skipped_unreadable += 1
                continue
            if b"\0" in raw:
                continue
            sha = hashlib.sha256(raw).hexdigest()
            for line, content in enumerate(text.splitlines(), 1):
                position = content.casefold().find(query.casefold())
                if position < 0:
                    continue
                matched += 1
                if matched <= offset:
                    continue
                if len(hits) >= limit:
                    more = True
                    break
                start = max(0, position - policy.snippet_chars // 4)
                hits.append({"path": name, "line": line, "sha256": sha,
                             "snippet": content[start:start + policy.snippet_chars]})
            if more:
                break
        return ToolResult(ok=True, output={"repo_root": str(root), "query": query, "matches": hits,
            "truncated": more or scan_limited, "scan_limited": scan_limited,
            "skipped_large_files": skipped_large, "skipped_unreadable_files": skipped_unreadable,
            "next_offset": offset + len(hits) if more else None,
            "hint": "Read only the relevant matching line ranges; no match is not proof of absence if scanning was limited."},
            evidence_refs=list(dict.fromkeys(str(hit["path"]) for hit in hits)))
    except (OSError, ValueError) as exc:
        return ToolResult(ok=False, error=str(exc))


def _read_code_fragment(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Read a bounded line range, never an automatic repository dump."""
    try:
        policy = inspection_policy()
        path = str(args.get("path", "")).replace("\\", "/")
        if not path or forbidden_source_path(path):
            raise ValueError("path is required and must identify a readable source file")
        resolved = _resolve_project_file(ctx, path, must_exist=True)
        if isinstance(resolved, ToolResult):
            return resolved
        root, relative, target = resolved
        current = root
        for part in Path(path).parts:
            current /= part
            if current.is_symlink():
                raise ValueError("repository inspection cannot traverse symbolic links")
        if target.suffix.lower() not in TEXT_SUFFIXES or target.stat().st_size > policy.max_file_bytes:
            raise ValueError("source file is unsupported or too large; narrow the investigation")
        raw = target.read_bytes()
        if b"\0" in raw:
            raise ValueError("source file must be text")
        lines = raw.decode("utf-8").splitlines(keepends=True)
        start = _integer(args, "start_line", 1, 1, max(1, len(lines)))
        end = _integer(args, "end_line", min(len(lines) or 1, start + policy.max_read_lines - 1),
                       start, start + policy.max_read_lines - 1)
        end = min(end, len(lines))
        excerpt: list[str] = []
        chars = 0
        for line in lines[start - 1:end]:
            if chars + len(line) > policy.max_read_chars:
                break
            excerpt.append(line)
            chars += len(line)
        if lines and not excerpt:
            raise ValueError("source line exceeds the excerpt budget; use literal search")
        last = start + len(excerpt) - 1
        return ToolResult(ok=True, output={"repo_root": str(root), "path": relative,
            "sha256": hashlib.sha256(raw).hexdigest(), "line_start": start, "line_end": last,
            "total_lines": len(lines), "content": "".join(excerpt), "truncated": last < len(lines),
            "next_line": last + 1 if last < len(lines) else None}, evidence_refs=[relative])
    except (OSError, ValueError, UnicodeError) as exc:
        return ToolResult(ok=False, error=str(exc))


async def repo_list_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    return await asyncio.to_thread(_repo_list_tool, args, ctx)


async def repo_search_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    return await asyncio.to_thread(_repo_search_tool, args, ctx)


async def read_code_fragment(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    return await asyncio.to_thread(_read_code_fragment, args, ctx)
