"""V1 Idea Agent self-context, research pack, and quality checks."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agents.base import ContextPack, RunRequest
from app.harness.context.project_layer import build_project_layer
from app.harness.kb.retriever import query as kb_query
from app.storage.agent_context_store import (
    TEXT_SUFFIXES,
    list_agent_context_files,
    load_agent_research_sites,
)

SELF_CONTEXT_DIRS: tuple[str, ...] = ("docs", "prompts", "examples", "evals", "uploads")


@dataclass(frozen=True)
class SelfContextEntry:
    path: str
    text: str


@dataclass(frozen=True)
class ResearchPack:
    research_dir: Path | None
    plan_md: str
    notes_md: str
    summary_md: str
    evidence_index: dict[str, Any]


def load_idea_self_context(
    *, base_dir: Path | None = None, max_chars_per_file: int = 4000
) -> tuple[SelfContextEntry, ...]:
    """Read Idea Agent-owned docs/prompts/examples/evals and local code."""
    if base_dir is None:
        return tuple(
            SelfContextEntry(path=item.path, text=item.content)
            for item in list_agent_context_files(
                "idea",
                include_runtime_code=True,
                max_chars_per_file=max_chars_per_file,
            )
        )
    root = base_dir
    entries: list[SelfContextEntry] = []
    for dirname in SELF_CONTEXT_DIRS:
        directory = root / dirname
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            rel = path.relative_to(root).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            entries.append(SelfContextEntry(path=rel, text=text[:max_chars_per_file]))
    for path in sorted(root.glob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        entries.append(SelfContextEntry(path=rel, text=text[:max_chars_per_file]))
    return tuple(entries)


def render_self_context(entries: Sequence[SelfContextEntry]) -> str:
    if not entries:
        return "未找到 Idea Agent 自上下文文件。"
    parts: list[str] = ["# Idea Agent 自上下文"]
    for entry in entries:
        parts.append(f"## {entry.path}\n\n{entry.text}")
    return "\n\n".join(parts)


def research_dir_for_request(request: RunRequest) -> Path | None:
    """Resolve where this run should persist Idea research artifacts."""
    explicit = request.extra.get("idea_research_dir") or request.extra.get("research_dir")
    if explicit:
        return Path(str(explicit))

    run_root = request.extra.get("run_root")
    if run_root:
        return Path(str(run_root)) / "idea" / "research"

    progress_path = request.extra.get("debate_progress_path")
    if progress_path:
        return Path(str(progress_path)).parent / "research"

    return None


def build_idea_context(request: RunRequest, base_context: ContextPack) -> ContextPack:
    """Add project rules, KB excerpts, and self-context before research."""
    project_layer = build_project_layer(project=request.project)
    self_entries = load_idea_self_context()
    kb_excerpts = _query_research_kb(request.user_request)
    research_sites = load_agent_research_sites("idea")

    system = (
        base_context.system
        + "\n\nV1 Idea workflow: load self-context, research the task, persist "
        "the research pack, then draft proposal.v1 from research_summary.v1.md "
        "and evidence_index.v1.json. Do not invent hypotheses that cannot be "
        "validated by the current project."
    )
    upstream = dict(base_context.upstream)
    upstream["idea_self_context"] = render_self_context(self_entries)
    upstream["idea_research_sites"] = _render_research_sites(research_sites)
    if kb_excerpts:
        upstream["idea_kb_research_excerpts"] = "\n\n".join(kb_excerpts)
    return ContextPack(
        system=system,
        project=project_layer.render(),
        task=base_context.task,
        upstream=upstream,
        metadata={
            **base_context.metadata,
            "idea_self_context_files": [entry.path for entry in self_entries],
            "idea_kb_excerpt_count": len(kb_excerpts),
            "idea_research_site_count": len(
                [site for site in research_sites if site.enabled]
            ),
        },
    )


def _render_research_sites(sites: Sequence[Any]) -> str:
    lines = ["# Idea Agent 调研站点配置"]
    for site in sites:
        status = "enabled" if bool(getattr(site, "enabled", False)) else "disabled"
        lines.append(
            f"- [{status}] {getattr(site, 'label', '')}: {getattr(site, 'url', '')}"
        )
    return "\n".join(lines)


def prepare_research_pack(
    *,
    request: RunRequest,
    context: ContextPack,
    self_context: Sequence[SelfContextEntry] | None = None,
    research_config: Mapping[str, Any] | None = None,
) -> ResearchPack:
    """Build and optionally persist deterministic research artifacts."""
    entries = tuple(self_context or load_idea_self_context())
    research_dir = research_dir_for_request(request)
    network_enabled = _network_research_enabled(request, research_config)
    evidence_items = _evidence_items(
        request=request,
        context=context,
        self_context=entries,
    )
    evidence_index: dict[str, Any] = {
        "schema": "idea_research_evidence.v1",
        "project": request.project,
        "created": _now(),
        "network_research": (
            "enabled_configured_no_fetcher" if network_enabled else "disabled"
        ),
        "items": evidence_items,
    }
    plan_md = _render_research_plan(
        request=request,
        evidence_items=evidence_items,
        network_enabled=network_enabled,
    )
    notes_md = _render_research_notes(evidence_items=evidence_items)
    summary_md = _render_research_summary(
        request=request,
        evidence_items=evidence_items,
    )
    pack = ResearchPack(
        research_dir=research_dir,
        plan_md=plan_md,
        notes_md=notes_md,
        summary_md=summary_md,
        evidence_index=evidence_index,
    )
    if research_dir is not None:
        write_research_pack(pack)
    return pack


def _network_research_enabled(
    request: RunRequest,
    research_config: Mapping[str, Any] | None,
) -> bool:
    value = request.extra.get("enable_network_research")
    if value is None:
        value = request.extra.get("network_research")
    if value is None and research_config is not None:
        value = research_config.get("enable_network")
    return _truthy(value)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def write_research_pack(pack: ResearchPack) -> None:
    if pack.research_dir is None:
        return
    pack.research_dir.mkdir(parents=True, exist_ok=True)
    (pack.research_dir / "research_plan.v1.md").write_text(
        pack.plan_md, encoding="utf-8"
    )
    (pack.research_dir / "research_notes.v1.md").write_text(
        pack.notes_md, encoding="utf-8"
    )
    (pack.research_dir / "research_summary.v1.md").write_text(
        pack.summary_md, encoding="utf-8"
    )
    (pack.research_dir / "evidence_index.v1.json").write_text(
        json.dumps(pack.evidence_index, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def augment_context_with_research(
    context: ContextPack,
    pack: ResearchPack,
) -> ContextPack:
    upstream = dict(context.upstream)
    upstream["idea_research_summary"] = pack.summary_md
    upstream["idea_evidence_index"] = json.dumps(
        pack.evidence_index, ensure_ascii=False, indent=2
    )
    system = (
        context.system
        + "\n\nProposal constraint: base the proposal on idea_research_summary "
        "and idea_evidence_index. If evidence is weak, state the evidence gap "
        "instead of overstating confidence."
    )
    return ContextPack(
        system=system,
        project=context.project,
        task=context.task,
        upstream=upstream,
        metadata={
            **context.metadata,
            "idea_research_dir": str(pack.research_dir) if pack.research_dir else "",
            "idea_research_evidence_count": len(pack.evidence_index.get("items", [])),
        },
    )


def validate_idea_quality(metadata: Mapping[str, Any]) -> list[str]:
    """Return warning codes for V1 Idea quality checks."""
    warnings: list[str] = []
    hypothesis = str(metadata.get("hypothesis", "")).strip()
    if not _looks_testable(hypothesis):
        warnings.append("hypothesis_not_testable")
    if not _non_empty_list(metadata.get("testable_predictions")):
        warnings.append("missing_testable_predictions")
    if not metadata.get("experiment_hint"):
        warnings.append("missing_experiment_hint")
    if not _non_empty_list(metadata.get("evidence_refs")):
        warnings.append("missing_evidence_refs")
    constraints = metadata.get("constraints", [])
    constraints_text = " ".join(str(item) for item in constraints) if isinstance(constraints, list) else str(constraints)
    if "baseline" not in constraints_text.lower() and "兼容" not in constraints_text:
        warnings.append("missing_baseline_compat_constraint")
    return warnings


def _query_research_kb(user_request: str) -> list[str]:
    if not user_request.strip():
        return []
    try:
        hits = kb_query(
            query=user_request,
            zones=["literature", "methodology", "run_archive"],
            top_k=5,
        )
    except Exception:
        return []
    excerpts: list[str] = []
    for hit in hits:
        excerpts.append(
            f"[{hit.record.zone} score={hit.score:.3f}] "
            f"{hit.record.text[:1200]}"
        )
    return excerpts


def _evidence_items(
    *,
    request: RunRequest,
    context: ContextPack,
    self_context: Sequence[SelfContextEntry],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, entry in enumerate(self_context, 1):
        items.append(
            {
                "id": f"self_context_{index}",
                "kind": "self_context",
                "title": entry.path,
                "excerpt": entry.text[:800],
                "path": f"backend/app/agents/idea/{entry.path}",
            }
        )
    if context.project:
        items.append(
            {
                "id": "project_context",
                "kind": "project",
                "title": f"Project rules for {request.project}",
                "excerpt": context.project[:1200],
            }
        )
    for label, content in context.upstream.items():
        if label == "idea_self_context":
            continue
        items.append(
            {
                "id": f"upstream_{len(items) + 1}",
                "kind": "context",
                "title": label,
                "excerpt": content[:1200],
            }
        )
    return items


def _render_research_plan(
    *,
    request: RunRequest,
    evidence_items: list[dict[str, Any]],
    network_enabled: bool,
) -> str:
    network_status = (
        "enabled by config, but no web/arXiv fetcher is required in V1"
        if network_enabled
        else "disabled by default for V1 stability"
    )
    lines = [
        "# Idea Research Plan",
        "",
        f"- Project: `{request.project}`",
        f"- Task: {request.user_request or '(empty task)'}",
        "- Source order: project rules -> Idea self context -> local KB/history -> optional network research.",
        f"- Network research: optional and {network_status}.",
        "",
        "## Evidence Sources",
    ]
    for item in evidence_items:
        lines.append(f"- `{item['id']}` ({item['kind']}): {item['title']}")
    return "\n".join(lines) + "\n"


def _render_research_notes(*, evidence_items: list[dict[str, Any]]) -> str:
    lines = ["# Idea Research Notes", ""]
    for item in evidence_items:
        lines.extend(
            [
                f"## {item['id']} · {item['title']}",
                "",
                str(item.get("excerpt", "")),
                "",
            ]
        )
    return "\n".join(lines)


def _render_research_summary(
    *,
    request: RunRequest,
    evidence_items: list[dict[str, Any]],
) -> str:
    evidence_ids = ", ".join(str(item["id"]) for item in evidence_items[:8])
    return (
        "# Idea Research Summary\n\n"
        f"## Task\n{request.user_request or '(empty task)'}\n\n"
        "## Evidence Used\n"
        f"{evidence_ids or 'No local evidence found.'}\n\n"
        "## Evidence Gaps\n"
        "- V1 local research does not require network access; external literature "
        "should be treated as optional until arXiv/web tools are explicitly enabled.\n\n"
        "## Proposal Guidance\n"
        "- Prefer hypotheses that can be tested with RES / PIM / APE / loss.\n"
        "- Preserve project baseline compatibility constraints.\n"
        "- Provide at least one minimal experiment hint for the Experiment Agent.\n"
    )


def _looks_testable(hypothesis: str) -> bool:
    if len(hypothesis) < 8:
        return False
    lowered = hypothesis.lower()
    markers = (
        "res",
        "pim",
        "ape",
        "loss",
        "db",
        "%",
        "降低",
        "提升",
        "保持",
        "退化",
        "对比",
        "验证",
        "控制",
    )
    return any(marker in lowered for marker in markers)


def _non_empty_list(value: Any) -> bool:
    return isinstance(value, list) and len(value) > 0


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
