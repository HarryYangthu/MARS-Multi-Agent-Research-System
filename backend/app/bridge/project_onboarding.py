"""Save project background as README reference material and bind baseline code."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
import os
import uuid
import yaml

from app.harness.context.folder_context import discover_folder_context
from app.harness.persistence import atomic_write_text, fsync_directory, path_lock
from app.harness.project_workspace import FolderProject
from app.harness.runtime.project_scope import safe_scope_path
from app.settings import get_settings


def save_background(project: FolderProject, filename: str, data: bytes) -> dict[str, Any]:
    """Replace README atomically, preserving its previous bytes outside context."""
    if (not filename or PurePosixPath(filename).name != filename or "\\" in filename
            or filename.startswith(".") or any(ord(char) < 32 for char in filename)
            or PurePosixPath(filename).suffix.lower() not in {".md", ".txt"}):
        raise ValueError("请选择文件名不含路径的 Markdown 或 TXT 文本")
    settings = get_settings()
    if len(data) > settings.mars_folder_context_max_chars * 4:
        raise ValueError("背景文档过大，请整理必要内容后上传")
    text = data.decode("utf-8-sig")
    if not text.strip() or "\x00" in text:
        raise ValueError("背景文档必须是非空 UTF-8 文本")
    # The incoming name never grants instruction authority, even AGENTS.md.
    relative = "README.md"
    lock = safe_scope_path(project.root, ".mars/background-upload.lock")
    with path_lock(lock):
        target = safe_scope_path(project.root, relative)
        # Account for a replacement, and permit replacing an oversized README.
        before = discover_folder_context(project, exclude_paths=frozenset({relative}))
        if len(before["files"]) + 1 > settings.mars_folder_context_max_files:
            raise ValueError("项目背景文档数量已达到上限")
        if before["total_chars"] + len(text) > settings.mars_folder_context_max_chars:
            raise ValueError("项目背景文档总长度超过上限，请精简后上传")
        previous: Path | None = None
        if target.exists():
            previous = safe_scope_path(project.root, f".mars/background-history/README.{uuid.uuid4().hex}.md")
            previous.parent.mkdir(exist_ok=True)
            fsync_directory(previous.parent.parent)
            # Back up bytes without normalizing line endings or a UTF-8 BOM.
            created = False
            try:
                with previous.open("xb") as stream:
                    created = True
                    stream.write(target.read_bytes())
                    stream.flush()
                    os.fsync(stream.fileno())
                fsync_directory(previous.parent)
            except Exception:
                if created:
                    previous.unlink(missing_ok=True)
                raise
        try:
            atomic_write_text(target, text)
            record = discover_folder_context(project)
            document = next((item for item in record["files"] if item["path"] == relative), None)
            if document is None:
                raise ValueError("项目 context_files 未包含 README.md，请先调整项目背景范围")
            result = {key: value for key, value in document.items() if key != "content"}
            if previous is not None:
                result["previous_path"] = previous.relative_to(project.root).as_posix()
            return result
        except Exception:
            if previous is not None:
                os.replace(previous, target)
                fsync_directory(previous.parent)
            else:
                target.unlink(missing_ok=True)
            fsync_directory(target.parent)
            raise


def bind_code_folder(project: FolderProject, path: str, *, role: str = "simulation_baseline") -> Path:
    """Bind existing code without copying files or changing project identity.

    ``simulation_baseline`` is the read-only research baseline (default).
    ``ainative`` binds a generated AI Native working repo that agents may
    write to; the previous baseline path is preserved as ``baseline_repo_path``.
    """
    if role not in {"simulation_baseline", "ainative"}:
        raise ValueError(f"未知的代码仓角色：{role}")
    candidate = Path(path).expanduser()
    if not path.strip() or not candidate.is_absolute():
        raise ValueError("请选择代码工程文件夹的完整路径")
    code = candidate.resolve(strict=True)
    if not code.is_dir():
        raise ValueError("代码工程必须是已存在的文件夹")
    with os.scandir(code):
        pass  # Check accessibility without writing to the selected repository.
    link = safe_scope_path(project.root, ".mars/repo_link.yaml", must_exist=True)
    with path_lock(safe_scope_path(project.root, ".mars/repo-link.lock")):
        raw = yaml.safe_load(link.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("project") != project.name:
            raise ValueError("代码关联配置与当前项目不匹配")
        if raw.get("repo_mode") != "local_path":
            raise ValueError("当前代码关联不是本地文件夹模式，请保留现有工程配置")
        if role == "ainative" and raw.get("repo_role") == "simulation_baseline":
            raw["baseline_repo_path"] = str(raw.get("repo_path", ""))
        raw["repo_path"] = str(code)
        raw["repo_role"] = role
        raw["read_only"] = role != "ainative"
        atomic_write_text(link, yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    return code
