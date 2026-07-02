"""V2 Idea Agent self-context, research pack, and quality checks."""
from __future__ import annotations

import json
import urllib.parse
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
    load_agent_code_repositories,
    list_agent_context_files,
    load_agent_research_sites,
)

SELF_CONTEXT_DIRS: tuple[str, ...] = ("docs", "prompts", "examples", "evals", "uploads")
PIMC_ALLOWED_KNOBS: tuple[str, ...] = (
    "expert_count",
    "order",
    "router_type",
    "snr_db",
    "learning_rate",
)
PIMC_CORE_METRICS: tuple[str, ...] = ("RES", "PIM", "APE", "loss")
LITERATURE_RELEVANCE_GROUPS: tuple[tuple[str, tuple[str, ...], int], ...] = (
    (
        "passive_intermodulation",
        (
            "passive intermodulation",
            "intermodulation cancellation",
            "pim cancellation",
            "pimc",
        ),
        3,
    ),
    (
        "massive_mimo",
        ("massive mimo", "mimo", "multi-antenna", "antenna array"),
        2,
    ),
    (
        "rf_nonlinearity",
        (
            "digital predistortion",
            "predistortion",
            "power amplifier",
            "rf",
            "nonlinear distortion",
        ),
        2,
    ),
    (
        "beam_layer_switching",
        ("beamforming", "beam switching", "layer switching", "fdd"),
        1,
    ),
    (
        "pimc_modeling",
        (
            "memory polynomial",
            "volterra",
            "low-rank",
            "group convolution",
            "sparse routing",
            "mixture of experts",
        ),
        1,
    ),
)
PLACEHOLDER_CITATION_MARKERS: tuple[str, ...] = (
    "1234567",
    "2103.00000",
    "0000.00000",
    "placeholder",
    "example.com",
    "待补",
    "占位",
)
LOW_LITERATURE_STATUSES: frozenset[str] = frozenset(
    {"low_relevance", "no_relevant_hits", "no_hits"}
)


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
    source_summary_md: str
    source_summary_index: dict[str, Any]
    evidence_index: dict[str, Any]
    tool_results: tuple[dict[str, Any], ...] = ()


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
    code_repositories = load_agent_code_repositories("idea", project=request.project)

    system = (
        base_context.system
        + "\n\nV2 Idea workflow: load self-context, research the task, persist "
        "the research pack, then draft proposal.v1 from research_summary.v1.md "
        "and evidence_index.v1.json. Do not invent hypotheses that cannot be "
        "validated by the current project."
    )
    upstream = dict(base_context.upstream)
    upstream["idea_self_context"] = render_self_context(self_entries)
    upstream["idea_research_sites"] = _render_research_sites(research_sites)
    upstream["idea_code_repositories"] = _render_code_repositories(code_repositories)
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
            "idea_code_repository_count": len(code_repositories),
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


def _render_code_repositories(repositories: Sequence[Any]) -> str:
    lines = ["# Idea Agent 代码仓配置"]
    if not repositories:
        lines.append("- 未配置代码仓。")
        return "\n".join(lines)
    for repo in repositories:
        lines.extend(
            [
                f"- project: {getattr(repo, 'project', '')}",
                f"  label: {getattr(repo, 'label', '')}",
                f"  repo_mode: {getattr(repo, 'repo_mode', '')}",
                f"  repo_path: {getattr(repo, 'repo_path', '')}",
                f"  exists: {getattr(repo, 'exists', False)}",
                f"  read_only: {getattr(repo, 'read_only', False)}",
                f"  allowed_paths: {', '.join(getattr(repo, 'allowed_paths', ()) or ())}",
                f"  protected_paths: {', '.join(getattr(repo, 'protected_paths', ()) or ())}",
            ]
        )
    return "\n".join(lines)


async def gather_required_research_tools(
    *,
    request: RunRequest,
    research_config: Mapping[str, Any] | None,
    real_provider: bool,
) -> tuple[dict[str, Any], ...]:
    """Run the non-optional Idea research tool pass for real LLM runs."""
    if not real_provider or not _required_tool_research_enabled(
        request,
        research_config,
    ):
        return ()

    from app.harness.tools.registry import ToolContext, get_registry

    registry = get_registry()
    run_root = str(request.extra.get("run_root", ""))
    tool_ctx = ToolContext(
        run_id=str(request.extra.get("run_id", "")),
        project=request.project,
        agent="idea",
        extra={
            "run_root": run_root,
            "project_repo_root": str(request.extra.get("project_repo_root", "")),
        },
    )
    network_enabled = _network_research_enabled(request, research_config)
    observations: list[dict[str, Any]] = []
    next_index = 1
    for tool_name, args in _required_research_calls(
        request,
        network_enabled=network_enabled,
        research_config=research_config,
    ):
        observation = await _dispatch_research_tool(
            registry=registry,
            tool_ctx=tool_ctx,
            tool_name=tool_name,
            args=args,
            index=next_index,
        )
        observations.append(observation)
        next_index += 1
        if _should_fetch_sources(tool_name, observation, research_config):
            source_observation = await _dispatch_research_tool(
                registry=registry,
                tool_ctx=tool_ctx,
                tool_name="search.fetch_sources",
                args={
                    "sources": _sources_for_download(observation),
                    "max_sources": _source_download_limit(research_config),
                },
                index=next_index,
                follow_up_of=str(observation.get("id") or ""),
                retry_reason="source_download_and_summary",
            )
            observations.append(source_observation)
            next_index += 1
        if tool_name != "search.arxiv_search" or not _needs_literature_followup(
            observation
        ):
            continue
        root_id = str(observation.get("id") or "")
        seen_queries = {str(args.get("query") or args.get("q") or "").lower()}
        for followup_args in _literature_followup_calls(
            request,
            initial_args=args,
            seen_queries=seen_queries,
        ):
            retry = await _dispatch_research_tool(
                registry=registry,
                tool_ctx=tool_ctx,
                tool_name=tool_name,
                args=followup_args,
                index=next_index,
                follow_up_of=root_id,
                retry_reason="literature_relevance_low",
            )
            observations.append(retry)
            next_index += 1
            if _should_fetch_sources(tool_name, retry, research_config):
                source_observation = await _dispatch_research_tool(
                    registry=registry,
                    tool_ctx=tool_ctx,
                    tool_name="search.fetch_sources",
                    args={
                        "sources": _sources_for_download(retry),
                        "max_sources": _source_download_limit(research_config),
                    },
                    index=next_index,
                    follow_up_of=str(retry.get("id") or ""),
                    retry_reason="source_download_and_summary",
                )
                observations.append(source_observation)
                next_index += 1
                break
            if not _needs_literature_followup(retry):
                break
    return tuple(observations)


