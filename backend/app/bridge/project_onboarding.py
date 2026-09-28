"""Save user-selected Markdown as reference material in a folder project."""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from app.harness.context.folder_context import discover_folder_context
from app.harness.persistence import path_lock
from app.harness.project_workspace import FolderProject
from app.harness.runtime.project_scope import safe_scope_path
from app.settings import get_settings


def save_background(project: FolderProject, filename: str, data: bytes) -> dict[str, Any]:
    """Create one new reference, never overwrite source or promote it to rules."""
    if (not filename or PurePosixPath(filename).name != filename or "\\" in filename
            or filename.startswith(".") or any(ord(char) < 32 for char in filename)
            or not filename.lower().endswith(".md")):
        raise ValueError("请选择文件名不含路径的 Markdown（.md）文档")
    settings = get_settings()
    if len(data) > settings.mars_folder_context_max_chars * 4:
        raise ValueError("背景文档过大，请整理必要内容后上传")
    text = data.decode("utf-8-sig")
    if not text.strip() or "\x00" in text:
        raise ValueError("背景文档必须是非空 UTF-8 Markdown 文本")
    # Use a .md suffix even for .MD uploads so the existing glob discovers it.
    relative = "context/" + filename[:-3] + ".md"
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
