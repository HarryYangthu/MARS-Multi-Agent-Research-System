"""Raw context storage for Write/reference context engineering.

Large tool outputs and bulky intermediate context are written outside the
prompt window under ``runs/<id>/context/raw``. Prompts receive a compact view
plus a stable relative reference.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

MAX_COMPACT_CHARS = 900
MAX_RAW_PREVIEW_CHARS = 20_000


def write_raw_context(
    *,
    run_root: Path,
    agent: str,
    label: str,
    payload: Any,
) -> str:
    raw_dir = run_root / "context" / "agents" / _safe_part(agent) / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{_safe_part(label)}.{uuid.uuid4().hex[:8]}.json"
    path = raw_dir / filename
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    legacy_dir = run_root / "context" / "raw" / _safe_part(agent)
    legacy_dir.mkdir(parents=True, exist_ok=True)
    legacy_path = legacy_dir / filename
    legacy_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return path.relative_to(run_root / "context").as_posix()


def read_raw_context(
    *,
    run_root: Path,
    raw_ref: str,
    max_chars: int = MAX_RAW_PREVIEW_CHARS,
) -> dict[str, Any]:
    path = _resolve_raw_ref(run_root=run_root, raw_ref=raw_ref)
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "raw_ref": raw_ref,
        "path": path.relative_to(run_root).as_posix(),
        "size_chars": len(text),
        "truncated": len(text) > max_chars,
        "content": text[:max_chars],
    }


def compact_tool_output(output: Any, *, max_chars: int = MAX_COMPACT_CHARS) -> Any:
    if output is None:
        return None
    if isinstance(output, str):
        return _compact_text(output, max_chars=max_chars)
    if isinstance(output, dict):
        compact: dict[str, Any] = {}
        for key, value in output.items():
            if len(compact) >= 12:
                compact["_omitted_keys"] = max(0, len(output) - len(compact))
                break
            compact[str(key)] = compact_tool_output(value, max_chars=max_chars // 2)
        return compact
    if isinstance(output, list):
        values = [compact_tool_output(item, max_chars=max_chars // 2) for item in output[:8]]
        if len(output) > 8:
            values.append({"_omitted_items": len(output) - 8})
        return values
    return output


def _compact_text(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-max_chars // 2 :]
    return f"{head}\n[... compacted {len(text) - max_chars} chars; see raw_ref ...]\n{tail}"


def _resolve_raw_ref(*, run_root: Path, raw_ref: str) -> Path:
    rel = Path(raw_ref)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise ValueError("invalid raw_ref")
    path = (run_root / "context" / rel).resolve()
    context_root = (run_root / "context").resolve()
    if path != context_root and context_root not in path.parents:
        raise ValueError("raw_ref escapes context/raw")
    legacy_root = (run_root / "context" / "raw").resolve()
    agent_root = (run_root / "context" / "agents").resolve()
    if (
        path != legacy_root
        and legacy_root not in path.parents
        and path != agent_root
        and agent_root not in path.parents
    ):
        raise ValueError("raw_ref escapes allowed context raw areas")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(raw_ref)
    return path


def _safe_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("._") or "context"