async def _dispatch_research_tool(
    *,
    registry: Any,
    tool_ctx: Any,
    tool_name: str,
    args: Mapping[str, Any],
    index: int,
    follow_up_of: str = "",
    retry_reason: str = "",
) -> dict[str, Any]:
    call_args = {str(key): args[key] for key in args}
    if follow_up_of:
        call_args["follow_up_of"] = follow_up_of
    if retry_reason:
        call_args["retry_reason"] = retry_reason
    result = await registry.dispatch(tool_name, call_args, tool_ctx)
    compact_output = _compact_tool_output(result.output)
    observation: dict[str, Any] = {
        "id": f"tool_{index}",
        "tool": tool_name,
        "args": call_args,
        "ok": result.ok,
        "status": str(result.status or ""),
        "error": result.error,
        "output": compact_output,
        "evidence_refs": list(result.evidence_refs),
        "duration_ms": result.duration_ms,
    }
    if follow_up_of:
        observation["follow_up_of"] = follow_up_of
    if retry_reason:
        observation["retry_reason"] = retry_reason
    quality = _assess_tool_output_quality(tool_name, compact_output)
    if quality:
        observation["quality"] = quality
    return observation


def _needs_literature_followup(observation: Mapping[str, Any]) -> bool:
    if observation.get("ok") is not True:
        return True
    relevance = _literature_relevance_quality(observation)
    if relevance is None:
        return False
    return str(relevance.get("status") or "") in LOW_LITERATURE_STATUSES


def _should_fetch_sources(
    tool_name: str,
    observation: Mapping[str, Any],
    research_config: Mapping[str, Any] | None,
) -> bool:
    if not _source_downloads_enabled(research_config):
        return False
    if observation.get("ok") is not True:
        return False
    sources = _sources_for_download(observation)
    if not sources:
        return False
    if tool_name == "search.arxiv_search":
        relevance = _literature_relevance_quality(observation)
        return relevance is not None and str(relevance.get("status") or "") == "pass"
    return tool_name == "search.web_search"


def _source_downloads_enabled(research_config: Mapping[str, Any] | None) -> bool:
    if research_config is None:
        return True
    value = research_config.get("source_downloads", True)
    return _truthy(value)


def _source_download_limit(research_config: Mapping[str, Any] | None) -> int:
    if research_config is None:
        return 3
    try:
        return max(1, min(int(research_config.get("source_download_limit", 3) or 3), 5))
    except (TypeError, ValueError):
        return 3


def _sources_for_download(observation: Mapping[str, Any]) -> list[dict[str, Any]]:
    output = observation.get("output")
    if not isinstance(output, Mapping):
        return []
    raw_hits = output.get("hits")
    if not isinstance(raw_hits, list):
        return []
    relevant_urls = _relevant_hit_urls(observation)
    sources: list[dict[str, Any]] = []
    for hit in raw_hits:
        if not isinstance(hit, Mapping):
            continue
        url = str(hit.get("url") or hit.get("evidence_ref") or "").strip()
        if relevant_urls and url not in relevant_urls:
            continue
        title = str(hit.get("title") or hit.get("id") or url).strip()
        if not title or not url:
            continue
        sources.append(
            {
                "title": title,
                "url": url,
                "pdf_url": str(hit.get("pdf_url") or "").strip(),
                "summary": str(hit.get("summary") or hit.get("snippet") or hit.get("excerpt") or ""),
            }
        )
        if len(sources) >= _source_download_limit(None):
            break
    return sources


def _relevant_hit_urls(observation: Mapping[str, Any]) -> set[str]:
    relevance = _literature_relevance_quality(observation)
    if relevance is None:
        return set()
    raw_hits = relevance.get("hits")
    if not isinstance(raw_hits, list):
        return set()
    return {
        str(item.get("url") or "").strip()
        for item in raw_hits
        if isinstance(item, Mapping) and bool(item.get("relevant")) and item.get("url")
    }


def _literature_followup_calls(
    request: RunRequest,
    *,
    initial_args: Mapping[str, Any],
    seen_queries: set[str],
) -> tuple[dict[str, Any], ...]:
    task_query = request.user_request.strip()
    candidates = [
        (
            'all:"passive intermodulation" AND all:"massive MIMO" '
            'AND all:"cancellation"'
        ),
        (
            'all:"passive intermodulation" AND all:"digital predistortion" '
            "AND all:RF"
        ),
        (
            'all:"passive intermodulation" AND all:"beamforming" '
            "AND all:MIMO"
        ),
        (
            'all:"memory polynomial" AND all:"passive intermodulation" '
            "AND all:cancellation"
        ),
        (
            'all:"passive intermodulation" AND all:"nonlinear distortion" '
            "AND all:antenna"
        ),
    ]
    if task_query:
        candidates.insert(0, f"{task_query} passive intermodulation massive MIMO")

    calls: list[dict[str, Any]] = []
    categories = initial_args.get("categories")
    if not isinstance(categories, list) or not categories:
        categories = ["eess.SP", "cs.IT", "physics.app-ph"]
    for query in candidates:
        normalized = query.lower()
        if normalized in seen_queries:
            continue
        seen_queries.add(normalized)
        calls.append(
            {
                "query": query,
                "top_k": max(8, int(initial_args.get("top_k") or 5)),
                "categories": categories,
                "sort_by": "relevance",
            }
        )
        if len(calls) >= 4:
            break
    return tuple(calls)


