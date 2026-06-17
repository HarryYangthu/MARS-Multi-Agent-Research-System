"""Context Engineering V1 compiler.

The compiler turns the Agent-local ``ContextPack`` shape (plain strings and
upstream blocks) into audited segments, applies conservative selection and
compression, packs them under a token budget, and returns the exact messages
to send to an LLM provider.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.harness.context.manifest_v2 import (
    ContextBudget,
    ContextKind,
    ContextManifestV2,
    ContextPriority,
    ContextSegment,
    RiskFlag,
    estimate_tokens,
    message_previews,
    messages_token_estimate,
    write_manifest_v2,
)
from app.harness.context.raw_store import write_raw_context
from app.harness.llm.provider_base import Message
from app.harness.schema.frontmatter_parser import parse as parse_frontmatter
from app.settings import get_settings, repo_root

DEFAULT_MAX_TOKENS = 32_000
DEFAULT_TARGET_TOKENS = 24_000
COMPRESSION_TRIGGER_RATIO = 0.70


@dataclass(frozen=True)
class CompileContextInput:
    agent: str
    node_key: str
    project: str
    output_schema: str
    system: str
    project_context: str
    task: str
    upstream: Mapping[str, str]
    metadata: Mapping[str, Any]
    run_id: str = ""
    run_root: Path | None = None
    purpose: str = "draft"
    tool_names: Sequence[str] = ()


@dataclass(frozen=True)
class ContextCompileResult:
    messages: list[Message]
    manifest: ContextManifestV2
    manifest_path: Path | None


@dataclass(frozen=True)
class PackedContext:
    segments: list[ContextSegment]
    decisions: list[dict[str, Any]]
    used_tokens: int
    target_tokens: int
    max_tokens: int


def compile_context(
    data: CompileContextInput,
    *,
    write: bool = True,
) -> ContextCompileResult:
    max_tokens = _settings_int("mars_context_max_tokens", DEFAULT_MAX_TOKENS)
    target_tokens = _settings_int("mars_context_target_tokens", DEFAULT_TARGET_TOKENS)
    schema_template = _schema_template(data.output_schema)
    collected_segments = collect_segments(data, schema_template=schema_template)
    selected_segments = select_segments(
        collected_segments, task=data.task, tool_names=data.tool_names
    )
    compressed_segments = compress_segments(
        selected_segments,
        task=data.task,
        run_root=data.run_root,
        agent=data.agent,
        enabled=_settings_bool("mars_context_auto_compress", True),
        target_tokens=target_tokens,
    )
    packed = _pack_segments_with_decisions(
        compressed_segments, max_tokens=max_tokens, target_tokens=target_tokens
    )
    segments, diagnostics = diagnose_segments(
        packed.segments,
        task=data.task,
        tool_names=data.tool_names,
        max_tokens=max_tokens,
    )
    compression_diagnostics = _compression_diagnostics(
        before=selected_segments, after=compressed_segments
    )
    diagnostics.update(
        {
            "collected_segment_count": len(collected_segments),
            "selected_segments_before_pack": len(compressed_segments),
            "compression": compression_diagnostics,
            "compression_counts": compression_diagnostics["counts"],
            "packing": _packing_diagnostics(packed),
        }
    )
    messages = render_messages(
        data,
        segments=segments,
        schema_template=schema_template,
    )
    used_tokens = messages_token_estimate(messages)
    raw_refs = [segment.raw_ref for segment in segments if segment.raw_ref]
    manifest = ContextManifestV2(
        run_id=data.run_id,
        agent=data.agent,
        node_key=data.node_key or data.agent,
        project=data.project,
        output_schema=data.output_schema,
        purpose=data.purpose,
        budget=ContextBudget(
            max_tokens=max_tokens,
            target_tokens=target_tokens,
            used_tokens=used_tokens,
        ),
        segments=segments,
        render_order=[segment.id for segment in segments],
        messages_preview=message_previews(messages),
        diagnostics=diagnostics,
        raw_refs=[ref for ref in raw_refs if ref is not None],
    )
    path: Path | None = None
    if write and data.run_root is not None:
        path = write_manifest_v2(run_root=data.run_root, manifest=manifest)
    return ContextCompileResult(messages=messages, manifest=manifest, manifest_path=path)


def write_messages_manifest(
    *,
    run_root: Path | None,
    run_id: str,
    agent: str,
    node_key: str,
    project: str,
    output_schema: str,
    purpose: str,
    messages: list[Message],
    diagnostics_extra: Mapping[str, Any] | None = None,
) -> Path | None:
    if run_root is None:
        return None
    segments = [
        ContextSegment(
            id=f"message_{index + 1}",
            kind="system" if msg.role == "system" else "task",
            title=f"{purpose} message {index + 1}",
            source_ref=f"message:{index + 1}",
            text=msg.content,
            priority="critical" if msg.role == "system" or index == len(messages) - 1 else "high",
            selection_reason="exact pre-call message capture",
        )
        for index, msg in enumerate(messages)
    ]
    diagnostics = {
        "risk_counts": {},
        "warnings": [],
        "capture_mode": "messages",
    }
    if diagnostics_extra:
        diagnostics.update(dict(diagnostics_extra))
    manifest = ContextManifestV2(
        run_id=run_id,
        agent=agent,
        node_key=node_key or agent,
        project=project,
        output_schema=output_schema,
        purpose=purpose,
        budget=ContextBudget(
            max_tokens=_settings_int("mars_context_max_tokens", DEFAULT_MAX_TOKENS),
            target_tokens=_settings_int("mars_context_target_tokens", DEFAULT_TARGET_TOKENS),
            used_tokens=messages_token_estimate(messages),
        ),
        segments=segments,
        render_order=[segment.id for segment in segments],
        messages_preview=message_previews(messages),
        diagnostics=diagnostics,
        raw_refs=[],
    )
    return write_manifest_v2(run_root=run_root, manifest=manifest)


def collect_segments(
    data: CompileContextInput,
    *,
    schema_template: str,
) -> list[ContextSegment]:
    segments: list[ContextSegment] = []
    segments.append(
        _segment(
            kind="system",
            title="Agent system guidance",
            source_ref=f"agent:{data.agent}:system",
            text=data.system,
            priority="critical",
            reason="agent role, hard constraints, and output contract",
        )
    )
    if schema_template:
        segments.append(
            _segment(
                kind="schema",
                title=f"Reference template {data.output_schema}",
                source_ref=f"templates/artifacts/{data.output_schema}.md",
                text=schema_template,
                priority="critical",
                reason="schema template keeps frontmatter and body format stable",
            )
        )
    if data.project_context:
        segments.append(
            _segment(
                kind="project",
                title="Project context",
                source_ref=f"project:{data.project}",
                text=data.project_context,
                priority="high",
                reason="project rules and domain constraints for this run",
            )
        )
    for label, content in data.upstream.items():
        kind = _kind_for_upstream(label)
        segments.append(
            _segment(
                kind=kind,
                title=label,
                source_ref=label,
                text=content,
                priority=_priority_for_kind(kind),
                reason=_selection_reason_for_kind(kind),
            )
        )
    if data.tool_names:
        selected_tools = _select_tool_names(data.tool_names, data.task)
        reason = (
            "all configured tools injected"
            if len(selected_tools) == len(data.tool_names)
            else "tool definitions selected by task keyword overlap"
        )
        segments.append(
            _segment(
                kind="tool",
                title="Configured tools",
                source_ref="configs/agents.yaml",
                text="\n".join(f"- {name}" for name in selected_tools),
                priority="medium",
                reason=reason,
            )
        )
    if data.task:
        segments.append(
            _segment(
                kind="task",
                title="Current user task",
                source_ref="input/user_request.md",
                text=data.task,
                priority="critical",
                reason="current task must remain in the tail of the prompt",
            )
        )
    return segments


def select_segments(
    segments: list[ContextSegment],
    *,
    task: str,
    tool_names: Sequence[str],
) -> list[ContextSegment]:
    selected: list[ContextSegment] = []
    kb_segments = [segment for segment in segments if segment.kind == "kb"]
    for segment in segments:
        if segment.kind != "kb":
            selected.append(segment)
            continue
        if len(kb_segments) <= 5 or _overlap_score(task, segment.text) > 0:
            selected.append(
                replace(
                    segment,
                    selection_reason=segment.selection_reason + "; selected by relevance filter",
                )
            )
    if len(tool_names) > 10:
        selected = [
            _add_risk(segment, "confusion") if segment.kind == "tool" else segment
            for segment in selected
        ]
    return selected


def compress_segments(
    segments: list[ContextSegment],
    *,
    task: str,
    run_root: Path | None,
    agent: str,
    enabled: bool,
    target_tokens: int,
) -> list[ContextSegment]:
    total = sum(segment.tokens_estimated for segment in segments)
    should_compress = enabled and total >= int(target_tokens * COMPRESSION_TRIGGER_RATIO)
    compressed: list[ContextSegment] = []
    for segment in segments:
        if segment.priority == "critical":
            compressed.append(segment)
            continue
        if segment.kind == "tool" and segment.tokens_estimated > 300:
            compressed.append(_reference_segment(segment, run_root=run_root, agent=agent))
            continue
        if segment.kind == "upstream" and (should_compress or segment.tokens_estimated > 900):
            compressed.append(_summary_segment(segment))
            continue
        if segment.kind == "kb" and segment.tokens_estimated > 350:
            compressed.append(_relevance_segment(segment, task=task))
            continue
        if segment.kind in {"project", "self_context"} and segment.tokens_estimated > 1200:
            compressed.append(_trim_segment(segment, max_chars=4800))
            continue
        compressed.append(segment)
    return compressed


def pack_segments(
    segments: list[ContextSegment],
    *,
    max_tokens: int,
    target_tokens: int,
) -> list[ContextSegment]:
    return _pack_segments_with_decisions(
        segments, max_tokens=max_tokens, target_tokens=target_tokens
    ).segments


def _pack_segments_with_decisions(
    segments: list[ContextSegment],
    *,
    max_tokens: int,
    target_tokens: int,
) -> PackedContext:
    packed: list[ContextSegment] = []
    decisions: list[dict[str, Any]] = []
    used = 0
    ordered = sorted(segments, key=_pack_sort_key)
    for segment in ordered:
        tokens = segment.tokens_estimated
        if segment.priority == "critical":
            packed.append(segment)
            used += tokens
            if used > target_tokens:
                decisions.append(
                    _packing_decision(
                        segment,
                        action="keep_critical_over_target",
                        reason="critical segment cannot be dropped by the packer",
                        before_tokens=tokens,
                        after_tokens=tokens,
                    )
                )
            continue
        if used + tokens <= target_tokens:
            packed.append(segment)
            used += tokens
            continue
        if segment.priority == "low":
            decisions.append(
                _packing_decision(
                    segment,
                    action="drop_low_priority_over_target",
                    reason="low priority segment exceeded target budget",
                    before_tokens=tokens,
                    after_tokens=0,
                )
            )
            continue
        trimmed = _trim_segment(segment, max_chars=max(800, (max_tokens - used) * 4))
        if used + trimmed.tokens_estimated <= max_tokens:
            packed.append(trimmed)
            used += trimmed.tokens_estimated
            decisions.append(
                _packing_decision(
                    segment,
                    action=(
                        "trim_to_fit"
                        if trimmed.tokens_estimated < tokens
                        else "include_over_target"
                    ),
                    reason="segment exceeded target budget but still fit max budget",
                    before_tokens=tokens,
                    after_tokens=trimmed.tokens_estimated,
                )
            )
            continue
        decisions.append(
            _packing_decision(
                segment,
                action="drop_over_max_budget",
                reason="segment could not fit max budget after deterministic trim",
                before_tokens=tokens,
                after_tokens=0,
            )
        )
    ordered_packed = _render_order(packed)
    return PackedContext(
        segments=ordered_packed,
        decisions=decisions,
        used_tokens=sum(segment.tokens_estimated for segment in ordered_packed),
        target_tokens=target_tokens,
        max_tokens=max_tokens,
    )


def diagnose_segments(
    segments: list[ContextSegment],
    *,
    task: str,
    tool_names: Sequence[str],
    max_tokens: int,
) -> tuple[list[ContextSegment], dict[str, Any]]:
    warnings: list[str] = []
    out = list(segments)
    if len([segment for segment in out if segment.kind == "kb"]) > 5:
        warnings.append("kb_segment_count_high")
        out = [_add_risk(segment, "confusion") if segment.kind == "kb" else segment for segment in out]
    if len(tool_names) > 10:
        warnings.append("tool_count_high")
    if sum(segment.tokens_estimated for segment in out if segment.kind == "upstream") > max_tokens * 0.25:
        warnings.append("upstream_tokens_high")
        out = [
            _add_risk(segment, "distraction") if segment.kind == "upstream" else segment
            for segment in out
        ]
    if _has_version_clash(out):
        warnings.append("multiple_versions_in_context")
        out = [_add_risk(segment, "clash") if segment.kind == "upstream" else segment for segment in out]
    if _has_unverified_memory(out):
        warnings.append("unverified_memory")
        out = [_add_risk(segment, "poisoning") if segment.kind == "memory" else segment for segment in out]
    if estimate_tokens("\n".join(segment.text for segment in out)) > max_tokens // 2:
        out = _mark_lost_middle(out)
    risk_counts = Counter(flag for segment in out for flag in segment.risk_flags)
    diagnostics: dict[str, Any] = {
        "risk_counts": dict(risk_counts),
        "warnings": warnings,
        "query_terms": sorted(_terms(task))[:20],
        "segment_count": len(out),
    }
    return out, diagnostics


def render_messages(
    data: CompileContextInput,
    *,
    segments: list[ContextSegment],
    schema_template: str,
) -> list[Message]:
    system_text = _system_prompt(data, schema_template=schema_template)
    messages = [Message(role="system", content=system_text)]
    project_text = _combined_text(segments, kinds={"project"})
    if project_text:
        messages.append(Message(role="system", content=project_text))
    for segment in segments:
        if segment.kind in {"system", "schema", "project", "task"}:
            continue
        messages.append(
            Message(
                role="user",
                content=f"[{segment.kind}:{segment.title}]\n{segment.text}",
            )
        )
    task_text = _combined_text(segments, kinds={"task"})
    if task_text:
        messages.append(Message(role="user", content=task_text))
    return messages


def summarize_handoff_artifact(*, text: str, source_ref: str) -> str:
    """Create a compact default handoff while preserving provenance."""
    approved = source_ref.endswith(".approved.md")
    lines = [
        "# Agent handoff summary",
        f"- Source: `{source_ref}`",
        f"- Approved: {'yes' if approved else 'no'}",
    ]
    try:
        parsed = parse_frontmatter(text)
    except Exception:
        parsed = None
    if parsed is not None:
        schema = parsed.metadata.get("schema")
        if schema:
            lines.append(f"- Schema: `{schema}`")
        visible_keys = [
            key
            for key in parsed.metadata.keys()
            if key not in {"debate_transcript_full", "debate_transcript_excerpt"}
        ][:10]
        if visible_keys:
            lines.append("- Frontmatter keys: " + ", ".join(f"`{key}`" for key in visible_keys))
        body = parsed.body
    else:
        body = text
    lines.extend(["", "## Body excerpt", body[:2200]])
    if len(body) > 2200:
        lines.append("\n[... handoff body omitted; original artifact remains on disk ...]")
    return "\n".join(lines)


def _segment(
    *,
    kind: ContextKind,
    title: str,
    source_ref: str,
    text: str,
    priority: ContextPriority,
    reason: str,
) -> ContextSegment:
    return ContextSegment(
        id=_segment_id(kind=kind, title=title),
        kind=kind,
        title=title,
        source_ref=source_ref,
        text=text,
        priority=priority,
        selection_reason=reason,
    )


def _segment_id(*, kind: str, title: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", title.strip()).strip("._")
    return f"{kind}:{clean or 'segment'}"


def _kind_for_upstream(label: str) -> ContextKind:
    lower = label.lower()
    if "self_context" in lower:
        return "self_context"
    if "research_sites" in lower:
        return "research_site"
    if "kb" in lower or "excerpt" in lower:
        return "kb"
    if "tool" in lower:
        return "tool"
    if "memory" in lower:
        return "memory"
    return "upstream"


def _priority_for_kind(kind: ContextKind) -> ContextPriority:
    if kind in {"self_context", "upstream", "kb"}:
        return "high"
    if kind in {"tool", "memory"}:
        return "medium"
    return "low"


def _selection_reason_for_kind(kind: ContextKind) -> str:
    reasons = {
        "self_context": "agent-owned self context loaded for this stage",
        "research_site": "configured optional research source list",
        "kb": "retrieved local knowledge relevant to the task",
        "tool": "tool observation or tool definition available to the agent",
        "memory": "long-lived memory recalled for this agent",
        "upstream": "approved upstream handoff from an earlier stage",
    }
    return reasons.get(kind, "included by context collector")


def _select_tool_names(tool_names: Sequence[str], task: str) -> list[str]:
    names = list(tool_names)
    if len(names) <= 10:
        return names
    terms = _terms(task)
    scored = [
        (len(terms & _terms(name.replace(".", " "))), index, name)
        for index, name in enumerate(names)
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [name for _score, _index, name in scored[:10]]


def _reference_segment(
    segment: ContextSegment,
    *,
    run_root: Path | None,
    agent: str,
) -> ContextSegment:
    if run_root is None:
        return _trim_segment(segment, max_chars=1200)
    raw_ref = write_raw_context(
        run_root=run_root,
        agent=agent,
        label=segment.title,
        payload={
            "segment_id": segment.id,
            "kind": segment.kind,
            "source_ref": segment.source_ref,
            "text": segment.text,
        },
    )
    text = (
        f"[reference: {raw_ref}]\n"
        f"Original segment `{segment.title}` was {len(segment.text)} chars. "
        f"Use the reference only if this stage truly needs the raw payload.\n\n"
        + segment.text[:700]
    )
    return replace(segment, text=text, compression="reference", raw_ref=raw_ref)


def _summary_segment(segment: ContextSegment) -> ContextSegment:
    text = summarize_handoff_artifact(text=segment.text, source_ref=segment.source_ref)
    return replace(segment, text=text, compression="summary")


def _relevance_segment(segment: ContextSegment, *, task: str) -> ContextSegment:
    terms = _terms(task)
    paragraphs = [part.strip() for part in segment.text.split("\n\n") if part.strip()]
    scored = [
        (len(terms & _terms(paragraph)), index, paragraph)
        for index, paragraph in enumerate(paragraphs)
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    kept = [paragraph for score, _index, paragraph in scored[:4] if score > 0]
    if not kept:
        kept = paragraphs[:2]
    return replace(
        segment,
        text="\n\n".join(kept)[:1800],
        compression="relevance_prune",
    )


def _trim_segment(segment: ContextSegment, *, max_chars: int) -> ContextSegment:
    if len(segment.text) <= max_chars:
        return segment
    head = segment.text[: max_chars // 2]
    tail = segment.text[-max_chars // 2 :]
    text = f"{head}\n[... trimmed {len(segment.text) - max_chars} chars ...]\n{tail}"
    return replace(segment, text=text, compression="trimmed")


def _compression_diagnostics(
    *,
    before: list[ContextSegment],
    after: list[ContextSegment],
) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for original, compressed in zip(before, after, strict=False):
        if compressed.compression == "none":
            continue
        counts[compressed.compression] += 1
        decisions.append(
            {
                "segment_id": compressed.id,
                "title": compressed.title,
                "kind": compressed.kind,
                "strategy": compressed.compression,
                "before_tokens": original.tokens_estimated,
                "after_tokens": compressed.tokens_estimated,
                "raw_ref": compressed.raw_ref,
            }
        )
    return {"counts": dict(counts), "decisions": decisions}


def _packing_decision(
    segment: ContextSegment,
    *,
    action: str,
    reason: str,
    before_tokens: int,
    after_tokens: int,
) -> dict[str, Any]:
    return {
        "segment_id": segment.id,
        "title": segment.title,
        "kind": segment.kind,
        "priority": segment.priority,
        "action": action,
        "reason": reason,
        "before_tokens": before_tokens,
        "after_tokens": after_tokens,
        "source_ref": segment.source_ref,
    }


def _packing_diagnostics(packed: PackedContext) -> dict[str, Any]:
    dropped = [
        decision
        for decision in packed.decisions
        if str(decision.get("action", "")).startswith("drop")
    ]
    trimmed = [
        decision
        for decision in packed.decisions
        if decision.get("action") == "trim_to_fit"
    ]
    return {
        "used_tokens": packed.used_tokens,
        "target_tokens": packed.target_tokens,
        "max_tokens": packed.max_tokens,
        "over_target": packed.used_tokens > packed.target_tokens,
        "over_max": packed.used_tokens > packed.max_tokens,
        "decision_count": len(packed.decisions),
        "dropped": len(dropped),
        "trimmed": len(trimmed),
        "decisions": packed.decisions,
    }


def _pack_sort_key(segment: ContextSegment) -> tuple[int, int]:
    priority_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    kind_rank = {
        "system": 0,
        "schema": 1,
        "project": 2,
        "self_context": 3,
        "upstream": 4,
        "kb": 5,
        "tool": 6,
        "memory": 7,
        "research_site": 8,
        "task": 9,
    }
    return (priority_rank[segment.priority], kind_rank.get(segment.kind, 99))


def _render_order(segments: list[ContextSegment]) -> list[ContextSegment]:
    head = [segment for segment in segments if segment.kind in {"system", "schema", "project"}]
    tail = [segment for segment in segments if segment.kind == "task"]
    middle = [
        segment
        for segment in segments
        if segment.kind not in {"system", "schema", "project", "task"}
    ]
    return head + middle + tail


def _system_prompt(data: CompileContextInput, *, schema_template: str) -> str:
    return (
        data.system
        + "\n\n"
        + f"You are the **{data.agent}** Agent in a research pipeline. "
        + f"Your output MUST validate against the JSON Schema named `{data.output_schema}`.\n\n"
        + "FORMAT RULES (strict):\n"
        + "1. Reply with a single markdown document.\n"
        + "2. The very first line of your reply MUST be `---` (no leading prose, no code fences).\n"
        + "3. The document begins with YAML frontmatter delimited by `---` lines.\n"
        + "4. The frontmatter MUST contain every required field for the schema.\n"
        + "5. Below the closing `---` write the body in markdown.\n"
        + "6. NEVER wrap the whole document in ```markdown ... ``` fences.\n\n"
        + "LANGUAGE RULES (strict):\n"
        + "1. All human-facing prose MUST be written in Simplified Chinese.\n"
        + "2. YAML string values that are meant to be read by humans MUST also be Simplified Chinese.\n"
        + "3. Preserve technical identifiers, schema IDs, file paths, metric names, model names, code symbols, and URLs.\n"
        + "4. If upstream artifacts are English, translate the explanation into Chinese instead of copying long English prose.\n\n"
        + (
            "REFERENCE TEMPLATE for this schema (copy the structure, replace values):\n\n"
            + schema_template
            + "\n\n"
            if schema_template
            else ""
        )
        + "Now produce a fresh, schema-conforming Chinese document for the user's task below."
    )


def _combined_text(segments: list[ContextSegment], *, kinds: set[str]) -> str:
    return "\n\n".join(segment.text for segment in segments if segment.kind in kinds)


def _schema_template(output_schema: str) -> str:
    path = repo_root() / "templates" / "artifacts" / f"{output_schema}.md"
    try:
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError:
        return ""


def _terms(text: str) -> set[str]:
    return {term.lower() for term in re.findall(r"[A-Za-z0-9_]+", text)}


def _overlap_score(query: str, text: str) -> int:
    return len(_terms(query) & _terms(text))


def _add_risk(segment: ContextSegment, risk: RiskFlag) -> ContextSegment:
    if risk in segment.risk_flags:
        return segment
    return replace(segment, risk_flags=[*segment.risk_flags, risk])


def _has_version_clash(segments: list[ContextSegment]) -> bool:
    names = [
        re.sub(r"\.(v\d+|approved)\.md$", "", segment.source_ref)
        for segment in segments
        if segment.kind == "upstream"
    ]
    return len(names) != len(set(names))


def _has_unverified_memory(segments: list[ContextSegment]) -> bool:
    return any(
        segment.kind == "memory"
        and not segment.source_ref
        and "source" not in segment.text.lower()
        for segment in segments
    )


def _mark_lost_middle(segments: list[ContextSegment]) -> list[ContextSegment]:
    out: list[ContextSegment] = []
    last_index = len(segments) - 1
    for index, segment in enumerate(segments):
        if segment.priority == "critical" and index not in {0, 1, last_index}:
            out.append(_add_risk(segment, "lost_in_middle"))
        else:
            out.append(segment)
    return out


def _settings_int(name: str, default: int) -> int:
    value = getattr(get_settings(), name, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _settings_bool(name: str, default: bool) -> bool:
    value = getattr(get_settings(), name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default
