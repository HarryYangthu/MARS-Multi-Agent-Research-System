"""Office exports of one run (Word full report, PowerPoint summary, Excel per-iteration data).

Files are written under the app's data directory (exports/<run_id>/), never into the core's run directory.
"""
from __future__ import annotations

import time
from pathlib import Path
from types import ModuleType

from mars_app.export import context, deck, word, workbook

WRITERS = {"docx": word.write, "pptx": deck.write, "xlsx": workbook.write}
MEDIA_TYPES = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
               "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
               "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def export(fmt: str, run_dir: Path, api: ModuleType, out_root: Path) -> Path:
    if fmt not in WRITERS:
        raise ValueError(f"不支持的格式 {fmt}（可选 {', '.join(WRITERS)}）")
    ctx = context.gather(run_dir, api)
    target = out_root / run_dir.name / f"{run_dir.name}-{time.strftime('%Y%m%d-%H%M%S')}.{fmt}"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(f".tmp.{fmt}")
    WRITERS[fmt](tmp, ctx)
    tmp.replace(target)  # a failed export never leaves a truncated file behind
    return target