def _required_tool_research_enabled(
    request: RunRequest,
    research_config: Mapping[str, Any] | None,
) -> bool:
    value = request.extra.get("enable_required_research")
    if value is None:
        value = request.extra.get("required_research")
    if value is None and research_config is not None:
        value = research_config.get("required_tools", True)
    if value is None:
        value = True
    return _truthy(value)


def _required_research_calls(
    request: RunRequest,
    *,
    network_enabled: bool,
    research_config: Mapping[str, Any] | None,
) -> tuple[tuple[str, dict[str, Any]], ...]:
    task_query = (
        request.user_request.strip()
        or "PIM cancellation FDD Massive MIMO beam layer switching"
    )
    calls: list[tuple[str, dict[str, Any]]] = [
        ("search.local_docs", {"query": task_query, "top_k": 5}),
        ("knowledge.kb_query", {"query": task_query, "zone": "literature", "top_k": 5}),
        ("code.repo_reader", {"path": "README.md"}),
        ("code.repo_reader", {"path": "libs/model.py"}),
        ("code.repo_reader", {"path": "model.py"}),
        (
            "knowledge.kb_query",
            {
                "query": (
                    "PIMC methodology RES PIM APE loss ablation baseline "
                    "compatibility memory polynomial router"
                ),
                "zone": "methodology",
                "top_k": 5,
            },
        ),
        (
            "knowledge.kb_query",
            {
                "query": (
                    "PIMC 8L prior runs RES loss hard top2 soft router "
                    "expert_count order"
                ),
                "zone": "run_archive",
                "top_k": 5,
            },
        ),
        (
            "knowledge.baseline_match",
            {
                "plan": {
                    "project": request.project,
                    "task": request.user_request,
                    "variables": ["router_type", "expert_count", "order"],
                    "metrics": list(PIMC_CORE_METRICS),
                },
                "threshold": 0.85,
            },
        ),
    ]
    if network_enabled:
        calls.append(
            (
                "search.arxiv_search",
                {
                    "query": (
                        "massive MIMO passive intermodulation cancellation "
                        "digital predistortion beamforming neural network"
                    ),
                    "top_k": 5,
                    "date_from": "2010-01-01",
                    "categories": ["eess.SP", "cs.IT", "physics.app-ph"],
                    "sort_by": "relevance",
                },
            ),
        )
        domains = _enabled_research_domains()
        if _web_research_enabled(research_config) and domains:
            calls.append(
                (
                    "search.web_search",
                    {
                        "query": f"{task_query} technical blog implementation notes",
                        "domains": domains,
                        "top_k": 5,
                    },
                )
            )
    return tuple(calls)


def _web_research_enabled(research_config: Mapping[str, Any] | None) -> bool:
    if research_config is None:
        return True
    return _truthy(research_config.get("web_search", True))


def _enabled_research_domains() -> list[str]:
    domains: list[str] = []
    for site in load_agent_research_sites("idea"):
        if not site.enabled:
            continue
        parsed = urllib.parse.urlparse(site.url)
        host = parsed.netloc or parsed.path
        if host:
            domains.append(host.removeprefix("www."))
    return domains


