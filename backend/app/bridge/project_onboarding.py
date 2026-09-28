"""Save user-selected Markdown as reference material in a folder project."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
import os
import yaml

from app.harness.context.folder_context import discover_folder_context
from app.harness.persistence import atomic_write_text, path_lock
from app.harness.project_workspace import FolderProject
from app.harness.runtime.project_scope import safe_scope_path
from app.settings import get_settings


def save_background(project: FolderProject, filename: str, data: bytes) -> dict[str, Any]:
    """Create one new reference, never overwrite source or promote it to rules."""
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
    # Use a .md suffix even for .MD uploads so the existing glob discovers it.
    relative = "context/" + PurePosixPath(filename).stem + ".md"
    lock = safe_scope_path(project.root, ".mars/background-upload.lock")
    with path_lock(lock):
        before = discover_folder_context(project)
        if len(before["files"]) >= settings.mars_folder_context_max_files:
            raise ValueError("项目背景文档数量已达到上限")
        if before["total_chars"] + len(text) > settings.mars_folder_context_max_chars:
            raise ValueError("项目背景文档总长度超过上限，请精简后上传")
        target = safe_scope_path(project.root, relative)
        target.parent.mkdir(exist_ok=True)
        # Exclusive creation also refuses dangling symlinks and racing uploads.
        with target.open("x", encoding="utf-8") as stream:
            try:
                stream.write(text)
                stream.flush()
            except OSError:
                target.unlink()
                raise
        try:
            record = discover_folder_context(project)
            document = next((item for item in record["files"] if item["path"] == relative), None)
            if document is None:
                raise ValueError("项目 context_files 未包含 context/，请先调整项目背景范围")
            return {key: value for key, value in document.items() if key != "content"}
        except Exception:
            target.unlink()
            raise


def bind_code_folder(project: FolderProject, path: str) -> Path:
    """Bind existing code without copying files or changing project identity."""
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
        raw["repo_path"] = str(code)
        atomic_write_text(link, yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    return code
