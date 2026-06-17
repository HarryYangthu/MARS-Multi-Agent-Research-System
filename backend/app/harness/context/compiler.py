"""Legacy context compiler used as a compatibility fallback.

Context Engineering V1's authoritative pre-call path lives in
``app.harness.context.engine``. This module is intentionally kept small because
``ContextPack.to_messages()`` still uses it when the V1 engine is unavailable,
and older tests/tools still read its ``context_compile_manifest.v1`` payload.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.harness.llm.provider_base import Message
from app.harness.schema.frontmatter_parser import parse as parse_frontmatter


@dataclass(frozen=True)
class CompiledContext:
    messages: list[Message]
    manifest: dict[str, Any]


def compile_agent_context(
    *,
    system: str,
    project: str,
    task: str,
    upstream: dict[str, str],
    agent_name: str,
    output_schema: str,
    schema_template: str = "",
) -> CompiledContext:
    """Compile legacy messages and a v1 fallback manifest from the same inputs."""
    sys_text = _system_prompt(
        base=system,
        agent_name=agent_name,
        output_schema=output_schema,
        schema_template=schema_template,
    )
    messages = [Message(role="system", content=sys_text)]
    source_refs: list[dict[str, Any]] = [
        _source_ref(role="system", source="system_prompt", content=sys_text)
    ]
    if project:
        messages.append(Message(role="system", content=project))
        source_refs.append(_source_ref(role="system", source="project", content=project))
    for label, content in upstream.items():
        distilled = distill_handoff(label=label, content=content)
        messages.append(Message(role="user", content=distilled))
        source_refs.append(
            _source_ref(role="user", source=f"upstream:{label}", content=distilled)
        )
    if task:
        messages.append(Message(role="user", content=task))
        source_refs.append(_source_ref(role="user", source="task", content=task))

    manifest = {
        "schema": "context_compile_manifest.v1",
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "agent": agent_name,
        "output_schema": output_schema,
        "message_count": len(messages),
        "token_estimate": sum(_estimate_tokens(message.content) for message in messages),
        "messages": source_refs,
    }
    return CompiledContext(messages=messages, manifest=manifest)


def distill_handoff(*, label: str, content: str, body_limit: int = 900) -> str:
    """Turn an upstream artifact into a deterministic handoff packet."""
    frontmatter: dict[str, Any] = {}
    body = content
    try:
        parsed = parse_frontmatter(content)
        frontmatter = parsed.metadata
        body = parsed.body
    except Exception:
        frontmatter = {}
    key_order = (
        "schema",
        "project",
        "agent",
        "run_id",
        "status",
        "research_question",
        "hypothesis",
        "novelty",
        "summary",
        "fingerprint_hash",
    )
    meta_lines = [
        f"- {key}: {frontmatter[key]}"
        for key in key_order
        if key in frontmatter and frontmatter[key] not in (None, "")
    ]
    body_summary = _squash(body, limit=body_limit)
    parts = [
        f"[upstream:{label}]",
        f"source: {label}",
        "frontmatter:",
        *(meta_lines or ["- unavailable"]),
        "body_summary:",
        body_summary or "(empty)",
    ]
    return "\n".join(parts)


def load_schema_template(output_schema: str) -> str:
    try:
        from app.settings import repo_root

        tpl_path = repo_root() / "templates" / "artifacts" / f"{output_schema}.md"
        return tpl_path.read_text(encoding="utf-8") if tpl_path.exists() else ""
    except Exception:
        return ""


def _system_prompt(
    *,
    base: str,
    agent_name: str,
    output_schema: str,
    schema_template: str,
) -> str:
    return (
        base
        + "\n\n"
        + f"You are the **{agent_name}** Agent in a research pipeline. "
        + f"Your output MUST validate against the JSON Schema named `{output_schema}`.\n\n"
        + "FORMAT RULES (strict):\n"
        + "1. Reply with a single markdown document.\n"
        + "2. The very first line of your reply MUST be `---` (no leading prose, no code fences).\n"
        + "3. The document begins with YAML frontmatter delimited by `---` lines.\n"
        + "4. The frontmatter MUST contain every required field for the schema.\n"
        + "5. Below the closing `---` write the body in markdown.\n"
        + "6. NEVER wrap the whole document in ```markdown ... ``` fences.\n\n"
        + "LANGUAGE RULES (strict):\n"
        + "1. All human-facing prose MUST be written in Simplified Chinese.\n"
        + "2. YAML string values that are meant to be read by humans, such as "
        + "`research_question`, `hypothesis`, `novelty`, `rationale`, "
        + "`summary`, and report text, MUST also be Simplified Chinese.\n"
        + "3. Preserve technical identifiers, schema IDs, file paths, metric names, "
        + "model names, code symbols, and URLs in their original form.\n"
        + "4. If upstream artifacts are English, translate the explanation into "
        + "Chinese instead of copying long English prose.\n\n"
        + (
            "REFERENCE TEMPLATE for this schema (copy the structure, replace values):\n\n"
            + schema_template
            + "\n\n"
            if schema_template
            else ""
        )
        + "Now produce a fresh, schema-conforming Chinese document for the user's task below."
    )


def _source_ref(*, role: str, source: str, content: str) -> dict[str, Any]:
    preview = _squash(content, limit=180)
    return {
        "role": role,
        "source": source,
        "content_hash": _hash(content),
        "token_estimate": _estimate_tokens(content),
        "preview": preview,
        "compressed": len(content) > len(preview),
    }


def _squash(text: str, *, limit: int) -> str:
    cleaned = " ".join(text.strip().split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."


def _hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def write_compiled_manifest(
    *, run_root: Path, manifest: dict[str, Any], agent_name: str
) -> Path:
    cdir = run_root / "context"
    cdir.mkdir(parents=True, exist_ok=True)
    existing = list(cdir.glob(f"{agent_name}_compiled_context.v*.json"))
    path = cdir / f"{agent_name}_compiled_context.v{len(existing) + 1}.json"
    import json

    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