def prepare_research_pack(
    *,
    request: RunRequest,
    context: ContextPack,
    self_context: Sequence[SelfContextEntry] | None = None,
    research_config: Mapping[str, Any] | None = None,
    tool_observations: Sequence[Mapping[str, Any]] | None = None,
) -> ResearchPack:
    """Build and optionally persist deterministic research artifacts."""
    entries = tuple(self_context or load_idea_self_context())
    research_dir = research_dir_for_request(request)
    network_enabled = _network_research_enabled(request, research_config)
    tool_results = tuple(_normalize_tool_observation(item) for item in tool_observations or ())
    evidence_items = _evidence_items(
        request=request,
        context=context,
        self_context=entries,
        tool_observations=tool_results,
    )
    evidence_index: dict[str, Any] = {
        "schema": "idea_research_evidence.v1",
        "project": request.project,
        "created": _now(),
        "network_research": _network_research_status(network_enabled, tool_results),
        "quality": _summarize_tool_quality(tool_results),
        "tool_call_count": len(tool_results),
        "items": evidence_items,
    }
    source_summary_index = _source_summary_index(
        request=request,
        tool_observations=tool_results,
    )
    evidence_index["source_summary_count"] = len(source_summary_index["sources"])
    evidence_index["source_summaries"] = source_summary_index["sources"]
    plan_md = _render_research_plan(
        request=request,
        evidence_items=evidence_items,
        network_enabled=network_enabled,
        tool_observations=tool_results,
    )
    notes_md = _render_research_notes(evidence_items=evidence_items)
    summary_md = _render_research_summary(
        request=request,
        evidence_items=evidence_items,
        tool_observations=tool_results,
    )
    source_summary_md = _render_source_summary_index_md(source_summary_index)
    pack = ResearchPack(
        research_dir=research_dir,
        plan_md=plan_md,
        notes_md=notes_md,
        summary_md=summary_md,
        source_summary_md=source_summary_md,
        source_summary_index=source_summary_index,
        evidence_index=evidence_index,
        tool_results=tool_results,
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


def _network_research_status(
    network_enabled: bool,
    tool_observations: Sequence[Mapping[str, Any]],
) -> str:
    if not network_enabled:
        return "disabled"
    arxiv = [
        item for item in tool_observations if item.get("tool") == "search.arxiv_search"
    ]
    if not arxiv:
        return "enabled_not_fetched"
    if any(bool(item.get("ok")) for item in arxiv):
        return "enabled_fetched"
    return "enabled_fetch_failed"


def _normalize_tool_observation(item: Mapping[str, Any]) -> dict[str, Any]:
    observation = dict(item)
    output = _compact_tool_output(observation.get("output"))
    observation["output"] = output
    if not isinstance(observation.get("quality"), Mapping):
        quality = _assess_tool_output_quality(str(observation.get("tool") or ""), output)
        if quality:
            observation["quality"] = quality
    return observation


def _source_summary_index(
    *,
    request: RunRequest,
    tool_observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    for observation in tool_observations:
        if observation.get("tool") != "search.fetch_sources":
            continue
        output = observation.get("output")
        if not isinstance(output, Mapping):
            continue
        raw_sources = output.get("sources")
        if not isinstance(raw_sources, list):
            continue
        for raw in raw_sources:
            if not isinstance(raw, Mapping) or raw.get("ok") is not True:
                continue
            sources.append(
                {
                    "title": str(raw.get("title") or ""),
                    "url": str(raw.get("url") or ""),
                    "download_url": str(raw.get("download_url") or ""),
                    "source_type": str(raw.get("source_type") or ""),
                    "download_path": str(raw.get("download_path") or ""),
                    "summary_path": str(raw.get("summary_path") or ""),
                    "summary": str(raw.get("summary") or "")[:2400],
                    "excerpt": str(raw.get("excerpt") or "")[:1000],
                }
            )
    return {
        "schema": "idea_source_summaries.v1",
        "project": request.project,
        "created": _now(),
        "task": request.user_request,
        "sources": sources,
    }


def _render_source_summary_index_md(index: Mapping[str, Any]) -> str:
    sources = _as_mapping_list(index.get("sources"))
    lines = [
        "# Idea Source Summaries",
        "",
        f"- Project: `{index.get('project', '')}`",
        f"- Task: {index.get('task', '')}",
        f"- Source count: {len(sources)}",
        "",
    ]
    if not sources:
        lines.append("No downloaded paper/blog sources were available for context.")
        return "\n".join(lines) + "\n"
    for item in sources:
        lines.extend(
            [
                f"## {item.get('title', 'source')}",
                "",
                f"- URL: {item.get('url', '')}",
                f"- Download: {item.get('download_path', '')}",
                f"- Summary file: {item.get('summary_path', '')}",
                f"- Type: {item.get('source_type', '')}",
                "",
                str(item.get("summary") or "")[:2400],
                "",
            ]
        )
    return "\n".join(lines)


def _compact_tool_output(value: Any) -> Any:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key == "hits" and isinstance(item, list):
                out[key] = [_compact_tool_hit(hit) for hit in item[:5]]
            elif key in {"content", "summary", "excerpt"}:
                out[key] = str(item)[:1200]
            else:
                out[key] = _compact_tool_output(item)
        return out
    if isinstance(value, list):
        return [_compact_tool_output(item) for item in value[:10]]
    if isinstance(value, str):
        return value[:3000]
    return value


def _compact_tool_hit(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return _compact_tool_output(value)
    keys = (
        "title",
        "id",
        "url",
        "published",
        "score",
        "excerpt",
        "summary",
        "pdf_url",
        "metadata",
        "meta",
        "evidence_ref",
    )
    return {
        key: _compact_tool_output(value[key])
        for key in keys
        if key in value and value[key] not in (None, "")
    }


def _assess_tool_output_quality(tool_name: str, output: Any) -> dict[str, Any]:
    if tool_name == "search.arxiv_search":
        return {"literature_relevance": _assess_literature_relevance(output)}
    return {}


def _assess_literature_relevance(output: Any) -> dict[str, Any]:
    if not isinstance(output, Mapping):
        return {
            "status": "unknown",
            "total_hits": 0,
            "relevant_hits": 0,
            "precision": 0.0,
            "warnings": ["literature_search_unstructured_output"],
            "hits": [],
        }

    raw_hits = output.get("hits")
    if not isinstance(raw_hits, list) or not raw_hits:
        return {
            "status": "no_hits",
            "total_hits": 0,
            "relevant_hits": 0,
            "precision": 0.0,
            "warnings": ["literature_search_no_hits"],
            "hits": [],
        }

    annotations: list[dict[str, Any]] = []
    relevant_hits = 0
    for index, raw_hit in enumerate(raw_hits[:5], 1):
        hit = raw_hit if isinstance(raw_hit, Mapping) else {"title": str(raw_hit)}
        score, matched_concepts = _score_literature_hit(hit)
        relevant = _literature_hit_is_relevant(score, matched_concepts)
        if relevant:
            relevant_hits += 1
        annotations.append(
            {
                "index": index,
                "title": str(hit.get("title") or hit.get("id") or "")[:220],
                "url": str(hit.get("url") or hit.get("id") or "")[:260],
                "score": score,
                "matched_concepts": matched_concepts,
                "relevant": relevant,
            }
        )

    total_hits = len(raw_hits[:5])
    precision = round(relevant_hits / total_hits, 3) if total_hits else 0.0
    if relevant_hits == 0:
        status = "no_relevant_hits"
        warnings = ["literature_relevance_low"]
    elif precision < 0.4:
        status = "low_relevance"
        warnings = ["literature_relevance_low"]
    else:
        status = "pass"
        warnings = []

    return {
        "status": status,
        "total_hits": total_hits,
        "relevant_hits": relevant_hits,
        "precision": precision,
        "warnings": warnings,
        "hits": annotations,
    }


def _score_literature_hit(hit: Mapping[str, Any]) -> tuple[int, list[str]]:
    text = _text_blob(
        {
            "title": hit.get("title", ""),
            "summary": hit.get("summary", ""),
            "excerpt": hit.get("excerpt", ""),
            "metadata": hit.get("metadata", hit.get("meta", "")),
        }
    ).lower()
    score = 0
    matched: list[str] = []
    for group, terms, weight in LITERATURE_RELEVANCE_GROUPS:
        if any(term in text for term in terms):
            score += weight
            matched.append(group)
    return score, matched


def _literature_hit_is_relevant(score: int, matched_concepts: Sequence[str]) -> bool:
    concepts = set(matched_concepts)
    if "passive_intermodulation" in concepts and (
        concepts & {"massive_mimo", "rf_nonlinearity", "beam_layer_switching"}
    ):
        return True
    return (
        score >= 4
        and len(concepts) >= 2
        and bool(concepts & {"passive_intermodulation", "massive_mimo"})
    )


def _summarize_tool_quality(
    tool_observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    literature: list[dict[str, Any]] = []
    warnings: list[str] = []
    for item in tool_observations:
        relevance = _literature_relevance_quality(item)
        if relevance is None:
            continue
        args = item.get("args")
        output = item.get("output")
        summary = {
            "tool_id": str(item.get("id") or ""),
            "tool": str(item.get("tool") or ""),
            "status": str(relevance.get("status") or "unknown"),
            "total_hits": int(relevance.get("total_hits") or 0),
            "relevant_hits": int(relevance.get("relevant_hits") or 0),
            "precision": float(relevance.get("precision") or 0.0),
            "query": _observation_query(args, output),
            "follow_up_of": str(item.get("follow_up_of") or ""),
            "retry_reason": str(item.get("retry_reason") or ""),
        }
        literature.append(summary)
        for warning in _string_list(relevance.get("warnings")):
            if warning == "literature_relevance_low":
                continue
            if warning not in warnings:
                warnings.append(warning)
    if _literature_attempts_are_still_low(literature):
        warnings.append("literature_relevance_low")
    return {
        "warnings": warnings,
        "literature_relevance": literature,
    }


def _observation_query(args: Any, output: Any) -> str:
    if isinstance(args, Mapping):
        value = args.get("query") or args.get("q")
        if value:
            return str(value)
    if isinstance(output, Mapping):
        value = output.get("query") or output.get("q")
        if value:
            return str(value)
    return ""


def _literature_attempts_are_still_low(
    literature: Sequence[Mapping[str, Any]],
) -> bool:
    if not literature:
        return False
    has_pass = any(str(item.get("status") or "") == "pass" for item in literature)
    if has_pass:
        return False
    return any(
        str(item.get("status") or "") in LOW_LITERATURE_STATUSES
        for item in literature
    )


def _literature_relevance_quality(
    observation: Mapping[str, Any],
) -> dict[str, Any] | None:
    quality = observation.get("quality")
    if not isinstance(quality, Mapping):
        return None
    relevance = quality.get("literature_relevance")
    if not isinstance(relevance, Mapping):
        return None
    return {str(key): value for key, value in relevance.items()}


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
    (pack.research_dir / "source_summaries.v1.md").write_text(
        pack.source_summary_md, encoding="utf-8"
    )
    (pack.research_dir / "source_summaries.v1.json").write_text(
        json.dumps(pack.source_summary_index, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (pack.research_dir / "evidence_index.v1.json").write_text(
        json.dumps(pack.evidence_index, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (pack.research_dir / "tool_results.v1.json").write_text(
        json.dumps(list(pack.tool_results), indent=2, ensure_ascii=False, default=str),
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
    if pack.source_summary_index.get("sources"):
        upstream["idea_source_summaries"] = pack.source_summary_md
    system = (
        context.system
        + "\n\nProposal constraint: first use code repository evidence and "
        "background context, then use external paper/blog source summaries only "
        "where they are relevant. Base the proposal on idea_research_summary, "
        "idea_evidence_index, and idea_source_summaries when present. If "
        "evidence is weak, state the evidence gap instead of overstating confidence."
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
            "idea_research_tool_call_count": len(pack.tool_results),
            "idea_source_summary_count": len(pack.source_summary_index.get("sources", [])),
        },
    )


def normalize_idea_metadata(
    metadata: Mapping[str, Any],
    research_pack: ResearchPack,
) -> dict[str, Any]:
    """Add deterministic proposal scaffolding without changing core claims."""
    normalized = dict(metadata)
    normalized["schema"] = "proposal.v1"
    normalized["project"] = str(
        normalized.get("project") or research_pack.evidence_index.get("project") or "pimc"
    )
    normalized["agent"] = "idea"
    normalized["created"] = _now()
    normalized["constraints"] = _ensure_constraints(normalized.get("constraints"))
    normalized["evidence_refs"] = _ensure_evidence_refs(
        normalized.get("evidence_refs"),
        research_pack,
    )
    normalized["experiment_hint"] = _ensure_experiment_hint(
        normalized.get("experiment_hint")
    )
    normalized["risk_register"] = _ensure_risk_register(
        normalized.get("risk_register")
    )
    normalized["downstream_requirements"] = _ensure_downstream_requirements(
        normalized.get("downstream_requirements")
    )
    return normalized


def merge_quality_warnings(
    existing: Any,
    generated: Sequence[str],
) -> list[str]:
    """Merge model-authored and deterministic warnings while preserving order."""
    merged: list[str] = []
    for item in [*_as_list(existing), *generated]:
        text = str(item).strip()
        if text and text not in merged:
            merged.append(text)
    return merged


def validate_idea_quality(
    metadata: Mapping[str, Any],
    *,
    evidence_index: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return warning codes for V2 Idea quality checks."""
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
    elif evidence_index is not None and not _has_research_evidence_anchor(
        metadata.get("evidence_refs"),
        evidence_index,
    ):
        warnings.append("evidence_refs_not_in_research_index")
    if evidence_index is not None and _has_low_literature_relevance(evidence_index):
        warnings.append("literature_relevance_low")
    if evidence_index is not None and _missing_source_summaries_required(evidence_index):
        warnings.append("source_summaries_missing")
    if _has_placeholder_literature(metadata.get("related_literature")):
        warnings.append("related_literature_placeholder")
    constraints = metadata.get("constraints", [])
    constraints_text = _text_blob(constraints)
    if "baseline" not in constraints_text.lower() and "兼容" not in constraints_text:
        warnings.append("missing_baseline_compat_constraint")
    if not _experiment_hint_mentions_allowed_knob(metadata.get("experiment_hint")):
        warnings.append("experiment_hint_missing_allowed_knob")
    if not _mentions_core_metric(
        metadata.get("hypothesis"),
        metadata.get("testable_predictions"),
        metadata.get("experiment_hint"),
    ):
        warnings.append("missing_core_metric")
    if _res_direction_reversed(
        metadata.get("hypothesis"),
        metadata.get("testable_predictions"),
        metadata.get("experiment_hint"),
    ):
        warnings.append("res_direction_reversed")
    if not _non_empty_list(metadata.get("risk_register")):
        warnings.append("missing_risk_register")
    if not _non_empty_list(metadata.get("downstream_requirements")):
        warnings.append("missing_downstream_requirements")
    return warnings


def _ensure_constraints(raw: Any) -> list[str]:
    items = [str(item).strip() for item in _as_list(raw) if str(item).strip()]
    if not _contains_any(items, ("baseline", "Paper_Total_0327", "兼容")):
        items.append(
            "baseline_compat: additive-only; do not modify Paper_Total_0327 or "
            "forward(x, stream_label)."
        )
    if not _contains_any(items, ("RES", "loss", "-26", "0.04")):
        items.append("metric_gate: RES mean <= -26 dB; loss max <= 0.04.")
    return items


def _ensure_evidence_refs(raw: Any, research_pack: ResearchPack) -> list[Any]:
    refs = _as_list(raw)
    if _has_research_evidence_anchor(refs, research_pack.evidence_index):
        return refs

    seen = {_evidence_ref_key(item) for item in refs}
    for item in _fallback_evidence_refs(research_pack.evidence_index):
        ref = _evidence_ref_key(item)
        if ref and ref not in seen:
            refs.append(item)
            seen.add(ref)
    return refs


def _ensure_experiment_hint(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        hint = dict(raw)
    elif isinstance(raw, str) and raw.strip():
        hint = {"notes": raw.strip()}
    else:
        hint = {}

    variables = _string_list(hint.get("variables"))
    _append_missing(variables, ["router_type", "expert_count", "order"])
    hint["variables"] = variables

    metrics = _string_list(hint.get("metrics"))
    _append_missing(metrics, list(PIMC_CORE_METRICS))
    hint["metrics"] = metrics

    ablations = _as_list(hint.get("minimal_ablations"))
    if not ablations:
        ablations = [
            {
                "name": "soft_mem8_reference",
                "config": {
                    "router_type": "soft",
                    "expert_count": 8,
                    "order": 7,
                },
            },
            {
                "name": "hard_top2_mem12_candidate",
                "config": {
                    "router_type": "hard-top2",
                    "expert_count": 12,
                    "order": 7,
                },
            },
        ]
    hint["minimal_ablations"] = ablations
    return hint


def _ensure_risk_register(raw: Any) -> list[Any]:
    items = _as_list(raw)
    if not _contains_any(items, ("baseline", "Paper_Total_0327", "Gate 5")):
        items.append(
            {
                "risk": (
                    "Proposal or downstream code may accidentally touch the "
                    "baseline-protected Paper_Total_0327 surface."
                ),
                "severity": "high",
                "mitigation": (
                    "Keep implementation additive and route baseline-sensitive "
                    "changes through Gate 5 review."
                ),
            }
        )
    if not _contains_any(items, ("evidence", "证据", "network", "联网")):
        items.append(
            {
                "risk": "Local V2 research may miss recent external literature.",
                "severity": "medium",
                "mitigation": (
                    "Treat external citations as evidence gaps until network "
                    "research is explicitly enabled."
                ),
            }
        )
    return items


def _ensure_downstream_requirements(raw: Any) -> list[Any]:
    items = [str(item).strip() for item in _as_list(raw) if str(item).strip()]
    if not _contains_any(items, ("expert_count", "router_type", "order")):
        items.append(
            "Experiment Agent must expand router_type x expert_count x order "
            "into comparable RES/loss ablations."
        )
    if not _contains_any(items, ("baseline", "forward", "Paper_Total_0327")):
        items.append(
            "Coding Agent must preserve Paper_Total_0327 and "
            "forward(x, stream_label); only additive modules are allowed."
        )
    return items


def _fallback_evidence_refs(evidence_index: Mapping[str, Any]) -> list[dict[str, str]]:
    raw_items = evidence_index.get("items", [])
    if not isinstance(raw_items, list):
        return []
    candidates = [item for item in raw_items if isinstance(item, Mapping)]
    priority = {"tool": 0, "project": 1, "context": 2, "self_context": 3}
    candidates.sort(key=lambda item: priority.get(str(item.get("kind", "")), 9))
    refs: list[dict[str, str]] = []
    for item in candidates[:3]:
        ref = str(item.get("id", "")).strip()
        if not ref:
            continue
        excerpt = str(item.get("excerpt", "")).strip().replace("\n", " ")
        refs.append(
            {
                "ref": ref,
                "kind": str(item.get("kind", "research")),
                "summary": excerpt[:220] or str(item.get("title", ""))[:220],
            }
        )
    return refs


def _has_research_evidence_anchor(
    evidence_refs: Any,
    evidence_index: Mapping[str, Any],
) -> bool:
    raw_items = evidence_index.get("items", [])
    if not isinstance(raw_items, list):
        return False
    ids = {
        str(item.get("id", "")).strip()
        for item in raw_items
        if isinstance(item, Mapping) and item.get("id")
    }
    if not ids:
        return False
    return any(_evidence_ref_key(item) in ids for item in _as_list(evidence_refs))


def _has_low_literature_relevance(evidence_index: Mapping[str, Any]) -> bool:
    quality = evidence_index.get("quality")
    if isinstance(quality, Mapping):
        warnings = quality.get("warnings")
        if "literature_relevance_low" in _string_list(warnings):
            return True
        literature = _as_mapping_list(quality.get("literature_relevance"))
        if literature:
            return _literature_attempts_are_still_low(literature)

    items = evidence_index.get("items")
    if not isinstance(items, list):
        return False
    literature_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        relevance = _literature_relevance_quality(item)
        if relevance:
            literature_items.append(relevance)
    return _literature_attempts_are_still_low(literature_items)


def _missing_source_summaries_required(evidence_index: Mapping[str, Any]) -> bool:
    try:
        source_count = int(evidence_index.get("source_summary_count") or 0)
    except (TypeError, ValueError):
        source_count = 0
    if source_count > 0:
        return False

    quality = evidence_index.get("quality")
    if isinstance(quality, Mapping):
        literature = _as_mapping_list(quality.get("literature_relevance"))
        if any(str(item.get("status") or "") == "pass" for item in literature):
            return True

    items = evidence_index.get("items")
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, Mapping):
            continue
        tool = str(item.get("tool") or "")
        if tool == "search.web_search" and bool(item.get("ok")):
            return True
        if tool == "search.fetch_sources" and not bool(item.get("ok")):
            return True
    return False


def _has_placeholder_literature(raw: Any) -> bool:
    text = _text_blob(raw).lower()
    return any(marker in text for marker in PLACEHOLDER_CITATION_MARKERS)


def _evidence_ref_key(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, Mapping):
        raw = item.get("ref") or item.get("id") or item.get("path")
        return str(raw).strip() if raw is not None else ""
    return ""


def _experiment_hint_mentions_allowed_knob(raw: Any) -> bool:
    if not raw:
        return False
    text = _text_blob(raw)
    return any(knob in text for knob in PIMC_ALLOWED_KNOBS)


def _mentions_core_metric(*values: Any) -> bool:
    text = _text_blob(values).lower()
    return any(metric.lower() in text for metric in PIMC_CORE_METRICS)


def _res_direction_reversed(*values: Any) -> bool:
    text = _text_blob(values).lower().replace(" ", "")
    return any(
        phrase in text
        for phrase in (
            "res越高越好",
            "res越大越好",
            "reshigherisbetter",
            "higherresisbetter",
        )
    )


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


def _as_mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [{str(key): item[key] for key in item} for item in value if isinstance(item, Mapping)]


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in _as_list(value) if str(item).strip()]


def _append_missing(items: list[str], additions: Sequence[str]) -> None:
    lowered = {item.lower() for item in items}
    for item in additions:
        if item.lower() not in lowered:
            items.append(item)
            lowered.add(item.lower())


def _contains_any(value: Any, needles: Sequence[str]) -> bool:
    text = _text_blob(value).lower()
    return any(needle.lower() in text for needle in needles)


def _text_blob(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(_text_blob(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_text_blob(item) for item in value)
    return str(value)


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
    tool_observations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for observation in tool_observations:
        output = observation.get("output")
        title = str(observation.get("tool", "tool"))
        status = str(observation.get("status") or "unknown")
        item = {
            "id": str(observation.get("id") or f"tool_{len(items) + 1}"),
            "kind": "tool",
            "title": f"{title} ({status})",
            "excerpt": _tool_observation_excerpt(observation),
            "tool": title,
            "ok": bool(observation.get("ok", False)),
            "error": str(observation.get("error") or ""),
            "evidence_refs": observation.get("evidence_refs", []),
            "output_kind": type(output).__name__,
        }
        quality = observation.get("quality")
        if isinstance(quality, Mapping):
            item["quality"] = {str(key): quality[key] for key in quality}
        items.append(item)
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


def _tool_observation_excerpt(observation: Mapping[str, Any]) -> str:
    output = observation.get("output")
    if isinstance(output, Mapping):
        query = str(output.get("query") or output.get("zone") or "")
        hits = output.get("hits")
        if isinstance(hits, list):
            snippets: list[str] = []
            for hit in hits[:3]:
                if isinstance(hit, Mapping):
                    title = str(hit.get("title") or hit.get("excerpt") or "")
                    snippets.append(title[:280])
                else:
                    snippets.append(str(hit)[:280])
            hit_text = " | ".join(item for item in snippets if item)
            quality_suffix = _tool_quality_excerpt_suffix(observation)
            return f"query={query}; hits={len(hits)}{quality_suffix}; {hit_text}"[
                :1200
            ]
        return json.dumps(output, ensure_ascii=False, default=str)[:1200]
    if output is None:
        error = str(observation.get("error") or "")
        return error[:1200] if error else ""
    return str(output)[:1200]


def _tool_quality_excerpt_suffix(observation: Mapping[str, Any]) -> str:
    relevance = _literature_relevance_quality(observation)
    if relevance is None:
        return ""
    status = str(relevance.get("status") or "unknown")
    relevant_hits = int(relevance.get("relevant_hits") or 0)
    total_hits = int(relevance.get("total_hits") or 0)
    return f"; relevant={relevant_hits}/{total_hits}; quality={status}"


def _render_research_plan(
    *,
    request: RunRequest,
    evidence_items: list[dict[str, Any]],
    network_enabled: bool,
    tool_observations: Sequence[Mapping[str, Any]],
) -> str:
    network_status = (
        "enabled; arXiv search is part of the required real-mode pass"
        if network_enabled
        else "disabled by config"
    )
    lines = [
        "# Idea Research Plan",
        "",
        f"- Project: `{request.project}`",
        f"- Task: {request.user_request or '(empty task)'}",
        (
            "- Source order: project rules + Idea self context -> local KB/history "
            "-> code.repo_reader -> external papers/blogs -> downloaded source summaries."
        ),
        f"- Network research: {network_status}.",
        f"- Tool calls planned/executed: {len(tool_observations)}.",
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
    tool_observations: Sequence[Mapping[str, Any]],
) -> str:
    evidence_ids = ", ".join(str(item["id"]) for item in evidence_items[:8])
    action_lines = _render_research_action_lines(tool_observations)
    quality_lines = _render_evidence_quality_lines(tool_observations)
    gap_lines = _render_evidence_gap_lines(tool_observations)
    source_lines = _render_downloaded_source_lines(tool_observations)
    return (
        "# Idea Research Summary\n\n"
        f"## Task\n{request.user_request or '(empty task)'}\n\n"
        "## Research Actions\n"
        f"{action_lines}\n\n"
        "## Downloaded Sources\n"
        f"{source_lines}\n\n"
        "## Evidence Quality\n"
        f"{quality_lines}\n\n"
        "## Evidence Used\n"
        f"{evidence_ids or 'No local evidence found.'}\n\n"
        "## Evidence Gaps\n"
        f"{gap_lines}\n\n"
        "## Proposal Guidance\n"
        "- Prefer hypotheses that can be tested with RES / PIM / APE / loss.\n"
        "- Preserve project baseline compatibility constraints.\n"
        "- Provide at least one minimal experiment hint for the Experiment Agent.\n"
    )


def _render_research_action_lines(
    tool_observations: Sequence[Mapping[str, Any]],
) -> str:
    if not tool_observations:
        return "- Required tool research was not executed in this run."
    lines: list[str] = []
    for item in tool_observations:
        tool = str(item.get("tool") or "tool")
        status = str(item.get("status") or "unknown")
        output = item.get("output")
        query = _observation_query(item.get("args"), output)
        count = ""
        if isinstance(output, Mapping) and isinstance(output.get("hits"), list):
            count = f"; hits={len(output['hits'])}"
        quality = _tool_quality_excerpt_suffix(item)
        if quality:
            count = f"{count}{quality}"
        error = str(item.get("error") or "")
        suffix = f"; error={error[:180]}" if error else count
        label = "follow-up" if item.get("follow_up_of") else "initial"
        query_suffix = f"; query={query[:140]}" if query else ""
        lines.append(f"- {tool} [{label}]: {status}{suffix}{query_suffix}")
    return "\n".join(lines)


def _render_downloaded_source_lines(
    tool_observations: Sequence[Mapping[str, Any]],
) -> str:
    rows: list[str] = []
    for observation in tool_observations:
        if observation.get("tool") != "search.fetch_sources":
            continue
        output = observation.get("output")
        if not isinstance(output, Mapping):
            continue
        sources = output.get("sources")
        if not isinstance(sources, list):
            continue
        for item in sources:
            if not isinstance(item, Mapping):
                continue
            title = str(item.get("title") or "source")[:160]
            if item.get("ok") is True:
                rows.append(
                    "- "
                    f"{title}: {item.get('source_type', '')}; "
                    f"download={item.get('download_path', '')}; "
                    f"summary={item.get('summary_path', '')}"
                )
            else:
                rows.append(
                    "- "
                    f"{title}: download failed; error={str(item.get('error') or '')[:180]}"
                )
    if not rows:
        return "- No external PDF/blog source was downloaded in this run."
    return "\n".join(rows)


def _render_evidence_quality_lines(
    tool_observations: Sequence[Mapping[str, Any]],
) -> str:
    rows: list[str] = []
    for item in tool_observations:
        relevance = _literature_relevance_quality(item)
        if relevance is None:
            continue
        tool = str(item.get("tool") or "tool")
        status = str(relevance.get("status") or "unknown")
        relevant_hits = int(relevance.get("relevant_hits") or 0)
        total_hits = int(relevance.get("total_hits") or 0)
        precision = float(relevance.get("precision") or 0.0)
        query = _observation_query(item.get("args"), item.get("output"))
        label = "follow-up" if item.get("follow_up_of") else "initial"
        rows.append(
            "- "
            f"{tool} [{label}]: {status}; relevant={relevant_hits}/{total_hits}; "
            f"precision={precision:.2f}; query={query[:160]}"
        )
    if not rows:
        return "- No tool-level evidence quality signal recorded."
    return "\n".join(rows)


def _render_evidence_gap_lines(
    tool_observations: Sequence[Mapping[str, Any]],
) -> str:
    gaps: list[str] = []
    if not tool_observations:
        gaps.append("- No required tool pass was recorded; treat evidence as local-only.")
    for item in tool_observations:
        if item.get("ok"):
            continue
        tool = str(item.get("tool") or "tool")
        error = str(item.get("error") or "unknown error")
        gaps.append(f"- {tool} did not return usable evidence: {error[:220]}")
    literature = [
        item
        for item in tool_observations
        if _literature_relevance_quality(item) is not None
    ]
    if _literature_attempts_are_still_low(
        [
            relevance
            for item in literature
            if (relevance := _literature_relevance_quality(item)) is not None
        ]
    ):
        last = literature[-1]
        relevance = _literature_relevance_quality(last) or {}
        tool = str(last.get("tool") or "tool")
        relevant_hits = int(relevance.get("relevant_hits") or 0)
        total_hits = int(relevance.get("total_hits") or 0)
        attempts = len(literature)
        gaps.append(
            "- "
            f"{tool} still returned low-relevance literature after {attempts} "
            f"attempt(s) ({relevant_hits}/{total_hits} useful hits in the last "
            "attempt); treat external papers as an evidence gap until rerun "
            "with a curated source."
        )
    if not gaps:
        gaps.append("- No blocking evidence gap detected in the required tool pass.")
    return "\n".join(gaps)


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
