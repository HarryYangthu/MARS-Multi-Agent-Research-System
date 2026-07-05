"""Acceptance report for real Idea Agent runs."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app.harness.schema.frontmatter_parser import parse as parse_frontmatter
from app.harness.schema.validator import validate_document
from app.settings import get_settings, repo_root
from app.storage.artifact_store import ArtifactRef
from app.storage.run_store import RunHandle

AcceptanceStatus = Literal["pass", "warn", "fail", "blocked"]

_STATUS_RANK: dict[AcceptanceStatus, int] = {
    "pass": 0,
    "warn": 1,
    "fail": 2,
    "blocked": 3,
}
_REQUIRED_RESEARCH_FILES: tuple[str, ...] = (
    "research/research_plan.v1.md",
    "research/research_notes.v1.md",
    "research/research_summary.v1.md",
    "research/evidence_index.v1.json",
    "research/tool_results.v1.json",
    "research/source_summaries.v1.md",
    "research/source_summaries.v1.json",
)
_REQUIRED_TOOL_FAMILIES: tuple[str, ...] = (
    "search.local_docs",
    "knowledge.kb_query",
    "knowledge.baseline_match",
    "code.repo_reader",
    "search.arxiv_search",
)
_HARD_QUALITY_WARNINGS: frozenset[str] = frozenset(
    {
        "missing_evidence_refs",
        "evidence_refs_not_in_research_index",
        "literature_relevance_low",
        "related_literature_placeholder",
        "source_summaries_missing",
    }
)


@dataclass(frozen=True)
class AcceptanceCheck:
    name: str
    status: AcceptanceStatus
    detail: str
    evidence: tuple[str, ...] = ()


def write_idea_acceptance_report(
    *,
    run: RunHandle,
    artifact_ref: ArtifactRef | None = None,
    node_key: str = "idea",
) -> Path:
    """Write ``idea/idea_agent_acceptance_report.md`` for the current run."""
    target = run.subdir("idea") / "idea_agent_acceptance_report.md"
    target.write_text(
        build_idea_acceptance_report(
            run=run,
            artifact_ref=artifact_ref,
            node_key=node_key,
        ),
        encoding="utf-8",
    )
    return target


def build_idea_acceptance_report(
    *,
    run: RunHandle,
    artifact_ref: ArtifactRef | None = None,
    node_key: str = "idea",
) -> str:
    proposal = artifact_ref.path if artifact_ref is not None else _latest_proposal(run)
    evidence_index = _read_json_object(run.subdir("idea") / "research" / "evidence_index.v1.json")
    tool_results = _read_json_list(run.subdir("idea") / "research" / "tool_results.v1.json")
    source_index = _read_json_object(run.subdir("idea") / "research" / "source_summaries.v1.json")
    checks = [
        _check_agent_path(run=run, node_key=node_key),
        _check_workspace_files(run=run, proposal=proposal),
        _check_context_loading(run=run, evidence_index=evidence_index),
        _check_research_tools(run=run, tool_results=tool_results, source_index=source_index),
        _check_artifact_quality(run=run, proposal=proposal),
        _check_frontend_readiness(run=run),
    ]
    overall = max((check.status for check in checks), key=lambda item: _STATUS_RANK[item])
    settings = get_settings()
    lines = [
        "# Idea Agent Acceptance Report",
        "",
        f"- Run: `{run.run_id}`",
        f"- Project: `{run.project}`",
        f"- Task: {run.task}",
        f"- Entrypoint: `{run.entrypoint}`",
        f"- Generated: `{datetime.now(tz=timezone.utc).isoformat()}`",
        f"- Overall: **{overall.upper()}**",
        f"- Mock mode: `{settings.mars_mock_mode}`",
        f"- Network tools: `{'enabled' if settings.mars_enable_network_tools else 'disabled'}`",
        f"- Web search provider configured: `{'yes' if settings.mars_web_search_provider else 'no'}`",
        "",
        "## Checks",
        "",
        "| Check | Status | Detail | Evidence |",
        "| --- | --- | --- | --- |",
    ]
    for check in checks:
        evidence = "<br>".join(f"`{item}`" for item in check.evidence) or "-"
        lines.append(
            f"| {check.name} | **{check.status}** | {_escape_table(check.detail)} | {evidence} |"
        )
    lines.extend(
        [
            "",
            "## Tool Calls",
            "",
            _render_tool_summary(tool_results),
            "",
            "## Source Summaries",
            "",
            _render_source_summary(source_index),
            "",
            "## Files",
            "",
        ]
    )
    for relative in _workspace_inventory(run):
        lines.append(f"- `{relative}`")
    lines.append("")
    return "\n".join(lines)


def _check_agent_path(*, run: RunHandle, node_key: str) -> AcceptanceCheck:
    websocket_events = _read_jsonl(run.subdir("events") / "websocket_events.jsonl")
    agent_events = _read_jsonl(run.subdir("events") / "agent_events.jsonl")
    run_started = any(
        item.get("event") == "run.started" and item.get("entrypoint") == "idea"
        for item in websocket_events
    )
    ran = any(
        item.get("agent") == node_key and item.get("to_state") == "running"
        for item in agent_events
    )
    interrupted = any(
        item.get("agent") == node_key and item.get("to_state") == "waiting_review"
        for item in agent_events
    )
    if run_started and ran and interrupted:
        return AcceptanceCheck(
            name="Agent 路径",
            status="pass",
            detail="Run came from the idea entrypoint and reached HITL review through the orchestrator.",
            evidence=("events/websocket_events.jsonl", "events/agent_events.jsonl"),
        )
    missing = []
    if run.entrypoint != "idea":
        missing.append("run_meta entrypoint is not idea")
    if not run_started:
        missing.append("run.started event missing")
    if not ran:
        missing.append("idea running transition missing")
    if not interrupted:
        missing.append("idea waiting_review transition missing")
    return AcceptanceCheck(
        name="Agent 路径",
        status="fail",
        detail="; ".join(missing) or "Idea orchestrator path evidence is incomplete.",
        evidence=("run_meta.json", "events/agent_events.jsonl"),
    )


def _check_workspace_files(*, run: RunHandle, proposal: Path | None) -> AcceptanceCheck:
    missing = [
        relative
        for relative in _REQUIRED_RESEARCH_FILES
        if not (run.subdir("idea") / relative).exists()
    ]
    missing.extend(
        relative
        for relative in (
            "events/agent_events.jsonl",
            "events/tool_calls.jsonl",
            "events/evaluation_events.jsonl",
        )
        if not (run.root / relative).exists()
    )
    if proposal is None or not proposal.exists():
        missing.append("idea/idea_proposal.vN.md")
    if missing:
        return AcceptanceCheck(
            name="Run 工作区沉淀",
            status="fail",
            detail=f"Missing required files: {', '.join(missing)}",
            evidence=tuple(missing[:4]),
        )
    return AcceptanceCheck(
        name="Run 工作区沉淀",
        status="pass",
        detail="Proposal, research pack, tool traces, and evaluation events are present.",
        evidence=("idea/", "idea/research/", "events/"),
    )


def _check_context_loading(
    *,
    run: RunHandle,
    evidence_index: Mapping[str, Any],
) -> AcceptanceCheck:
    items = _as_mapping_list(evidence_index.get("items"))
    titles = " ".join(str(item.get("title") or "") for item in items)
    kinds = {str(item.get("kind") or "") for item in items}
    context_files = tuple(
        path.relative_to(run.root).as_posix()
        for path in sorted(run.subdir("context").glob("context_manifest.v2*.json"))
    )
    snapshots = tuple(
        path.relative_to(run.root).as_posix()
        for path in sorted(run.subdir("context").glob("idea_context_snapshot*.md"))
    )
    requirements = {
        "project rules": "project" in kinds,
        "self context": "self_context" in kinds,
        "code repositories": "idea_code_repositories" in titles or "代码仓" in titles,
        "research sites": "idea_research_sites" in titles or "调研站点" in titles,
        "kb/local context": "idea_kb_research_excerpts" in titles or "kb" in titles.lower(),
        "context manifests": bool(context_files),
        "context snapshots": bool(snapshots),
    }
    missing = [name for name, ok in requirements.items() if not ok]
    if missing:
        return AcceptanceCheck(
            name="上下文装载",
            status="fail",
            detail=f"Missing context categories: {', '.join(missing)}",
            evidence=tuple([*context_files[:2], *snapshots[:2]]) or ("context/",),
        )
    return AcceptanceCheck(
        name="上下文装载",
        status="pass",
        detail="Project rules, self-context, code repo config, research sites, KB/local context, and manifests are visible.",
        evidence=tuple([*context_files[:2], *snapshots[:1]]),
    )


def _check_research_tools(
    *,
    run: RunHandle,
    tool_results: Sequence[Mapping[str, Any]],
    source_index: Mapping[str, Any],
) -> AcceptanceCheck:
    tools = [str(item.get("tool") or "") for item in tool_results]
    missing_tools = [tool for tool in _REQUIRED_TOOL_FAMILIES if tool not in tools]
    arxiv = [item for item in tool_results if item.get("tool") == "search.arxiv_search"]
    low_arxiv = [
        item for item in arxiv if _literature_status(item) in {"low_relevance", "no_relevant_hits", "no_hits"}
    ]
    has_followup = any(bool(item.get("follow_up_of")) for item in arxiv)
    has_pass_arxiv = any(_literature_status(item) == "pass" for item in arxiv)
    sources = _as_mapping_list(source_index.get("sources"))
    source_count = len(sources)
    downloaded_pdfs = _downloaded_pdf_sources(run=run, sources=sources)
    fetch_calls = [item for item in tool_results if item.get("tool") == "search.fetch_sources"]
    web_calls = [item for item in tool_results if item.get("tool") == "search.web_search"]
    failures: list[str] = []
    warnings: list[str] = []
    if missing_tools:
        failures.append(f"missing tools: {', '.join(missing_tools)}")
    if low_arxiv and not has_followup:
        failures.append("low-relevance arXiv hits were not followed by a revised query")
    if has_pass_arxiv and source_count == 0:
        failures.append("relevant external papers were found but no source summaries were written")
    if has_pass_arxiv and not downloaded_pdfs:
        failures.append("relevant external papers were found but no downloaded PDF source exists")
    if fetch_calls and source_count == 0:
        failures.append("fetch_sources ran but produced no usable summaries")
    if low_arxiv and has_followup and not has_pass_arxiv:
        warnings.append("arXiv follow-up ran, but literature remains low relevance")
    if not web_calls:
        warnings.append("web/blog search did not run; check provider and allowlist configuration")
    if failures:
        return AcceptanceCheck(
            name="真实调研工具链",
            status="fail",
            detail="; ".join([*failures, *warnings]),
            evidence=("idea/research/tool_results.v1.json",),
        )
    if warnings:
        return AcceptanceCheck(
            name="真实调研工具链",
            status="warn",
            detail="; ".join(warnings),
            evidence=("idea/research/tool_results.v1.json",),
        )
    return AcceptanceCheck(
        name="真实调研工具链",
        status="pass",
        detail="Required local, KB, code, arXiv/web, source summaries, and downloaded PDF evidence are present.",
        evidence=("idea/research/tool_results.v1.json", "idea/research/source_summaries.v1.md"),
    )


def _downloaded_pdf_sources(
    *, run: RunHandle, sources: Sequence[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    out: list[Mapping[str, Any]] = []
    for source in sources:
        if str(source.get("source_type") or "").lower() != "pdf":
            continue
        raw_path = str(source.get("download_path") or "").strip()
        if not raw_path:
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            path = repo_root() / path
        try:
            path.relative_to(run.root)
        except ValueError:
            # fetch_sources may store repo-relative paths; still verify the file
            # exists, but report only paths that are rooted in this workspace.
            pass
        if path.exists() and path.is_file():
            out.append(source)
    return out


def _check_artifact_quality(*, run: RunHandle, proposal: Path | None) -> AcceptanceCheck:
    if proposal is None or not proposal.exists():
        return AcceptanceCheck(
            name="产物质量",
            status="fail",
            detail="No proposal artifact found.",
            evidence=("idea/",),
        )
    text = proposal.read_text(encoding="utf-8")
    validation = validate_document(text, expected_schema="proposal.v1")
    if not validation.valid:
        return AcceptanceCheck(
            name="产物质量",
            status="fail",
            detail=f"proposal.v1 schema failed: {validation.first_error()}",
            evidence=(proposal.relative_to(run.root).as_posix(),),
        )
    parsed = parse_frontmatter(text)
    quality_warnings = {
        str(item).strip()
        for item in _as_list(parsed.metadata.get("quality_warnings"))
        if str(item).strip()
    }
    eval_decision = _latest_artifact_quality_decision(run, proposal)
    if quality_warnings & _HARD_QUALITY_WARNINGS and eval_decision == "pass":
        return AcceptanceCheck(
            name="产物质量",
            status="fail",
            detail=(
                "Hard evidence warnings are present but artifact quality still passed: "
                f"{', '.join(sorted(quality_warnings & _HARD_QUALITY_WARNINGS))}"
            ),
            evidence=(proposal.relative_to(run.root).as_posix(), "idea/evals/"),
        )
    if quality_warnings & _HARD_QUALITY_WARNINGS:
        return AcceptanceCheck(
            name="产物质量",
            status="warn",
            detail=f"Hard evidence warnings are visible: {', '.join(sorted(quality_warnings & _HARD_QUALITY_WARNINGS))}",
            evidence=(proposal.relative_to(run.root).as_posix(),),
        )
    return AcceptanceCheck(
        name="产物质量",
        status="pass",
        detail=f"proposal.v1 schema passed; artifact quality decision is {eval_decision or 'not recorded yet'}.",
        evidence=(proposal.relative_to(run.root).as_posix(), "idea/evals/"),
    )


def _check_frontend_readiness(*, run: RunHandle) -> AcceptanceCheck:
    files = _workspace_inventory(run)
    required = {
        "idea/research/tool_results.v1.json",
        "idea/research/source_summaries.v1.md",
    }
    missing = sorted(required - set(files))
    if missing:
        return AcceptanceCheck(
            name="前端工作区数据",
            status="fail",
            detail=f"Workspace tree will miss key files: {', '.join(missing)}",
            evidence=tuple(missing),
        )
    return AcceptanceCheck(
        name="前端工作区数据",
        status="pass",
        detail="Explorer can expose tool results and source summaries; this report is written into the same workspace.",
        evidence=("idea/idea_agent_acceptance_report.md", "idea/research/"),
    )


def _render_tool_summary(tool_results: Sequence[Mapping[str, Any]]) -> str:
    if not tool_results:
        return "- No tool results recorded."
    lines = ["| # | Tool | Status | Query / Detail |", "| --- | --- | --- | --- |"]
    for index, item in enumerate(tool_results, 1):
        args = item.get("args")
        output = item.get("output")
        query = ""
        if isinstance(args, Mapping):
            query = str(args.get("query") or args.get("q") or args.get("path") or "")
        if not query and isinstance(output, Mapping):
            query = str(output.get("query") or output.get("q") or "")
        quality = _literature_status(item)
        suffix = f"; relevance={quality}" if quality else ""
        lines.append(
            f"| {index} | `{item.get('tool', '')}` | `{item.get('status', '')}` | {_escape_table((query or str(item.get('error') or ''))[:220] + suffix)} |"
        )
    return "\n".join(lines)


def _render_source_summary(source_index: Mapping[str, Any]) -> str:
    sources = _as_mapping_list(source_index.get("sources"))
    if not sources:
        return "- No downloaded source summaries recorded."
    lines = ["| Source | Type | Summary |", "| --- | --- | --- |"]
    for source in sources:
        lines.append(
            "| "
            f"{_escape_table(str(source.get('title') or 'source')[:160])} | "
            f"`{source.get('source_type', '')}` | "
            f"`{source.get('summary_path', '')}` |"
        )
    return "\n".join(lines)


def _workspace_inventory(run: RunHandle) -> list[str]:
    out: list[str] = []
    for base in ("context", "idea", "events"):
        root = run.root / base
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file():
                out.append(path.relative_to(run.root).as_posix())
    return out


def _latest_proposal(run: RunHandle) -> Path | None:
    proposals = sorted(run.subdir("idea").glob("idea_proposal.v*.md"))
    return proposals[-1] if proposals else None


def _latest_artifact_quality_decision(run: RunHandle, proposal: Path) -> str:
    version = proposal.stem.rsplit(".", 1)[-1]
    paths = sorted(run.subdir("idea").glob(f"evals/idea_proposal.{version}.artifact_quality_rubric.eval.md"))
    if not paths:
        return ""
    try:
        return str(parse_frontmatter(paths[-1].read_text(encoding="utf-8")).metadata.get("decision") or "")
    except Exception:
        return ""


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return _as_mapping_list(data)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append({str(key): data[key] for key in data})
    return rows


def _literature_status(observation: Mapping[str, Any]) -> str:
    quality = observation.get("quality")
    if not isinstance(quality, Mapping):
        return ""
    relevance = quality.get("literature_relevance")
    if not isinstance(relevance, Mapping):
        return ""
    return str(relevance.get("status") or "")


def _as_mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [{str(key): item[key] for key in item} for item in value if isinstance(item, Mapping)]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str) and not value.strip():
        return []
    return [value]


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")
