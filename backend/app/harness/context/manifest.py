"""Write the Context Manifest into ``runs/<id>/context/``.

The manifest is the audit hook described in DESIGN §7.2: every LLM call
records what was loaded, why, and how big.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.harness.context.compiler import compile_agent_context, load_schema_template
from app.harness.context.loader import ContextPack


def _next_version(d: Path, stem: str) -> int:
    if not d.exists():
        return 2
    versions: list[int] = []
    for path in d.glob(f"{stem}.v*.json"):
        suffix = path.name.removeprefix(f"{stem}.v").removesuffix(".json")
        try:
            versions.append(int(suffix))
        except ValueError:
            continue
    return max(2, max(versions, default=1) + 1)


def write(*, run_root: Path, pack: ContextPack, agent_name: str) -> Path:
    """Persist the manifest as JSON + a human-readable snapshot.

    ``run_root`` is the directory that contains the 9 subdirs.
    """
    cdir = run_root / "context"
    cdir.mkdir(parents=True, exist_ok=True)
    v = _next_version(cdir, f"{agent_name}_context_pack")
    compiled = compile_agent_context(
        system=pack.system.render(),
        project=pack.project.render(),
        task=pack.task.render(),
        upstream=pack.task.upstream_handoff,
        agent_name=agent_name,
        output_schema=pack.system.output_schema,
        schema_template=load_schema_template(pack.system.output_schema),
    )

    payload: dict[str, Any] = {
        "agent": agent_name,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "summary": _summary_from_compiled(compiled.manifest, pack=pack),
        "tokens_estimated": int(compiled.manifest.get("token_estimate", 0) or 0),
        "compiled_manifest": compiled.manifest,
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


def _summary_from_compiled(
    compiled_manifest: dict[str, Any],
    *,
    pack: ContextPack,
) -> dict[str, Any]:
    messages_raw = compiled_manifest.get("messages", [])
    messages = messages_raw if isinstance(messages_raw, list) else []
    return {
        "source": "compiler",
        "compiler_schema": compiled_manifest.get("schema"),
        "message_count": compiled_manifest.get("message_count"),
        "token_estimate": compiled_manifest.get("token_estimate"),
        "messages": messages,
        "system": {
            "agent_role": pack.system.agent_role,
            "output_schema": pack.system.output_schema,
            "hard_constraints": list(pack.system.hard_constraints),
        },
        "project": {
            "name": pack.project.project,
            "agents_md_chars": len(pack.project.agents_md),
            "context_docs": [name for name, _content in pack.project.context_docs],
        },
        "task": {
            "user_request_chars": len(pack.task.user_request),
            "upstream_handoff_keys": list(pack.task.upstream_handoff.keys()),
        },
        "metadata": dict(pack.metadata),
    }


def _human_snapshot(payload: dict[str, Any], pack: ContextPack) -> str:
    compiled = payload.get("compiled_manifest", {})
    messages = compiled.get("messages", []) if isinstance(compiled, dict) else []
    previews = "\n".join(
        "- {role} {source}: {preview}".format(
            role=item.get("role", ""),
            source=item.get("source", ""),
            preview=item.get("preview", ""),
        )
        for item in messages[:8]
        if isinstance(item, dict)
    )
    return (
        f"# Context Manifest — {payload['agent']} ({payload['timestamp']})\n\n"
        f"- Estimated tokens: {payload['tokens_estimated']}\n"
        f"- Source: compiler\n"
        f"- Project: {pack.project.project}\n"
        f"- Output schema: {pack.system.output_schema}\n"
        f"- KB excerpts: {len(pack.task.kb_excerpts)}\n"
        f"- Upstream handoffs: {list(pack.task.upstream_handoff.keys())}\n\n"
        "## Message Preview\n\n"
        + (previews or "- (empty)")
    )
