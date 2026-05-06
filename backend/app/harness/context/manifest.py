"""Write the Context Manifest into ``runs/<id>/context/``.

The manifest is the audit hook described in DESIGN §7.2: every LLM call
records what was loaded, why, and how big.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.harness.context.loader import ContextPack


def _next_version(d: Path, stem: str) -> int:
    if not d.exists():
        return 1
    existing = list(d.glob(f"{stem}.v*.json"))
    return len(existing) + 1


def write(*, run_root: Path, pack: ContextPack, agent_name: str) -> Path:
    """Persist the manifest as JSON + a human-readable snapshot.

    ``run_root`` is the directory that contains the 9 subdirs.
    """
    cdir = run_root / "context"
    cdir.mkdir(parents=True, exist_ok=True)
    v = _next_version(cdir, f"{agent_name}_context_pack")

    payload: dict[str, Any] = {
        "agent": agent_name,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "summary": pack.to_manifest_dict(),
        "tokens_estimated": _estimate_tokens(pack),
    }
    json_path = cdir / f"{agent_name}_context_pack.v{v}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = cdir / f"{agent_name}_context_snapshot.v{v}.md"
    md_path.write_text(
        _human_snapshot(payload, pack), encoding="utf-8"
    )
    return json_path


def _estimate_tokens(pack: ContextPack) -> int:
    text = pack.render()
    # ~4 chars per token rule of thumb
    return max(1, len(text) // 4)


def _human_snapshot(payload: dict[str, Any], pack: ContextPack) -> str:
    return (
        f"# Context Manifest — {payload['agent']} ({payload['timestamp']})\n\n"
        f"- Estimated tokens: {payload['tokens_estimated']}\n"
        f"- Project: {pack.project.project}\n"
        f"- Output schema: {pack.system.output_schema}\n"
        f"- KB excerpts: {len(pack.task.kb_excerpts)}\n"
        f"- Upstream handoffs: {list(pack.task.upstream_handoff.keys())}\n\n"
        "## Rendered context (first 4 KB)\n\n"
        + pack.render()[:4000]
    )
