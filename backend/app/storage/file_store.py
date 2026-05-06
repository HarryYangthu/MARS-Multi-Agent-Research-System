"""Upload / file management under ``workspace/uploads/``."""
from __future__ import annotations

import shutil
from pathlib import Path

from app.settings import repo_root


class FileStore:
    def __init__(self, base: Path | None = None) -> None:
        self.base = base or (repo_root() / "workspace" / "uploads")
        self.base.mkdir(parents=True, exist_ok=True)

    def save_bytes(self, *, run_id: str, name: str, data: bytes) -> Path:
        target_dir = self.base / run_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / name
        target.write_bytes(data)
        return target

    def copy_into_run(self, *, src: Path, run_root: Path, subdir: str = "input") -> Path:
        dest_dir = run_root / subdir / "uploaded_files"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
        return dest
