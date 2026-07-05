"""V2 Idea Agent research/context behavior."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.base import ContextPack, RunRequest
from app.agents.idea.agent import IdeaAgent
from app.agents.idea.acceptance import write_idea_acceptance_report
from app.agents.idea.research import (
    gather_required_research_tools,
    load_idea_self_context,
    normalize_idea_metadata,
    prepare_research_pack,
    validate_idea_quality,
)
from app.harness.llm.mock_provider import build_fake_metadata
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.schema.validator import validate_document
from app.harness.tools.registry import ToolResult
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunStore


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "QWEN_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
        "CUSTOM_ENDPOINT_URL",
        "CUSTOM_ENDPOINT_API_KEY",
    ):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("MARS_MOCK_MODE", "always")
    monkeypatch.setenv("LOCAL_VLLM_BASE_URL", "")
    import app.settings as settings_mod

    settings_mod._settings = None


def test_loads_idea_self_context_assets() -> None:
    entries = load_idea_self_context()
    paths = {entry.path for entry in entries}
    assert any(path.startswith("docs/") for path in paths)
    assert any(path.startswith("prompts/") for path in paths)
    assert any(path.startswith("examples/") for path in paths)
    assert any(path.startswith("evals/") for path in paths)
    assert "agent.py" in paths
    assert "research.py" in paths
    assert any("先调研" in entry.text for entry in entries)


@pytest.mark.asyncio
async def test_idea_context_includes_self_context_and_project_rules(
    tmp_path: Path,
) -> None:
    agent = IdeaAgent()
    request = RunRequest(
        project="pimc",
        user_request="如何在 8L 配置下降低 PIMC 资源并保持 RES?",
        extra={"idea_research_dir": str(tmp_path / "research")},
    )
    context = await agent.build_context(request)
    assert "Project AGENTS.md" in context.project
    assert "idea_self_context" in context.upstream
    assert "idea_research_sites" in context.upstream
    assert "arXiv" in context.upstream["idea_research_sites"]
    assert "Idea Agent 自上下文" in context.upstream["idea_self_context"]


@pytest.mark.asyncio
async def test_idea_draft_writes_research_pack_before_valid_proposal(
    tmp_path: Path,
) -> None:
    research_dir = tmp_path / "idea" / "research"
    agent = IdeaAgent()
    request = RunRequest(
        project="pimc",
        user_request="如何在 8L 配置下降低 PIMC 资源并保持 RES?",
        extra={"idea_research_dir": str(research_dir)},
    )
    context = await agent.build_context(request)
    artifact = await agent.draft(request, context)

    assert (research_dir / "research_plan.v1.md").exists()
    assert (research_dir / "research_notes.v1.md").exists()
    assert (research_dir / "research_summary.v1.md").exists()
    evidence_path = research_dir / "evidence_index.v1.json"
    assert evidence_path.exists()
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["items"]
    assert evidence["network_research"] == "enabled_not_fetched"
    assert evidence["tool_call_count"] == 0
    assert any(item["kind"] == "self_context" for item in evidence["items"])
    assert (research_dir / "tool_results.v1.json").exists()

    result = validate_document(artifact.text, expected_schema="proposal.v1")
    assert result.valid, result.errors
    assert "research_artifacts" in artifact.metadata
    assert artifact.metadata["research_artifacts"]["research_dir"] == str(research_dir)
    assert artifact.metadata["research_artifacts"]["tool_results"] == (
        "research/tool_results.v1.json"
    )


def test_research_pack_records_network_research_toggle(tmp_path: Path) -> None:
    request = RunRequest(
        project="pimc",
        user_request="联网调研是否可选?",
        extra={
            "idea_research_dir": str(tmp_path / "research"),
            "enable_network_research": "true",
        },
    )
    context = ContextPack(system="", project="", task=request.user_request)
    pack = prepare_research_pack(
        request=request,
        context=context,
        research_config={"enable_network": False},
    )
    assert pack.evidence_index["network_research"] == "enabled_not_fetched"
    assert "enabled; arXiv search" in pack.plan_md


def test_research_pack_indexes_tool_observations(tmp_path: Path) -> None:
    request = RunRequest(
        project="pimc",
        user_request="真实调研工具结果要进入 evidence index",
        extra={"idea_research_dir": str(tmp_path / "research")},
    )
    context = ContextPack(system="", project="", task=request.user_request)
    pack = prepare_research_pack(
        request=request,
        context=context,
        research_config={"enable_network": True},
        tool_observations=[
            {
                "id": "tool_1",
                "tool": "search.arxiv_search",
                "args": {"query": "massive MIMO PIM cancellation"},
                "ok": True,
                "status": "success",
                "output": {
                    "query": "massive MIMO PIM cancellation",
                    "hits": [
                        {
                            "title": "PIM cancellation for massive MIMO",
                            "summary": "A bounded external research hit.",
                            "url": "https://arxiv.org/abs/0000.00000",
                        }
                    ],
                },
                "evidence_refs": ["https://arxiv.org/abs/0000.00000"],
            }
        ],
    )

    assert pack.evidence_index["network_research"] == "enabled_fetched"
    assert pack.evidence_index["tool_call_count"] == 1
    assert pack.evidence_index["items"][0]["kind"] == "tool"
    assert pack.evidence_index["items"][0]["id"] == "tool_1"
    assert pack.evidence_index["quality"]["warnings"] == []
    assert (tmp_path / "research" / "tool_results.v1.json").exists()


def test_research_pack_flags_low_relevance_arxiv_hits(tmp_path: Path) -> None:
    request = RunRequest(
        project="pimc",
        user_request="真实调研不能把跑偏论文当有效证据",
        extra={"idea_research_dir": str(tmp_path / "research")},
    )
    context = ContextPack(system="", project="", task=request.user_request)
    pack = prepare_research_pack(
        request=request,
        context=context,
        research_config={"enable_network": True},
        tool_observations=[
            {
                "id": "tool_1",
                "tool": "search.arxiv_search",
                "args": {"query": "massive MIMO passive intermodulation"},
                "ok": True,
                "status": "success",
                "output": {
                    "query": "massive MIMO passive intermodulation",
                    "hits": [
                        {
                            "title": "Bregman proximal methods for KKT systems",
                            "summary": "A paper about generic optimization complexity.",
                            "url": "https://arxiv.org/abs/2606.00001",
                        }
                    ],
                },
                "evidence_refs": ["https://arxiv.org/abs/2606.00001"],
            }
        ],
    )

    assert pack.evidence_index["quality"]["warnings"] == ["literature_relevance_low"]
    assert "no_relevant_hits" in pack.summary_md
    assert "low-relevance literature" in pack.summary_md
    warnings = validate_idea_quality(
        build_fake_metadata("proposal.v1", seed="abc123"),
        evidence_index=pack.evidence_index,
    )
    assert "literature_relevance_low" in warnings


def test_research_pack_clears_low_relevance_after_followup_hit(tmp_path: Path) -> None:
    request = RunRequest(
        project="pimc",
        user_request="首轮 arXiv 跑偏后必须继续查",
        extra={"idea_research_dir": str(tmp_path / "research")},
    )
    context = ContextPack(system="", project="", task=request.user_request)
    pack = prepare_research_pack(
        request=request,
        context=context,
        research_config={"enable_network": True},
        tool_observations=[
            {
                "id": "tool_1",
                "tool": "search.arxiv_search",
                "args": {"query": "massive MIMO passive intermodulation"},
                "ok": True,
                "status": "success",
                "output": {
                    "query": "massive MIMO passive intermodulation",
                    "hits": [
                        {
                            "title": "Bregman proximal methods for KKT systems",
                            "summary": "Generic optimization complexity.",
                            "url": "https://arxiv.org/abs/2606.00001",
                        }
                    ],
                },
            },
            {
                "id": "tool_2",
                "tool": "search.arxiv_search",
                "args": {
                    "query": (
                        "passive intermodulation cancellation massive MIMO "
                        "digital predistortion"
                    ),
                    "follow_up_of": "tool_1",
                    "retry_reason": "literature_relevance_low",
                },
                "follow_up_of": "tool_1",
                "retry_reason": "literature_relevance_low",
                "ok": True,
                "status": "success",
                "output": {
                    "query": (
                        "passive intermodulation cancellation massive MIMO "
                        "digital predistortion"
                    ),
                    "hits": [
                        {
                            "title": (
                                "Passive intermodulation cancellation for "
                                "massive MIMO digital predistortion"
                            ),
                            "summary": (
                                "RF nonlinear distortion cancellation with "
                                "beamforming-aware massive MIMO experiments."
                            ),
                            "url": "https://arxiv.org/abs/2606.12345",
                        }
                    ],
                },
            },
        ],
    )

    assert pack.evidence_index["quality"]["warnings"] == []
    assert pack.evidence_index["quality"]["literature_relevance"][0]["status"] == (
        "no_relevant_hits"
    )
    assert pack.evidence_index["quality"]["literature_relevance"][1]["status"] == "pass"
    assert "search.arxiv_search [follow-up]: pass" in pack.summary_md
    warnings = validate_idea_quality(
        build_fake_metadata("proposal.v1", seed="followup-ok"),
        evidence_index=pack.evidence_index,
    )
    assert "literature_relevance_low" not in warnings


def test_research_pack_accepts_relevant_arxiv_hits(tmp_path: Path) -> None:
    request = RunRequest(
        project="pimc",
        user_request="真实调研要识别相关 PIMC 论文",
        extra={"idea_research_dir": str(tmp_path / "research")},
    )
    context = ContextPack(system="", project="", task=request.user_request)
    pack = prepare_research_pack(
        request=request,
        context=context,
        research_config={"enable_network": True},
        tool_observations=[
            {
                "id": "tool_1",
                "tool": "search.arxiv_search",
                "args": {"query": "massive MIMO PIM cancellation"},
                "ok": True,
                "status": "success",
                "output": {
                    "query": "massive MIMO PIM cancellation",
                    "hits": [
                        {
                            "title": (
                                "Passive intermodulation cancellation for "
                                "massive MIMO digital predistortion"
                            ),
                            "summary": (
                                "RF nonlinear distortion cancellation with "
                                "beamforming-aware massive MIMO experiments."
                            ),
                            "url": "https://arxiv.org/abs/2606.12345",
                        }
                    ],
                },
                "evidence_refs": ["https://arxiv.org/abs/2606.12345"],
            }
        ],
    )

    assert pack.evidence_index["quality"]["warnings"] == []
    assert "pass; relevant=1/1" in pack.summary_md


@pytest.mark.asyncio
async def test_required_research_fetches_pdf_after_relevant_arxiv_hit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeRegistry:
        async def dispatch(
            self,
            tool_name: str,
            args: dict[str, object],
            ctx: object,
        ) -> ToolResult:
            calls.append((tool_name, args))
            if tool_name == "search.arxiv_search":
                return ToolResult(
                    ok=True,
                    output={
                        "query": args.get("query"),
                        "hits": [
                            {
                                "title": (
                                    "Neural Network Based Framework for Passive "
                                    "Intermodulation Cancellation in MIMO Systems"
                                ),
                                "summary": (
                                    "Passive intermodulation cancellation for "
                                    "MIMO systems with RF nonlinear distortion."
                                ),
                                "url": "https://arxiv.org/abs/2509.19382v2",
                                "pdf_url": "https://arxiv.org/pdf/2509.19382v2.pdf",
                            }
                        ],
                    },
                    evidence_refs=["https://arxiv.org/abs/2509.19382v2"],
                    duration_ms=12.0,
                )
            if tool_name == "search.fetch_sources":
                sources = args.get("sources")
                return ToolResult(
                    ok=True,
                    output={
                        "download_dir": str(tmp_path / "research" / "downloads"),
                        "sources": [
                            {
                                "title": "Neural Network Based Framework",
                                "ok": True,
                                "source_type": "pdf",
                                "url": "https://arxiv.org/abs/2509.19382v2",
                                "download_url": "https://arxiv.org/pdf/2509.19382v2.pdf",
                                "download_path": "runs/r1/idea/research/downloads/pim.pdf",
                                "summary_path": "runs/r1/idea/research/downloads/pim.summary.md",
                                "summary": "Downloaded PDF summary.",
                            }
                        ]
                        if sources
                        else [],
                    },
                    evidence_refs=["runs/r1/idea/research/downloads/pim.pdf"],
                    duration_ms=20.0,
                )
            return ToolResult(ok=True, output={"hits": []}, duration_ms=1.0)

    import app.harness.tools.registry as registry_mod

    monkeypatch.setattr(registry_mod, "get_registry", lambda: FakeRegistry())
    request = RunRequest(
        project="pimc",
        user_request="PIMC 8L 路由简化需要真实论文调研",
        extra={
            "run_id": "r1",
            "run_root": str(tmp_path / "run"),
            "idea_research_dir": str(tmp_path / "run" / "idea" / "research"),
        },
    )

    observations = await gather_required_research_tools(
        request=request,
        research_config={
            "required_tools": True,
            "enable_network": True,
            "source_downloads": True,
            "web_search": False,
        },
        real_provider=True,
    )

    tool_names = [name for name, _args in calls]
    assert "search.arxiv_search" in tool_names
    assert "search.fetch_sources" in tool_names
    fetch_args = next(args for name, args in calls if name == "search.fetch_sources")
    assert fetch_args["sources"][0]["pdf_url"] == "https://arxiv.org/pdf/2509.19382v2.pdf"  # type: ignore[index]
    assert any(item.get("tool") == "search.fetch_sources" and item.get("ok") for item in observations)


def test_idea_quality_warns_when_relevant_source_was_not_summarized(tmp_path: Path) -> None:
    request = RunRequest(
        project="pimc",
        user_request="相关论文命中后必须下载摘要进入上下文",
        extra={"idea_research_dir": str(tmp_path / "research")},
    )
    context = ContextPack(system="", project="", task=request.user_request)
    pack = prepare_research_pack(
        request=request,
        context=context,
        research_config={"enable_network": True},
        tool_observations=[
            {
                "id": "tool_1",
                "tool": "search.arxiv_search",
                "args": {"query": "massive MIMO PIM cancellation"},
                "ok": True,
                "status": "success",
                "output": {
                    "query": "massive MIMO PIM cancellation",
                    "hits": [
                        {
                            "title": (
                                "Passive intermodulation cancellation for "
                                "massive MIMO digital predistortion"
                            ),
                            "summary": (
                                "RF nonlinear distortion cancellation with "
                                "beamforming-aware massive MIMO experiments."
                            ),
                            "url": "https://arxiv.org/abs/2606.12345",
                        }
                    ],
                },
                "evidence_refs": ["https://arxiv.org/abs/2606.12345"],
            }
        ],
    )

    metadata = build_fake_metadata("proposal.v1", seed="source-missing")
    warnings = validate_idea_quality(metadata, evidence_index=pack.evidence_index)
    assert "source_summaries_missing" in warnings


def test_idea_acceptance_report_checks_real_run_path_and_workspace(tmp_path: Path) -> None:
    store = RunStore(runs_root=tmp_path / "runs")
    run = store.create(
        task="pimc_8l_acceptance",
        project="pimc",
        entrypoint="idea",
        user_request="如何在 8L 配置下降低 PIMC 资源并保持 RES?",
    )
    metadata = build_fake_metadata("proposal.v1", seed="acceptance")
    proposal = fm_dumps(metadata, "# Proposal\n\n接受真实验收。")
    ref = ArtifactStore(run).write(
        text=proposal,
        agent_dir="idea",
        stem="idea_proposal",
        expected_schema="proposal.v1",
        version="v1",
    )
    research_dir = run.subdir("idea") / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    tool_results = [
        {"id": "tool_1", "tool": "search.local_docs", "ok": True, "status": "success"},
        {"id": "tool_2", "tool": "knowledge.kb_query", "ok": True, "status": "success"},
        {"id": "tool_3", "tool": "code.repo_reader", "ok": True, "status": "success"},
        {"id": "tool_4", "tool": "knowledge.baseline_match", "ok": True, "status": "success"},
        {
            "id": "tool_5",
            "tool": "search.arxiv_search",
            "ok": True,
            "status": "success",
            "quality": {
                "literature_relevance": {
                    "status": "pass",
                    "total_hits": 1,
                    "relevant_hits": 1,
                    "precision": 1.0,
                    "hits": [
                        {
                            "index": 1,
                            "title": "Passive intermodulation cancellation for massive MIMO",
                            "url": "https://arxiv.org/abs/2606.12345",
                            "relevant": True,
                        }
                    ],
                }
            },
        },
        {"id": "tool_6", "tool": "search.web_search", "ok": True, "status": "success"},
        {"id": "tool_7", "tool": "search.fetch_sources", "ok": True, "status": "success"},
    ]
    evidence_index = {
        "schema": "idea_research_evidence.v1",
        "network_research": "enabled_fetched",
        "tool_call_count": len(tool_results),
        "quality": {
            "warnings": [],
            "literature_relevance": [
                {
                    "tool": "search.arxiv_search",
                    "status": "pass",
                    "total_hits": 1,
                    "relevant_hits": 1,
                    "precision": 1.0,
                }
            ],
        },
        "source_summary_count": 1,
        "items": [
            {"id": "tool_1", "kind": "tool", "title": "search.local_docs", "tool": "search.local_docs", "ok": True},
            {"id": "tool_2", "kind": "tool", "title": "knowledge.kb_query", "tool": "knowledge.kb_query", "ok": True},
            {"id": "tool_3", "kind": "tool", "title": "code.repo_reader", "tool": "code.repo_reader", "ok": True},
            {"id": "tool_4", "kind": "tool", "title": "knowledge.baseline_match", "tool": "knowledge.baseline_match", "ok": True},
            {"id": "tool_5", "kind": "tool", "title": "search.arxiv_search", "tool": "search.arxiv_search", "ok": True},
            {"id": "self_context_1", "kind": "self_context", "title": "docs/pimc_notes.md"},
            {"id": "project_context", "kind": "project", "title": "Project rules"},
            {"id": "upstream_1", "kind": "context", "title": "idea_code_repositories"},
            {"id": "upstream_2", "kind": "context", "title": "idea_research_sites"},
            {"id": "upstream_3", "kind": "context", "title": "idea_kb_research_excerpts"},
        ],
    }
    downloads_dir = research_dir / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = downloads_dir / "pim_massive_mimo.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n% test fixture\n")
    source_index = {
        "schema": "idea_source_summaries.v1",
        "sources": [
            {
                "title": "Passive intermodulation cancellation for massive MIMO",
                "source_type": "pdf",
                "download_path": str(pdf_path),
                "summary_path": "runs/x/idea/research/downloads/pim.summary.md",
            }
        ],
    }
    for name in (
        "research_plan.v1.md",
        "research_notes.v1.md",
        "research_summary.v1.md",
        "source_summaries.v1.md",
    ):
        (research_dir / name).write_text(f"# {name}\n", encoding="utf-8")
    (research_dir / "tool_results.v1.json").write_text(
        json.dumps(tool_results), encoding="utf-8"
    )
    (research_dir / "evidence_index.v1.json").write_text(
        json.dumps(evidence_index), encoding="utf-8"
    )
    (research_dir / "source_summaries.v1.json").write_text(
        json.dumps(source_index), encoding="utf-8"
    )
    context_dir = run.subdir("context")
    (context_dir / "context_manifest.v2.json").write_text("{}", encoding="utf-8")
    (context_dir / "idea_context_snapshot.v2.md").write_text("# context\n", encoding="utf-8")
    run.write_event(
        "websocket_events",
        {"event": "run.started", "entrypoint": "idea"},
    )
    run.write_event(
        "agent_events",
        {"agent": "idea", "to_state": "running"},
    )
    run.write_event(
        "agent_events",
        {"agent": "idea", "to_state": "waiting_review"},
    )
    run.write_event(
        "evaluation_events",
        {"event": "evaluation.artifact_evaluated", "decision": "pass"},
    )
    run.write_event(
        "tool_calls",
        {"agent": "idea", "tool": "search.arxiv_search"},
    )

    report_path = write_idea_acceptance_report(run=run, artifact_ref=ref)
    report = report_path.read_text(encoding="utf-8")
    assert "Overall: **PASS**" in report
    assert "| Agent 路径 | **pass**" in report
    assert "idea/research/source_summaries.v1.md" in report


def test_normalize_idea_metadata_adds_downstream_contract(tmp_path: Path) -> None:
    request = RunRequest(
        project="pimc",
        user_request="提出一个可验证的 PIMC routing 假设",
        extra={"idea_research_dir": str(tmp_path / "research")},
    )
    context = ContextPack(
        system="",
        project="Project AGENTS.md: baseline protected.",
        task=request.user_request,
    )
    pack = prepare_research_pack(request=request, context=context)
    metadata = {
        "schema": "wrong.v1",
        "project": "",
        "agent": "coding",
        "created": "2099-01-01T00:00:00Z",
        "research_question": "hard top-2 routing 能否降低 PIMC 计算量?",
        "hypothesis": "hard top-2 routing 可以在 RES gate 附近保持可接受退化。",
        "novelty": "把稀疏路由和 memory-polynomial canceller 的容量扫描绑定。",
    }

    normalized = normalize_idea_metadata(metadata, pack)
    evidence_ids = {
        item["id"]
        for item in pack.evidence_index["items"]
        if isinstance(item, dict) and "id" in item
    }
    normalized_refs = {
        item["ref"] if isinstance(item, dict) else item
        for item in normalized["evidence_refs"]
    }
    assert normalized["schema"] == "proposal.v1"
    assert normalized["project"] == "pimc"
    assert normalized["agent"] == "idea"
    assert normalized["created"] != "2099-01-01T00:00:00Z"
    assert normalized["created"].endswith("+00:00")
    assert normalized_refs & evidence_ids
    assert {"router_type", "expert_count", "order"}.issubset(
        set(normalized["experiment_hint"]["variables"])
    )
    assert {"RES", "PIM", "APE", "loss"}.issubset(
        set(normalized["experiment_hint"]["metrics"])
    )
    assert any("Paper_Total_0327" in str(item) for item in normalized["constraints"])
    assert normalized["risk_register"]
    assert normalized["downstream_requirements"]

    result = validate_document(
        fm_dumps(normalized, "# Proposal\n"),
        expected_schema="proposal.v1",
    )
    assert result.valid, result.errors


def test_idea_quality_warns_for_missing_prediction() -> None:
    metadata = build_fake_metadata("proposal.v1", seed="abc123")
    metadata.pop("testable_predictions")
    warnings = validate_idea_quality(metadata)
    assert "missing_testable_predictions" in warnings


def test_idea_quality_warns_for_missing_evidence_ref() -> None:
    metadata = build_fake_metadata("proposal.v1", seed="abc123")
    metadata.pop("evidence_refs")
    warnings = validate_idea_quality(metadata)
    assert "missing_evidence_refs" in warnings


def test_idea_quality_warns_for_unanchored_evidence_ref(tmp_path: Path) -> None:
    request = RunRequest(
        project="pimc",
        user_request="验证 evidence refs 是否锚到 research pack",
        extra={"idea_research_dir": str(tmp_path / "research")},
    )
    pack = prepare_research_pack(
        request=request,
        context=ContextPack(system="", project="", task=request.user_request),
    )
    metadata = build_fake_metadata("proposal.v1", seed="abc123")
    metadata["evidence_refs"] = ["external-only-ref"]
    warnings = validate_idea_quality(
        metadata,
        evidence_index=pack.evidence_index,
    )
    assert "evidence_refs_not_in_research_index" in warnings


def test_idea_quality_warns_for_reversed_res_direction() -> None:
    metadata = build_fake_metadata("proposal.v1", seed="abc123")
    metadata["hypothesis"] = "RES 越高越好，因此目标是提高 RES。"
    warnings = validate_idea_quality(metadata)
    assert "res_direction_reversed" in warnings


def test_idea_quality_warns_for_placeholder_literature() -> None:
    metadata = build_fake_metadata("proposal.v1", seed="abc123")
    metadata["related_literature"] = [
        {
            "title": "Low-rank Volterra model",
            "url": "https://ieeexplore.ieee.org/document/1234567",
        },
        {
            "title": "Group convolution placeholder",
            "url": "https://arxiv.org/abs/2103.00000",
        },
    ]
    warnings = validate_idea_quality(metadata)
    assert "related_literature_placeholder" in warnings


def test_idea_quality_passes_for_v2_mock_proposal() -> None:
    metadata = build_fake_metadata("proposal.v1", seed="abc123")
    assert validate_idea_quality(metadata) == []
