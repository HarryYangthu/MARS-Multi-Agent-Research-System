"""End-to-end FastAPI test: create a run, list it, fetch detail."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import reset_for_tests
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.tools.registry import ToolContext, get_registry as get_tool_registry
from app.main import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Force RunStore + Orchestrator to use a fresh tmpdir-rooted store.
    monkeypatch.chdir(tmp_path)
    # repo_root() looks at module path, but RunStore default uses repo_root().
    # We patch the dependency provider directly instead.
    reset_for_tests()
    from app.api import dependencies as deps
    from app.bridge.orchestrator import Orchestrator
    from app.harness.runtime.event_bus import InProcessEventBus
    from app.storage.run_store import RunStore

    store = RunStore(tmp_path / "runs")
    bus = InProcessEventBus()
    orch = Orchestrator(run_store=store, bus=bus)

    deps._run_store = store
    deps._bus = bus
    deps._orchestrator = orch

    app = create_app()
    return TestClient(app)


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_agent_context_endpoints_cover_commander_and_children(
    client: TestClient,
) -> None:
    for agent in ("commander", "idea", "experiment", "coding", "execution", "writing"):
        r = client.get(f"/api/agents/{agent}/context")
        assert r.status_code == 200, r.text
        payload = r.json()
        assert payload["agent"] == agent
        assert payload["files"]
        assert "research_sites" in payload


def test_coding_workspace_endpoint_exposes_code_context_and_memory(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/runs",
        json={"task": "coding-workspace", "project": "pimc", "entrypoint": "pipeline"},
    )
    assert created.status_code == 200
    run_id = created.json()["run_id"]

    workspace = client.get(
        "/api/agents/coding/workspace",
        params={"project": "pimc", "run_id": run_id},
    )
    assert workspace.status_code == 200, workspace.text
    payload = workspace.json()
    assert payload["project"] == "pimc"
    assert payload["selected_source"] in {"project_repo", "pimc_stub", "empty"}
    assert payload["sources"]
    assert "memory_items" in payload
    assert "upstream_context" in payload


def test_context_preview_and_run_manifest_endpoints(client: TestClient) -> None:
    created = client.post(
        "/api/runs",
        json={"task": "context-api", "project": "pimc", "entrypoint": "pipeline"},
    )
    assert created.status_code == 200
    run_id = created.json()["run_id"]

    preview = client.post(
        "/api/context/preview",
        json={
            "agent": "coding",
            "project": "pimc",
            "task": "Patch the router safely.",
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["schema"] == "context_manifest.v2"
    assert preview.json()["segments"]

    from app.api import dependencies as deps
    from app.harness.context.engine import CompileContextInput, compile_context

    run = deps.get_run_store().get(run_id)
    assert run is not None
    result = compile_context(
        CompileContextInput(
            agent="coding",
            node_key="coding",
            project="pimc",
            output_schema="code_spec.v1",
            system="system",
            project_context="project",
            task="Patch the router safely.",
            upstream={},
            metadata={},
            run_id=run_id,
            run_root=run.root,
            purpose="draft",
        )
    )
    assert result.manifest.manifest_id

    listed = client.get(f"/api/context/runs/{run_id}")
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    assert payload["budget_summary"]["manifest_count"] >= 1
    manifest_id = payload["manifests"][0]["manifest_id"]

    manifest = client.get(f"/api/context/runs/{run_id}/manifests/{manifest_id}")
    assert manifest.status_code == 200, manifest.text
    assert manifest.json()["schema"] == "context_manifest.v2"


def test_context_workbench_can_be_disabled(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_CONTEXT_WORKBENCH_ENABLED", "false")
    import app.settings as settings_mod

    settings_mod._settings = None
    preview = client.post(
        "/api/context/preview",
        json={
            "agent": "coding",
            "project": "pimc",
            "task": "Patch the router safely.",
        },
    )
    assert preview.status_code == 404
    settings_mod._settings = None


def test_create_and_list_run(client: TestClient) -> None:
    r = client.post(
        "/api/runs",
        json={"task": "smoke", "project": "pimc", "entrypoint": "pipeline"},
    )
    assert r.status_code == 200, r.text
    detail = r.json()
    run_id = detail["run_id"]
    assert detail["entrypoint"] == "pipeline"
    assert set(detail["states"]) == {
        "idea",
        "experiment",
        "coding",
        "execution",
        "writing",
    }

    r2 = client.get("/api/runs")
    assert r2.status_code == 200
    assert any(item["run_id"] == run_id for item in r2.json())

    r2_project = client.get("/api/runs?project=pimc")
    assert r2_project.status_code == 200
    assert any(item["run_id"] == run_id for item in r2_project.json())

    r2_other = client.get("/api/runs?project=unknown-project")
    assert r2_other.status_code == 200
    assert all(item["run_id"] != run_id for item in r2_other.json())

    r3 = client.get(f"/api/runs/{run_id}")
    assert r3.status_code == 200
    assert r3.json()["run_id"] == run_id


def test_evaluation_endpoints_return_artifact_reports_and_scorecard(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/runs",
        json={"task": "evaluation-api", "project": "pimc", "entrypoint": "pipeline"},
    )
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]

    from app.api import dependencies as deps
    from app.harness.evaluation.aggregation import write_scorecard
    from app.storage.artifact_store import ArtifactStore

    run = deps.get_run_store().get(run_id)
    assert run is not None
    artifact_store = ArtifactStore(run)
    draft = artifact_store.write(
        text=fm_dumps(
            {
                "schema": "proposal.v1",
                "project": "pimc",
                "agent": "idea",
                "research_question": "How can routing be simplified while preserving RES?",
                "hypothesis": "Hard top-2 routing keeps RES degradation below 1.5 dB.",
                "novelty": "Stream-aware routing is compared against the baseline.",
            },
            "# Proposal\n",
        )
    )
    artifact_store.approve(draft)
    write_scorecard(run_root=run.root, run_id=run_id, project=run.project)

    reports = client.get(
        f"/api/evaluation/runs/{run_id}/artifacts/idea/idea_proposal/v1"
    )
    assert reports.status_code == 200, reports.text
    assert {r["metadata"]["evaluator"] for r in reports.json()} >= {
        "contract.schema_validity",
        "contract.provenance",
    }

    scorecard = client.get(f"/api/evaluation/runs/{run_id}/scorecard")
    assert scorecard.status_code == 200, scorecard.text
    payload = scorecard.json()
    assert payload["schema"] == "evaluation_scorecard.v1"
    assert payload["run_id"] == run_id
    assert payload["report_count"] >= 3
    assert payload["quality_gate"]["schema"] == "evaluation_quality_gate.v1"

    summary = client.get(
        f"/api/evaluation/runs/{run_id}/artifacts/idea/idea_proposal/v1/summary"
    )
    assert summary.status_code == 200, summary.text
    assert summary.json()["policy"]["schema"] == "evaluation_policy_decision.v1"

    export = client.post(f"/api/evaluation/runs/{run_id}/post-training-export")
    assert export.status_code == 200, export.text
    export_payload = export.json()
    assert export_payload["schema"] == "post_training_export_manifest.v1"
    assert export_payload["record_count"] == 1
    assert export_payload["records_preview"][0]["artifact"]["approved"] is True
    assert (
        export_payload["records_preview"][0]["labels"]["schema_validity"][
            "evaluator_versions"
        ]["contract.schema_validity"]
        == 1
    )

    export_get = client.get(f"/api/evaluation/runs/{run_id}/post-training-export")
    assert export_get.status_code == 200, export_get.text
    assert export_get.json()["path"] == "events/post_training_export.jsonl"


def test_commander_attribution_eval_endpoint(client: TestClient) -> None:
    response = client.get("/api/evaluation/commander-attribution")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema"] == "commander_attribution_eval.v1"
    assert payload["failed"] == 0
    assert payload["target_accuracy"] == 1.0


def test_feedback_packet_and_run_memory_endpoints(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = client.post(
        "/api/runs",
        json={"task": "feedback-api", "project": "pimc", "entrypoint": "pipeline"},
    )
    assert created.status_code == 200
    run_id = created.json()["run_id"]

    from app.api import dependencies as deps
    from app.harness.kb.stores import reset_for_tests as reset_kb_stores
    from app.storage import agent_context_store as context_store

    monkeypatch.setattr(context_store, "repo_root", lambda: tmp_path)
    reset_kb_stores(tmp_path / "knowledge")
    run = deps.get_run_store().get(run_id)
    assert run is not None
    packet_meta = {
        "schema": "feedback_packet.v1",
        "project": "pimc",
        "agent": "commander",
        "run_id": run_id,
        "target_agent": "coding",
        "attempt": 2,
        "source_attempt": 1,
        "confidence": 0.82,
        "why_this_agent": "Coding risk is the strongest signal.",
        "evidence_refs": ["execution/metrics.json"],
        "failed_metrics": [
            {
                "metric": "loss",
                "observed": 0.5,
                "target": 0.04,
                "direction": "lte",
            }
        ],
        "do_next": ["Draft a focused patch."],
        "avoid_repeating": ["Avoid broad refactors."],
        "context_refs": ["coding/code_spec.approved.md"],
        "memory_candidates": [],
    }
    packet_path = run.subdir("diagnosis") / "feedback_packet.attempt_2.md"
    packet_path.write_text(fm_dumps(packet_meta, "# packet\n"), encoding="utf-8")
    diagnosis_meta = {
        "schema": "diagnosis.v1",
        "project": "pimc",
        "agent": "commander",
        "run_id": run_id,
        "attempt": 1,
        "passed": False,
        "failed_metrics": packet_meta["failed_metrics"],
        "suspected_causes": [
            {
                "kind": "code_change_risk",
                "summary": "High-risk file changed.",
                "severity": "high",
            }
        ],
        "recommended_target": "coding",
        "recommended_action": "Draft a focused patch.",
        "evidence_refs": ["execution/metrics.json"],
        "budget_status": "within_budget",
        "confidence": 0.82,
        "attribution": {
            "target_agent": "coding",
            "why_this_agent": "Coding risk is the strongest signal.",
            "expected_fix": "Draft a focused patch.",
        },
        "rejected_alternatives": [
            {"target_agent": "experiment", "confidence": 0.35}
        ],
        "feedback_packet_ref": "diagnosis/feedback_packet.attempt_2.md",
    }
    (run.subdir("diagnosis") / "diagnosis.v1.md").write_text(
        fm_dumps(diagnosis_meta, "# diagnosis\n"),
        encoding="utf-8",
    )
    run.write_event(
        "agent_events",
        {
            "event": "agent.state_changed",
            "agent": "coding",
            "from_state": "running",
            "to_state": "waiting_review",
            "timestamp": "2026-06-17T00:00:00+00:00",
        },
    )
    context_manifest = {
        "summary": {
            "task": {"upstream_handoff_keys": ["commander_feedback"]},
            "metadata": {
                "feedback_context": {
                    "path": "diagnosis/feedback_packet.attempt_2.md",
                    "original_chars": 2000,
                    "compressed_chars": 1200,
                    "injected": True,
                },
                "compression": {"strategy": "bounded_commander_feedback"},
                "pollution_guards": {"target_only": True},
            },
        },
        "tokens_estimated": 900,
    }
    (run.subdir("context") / "coding_attempt_2_context_pack.v2.json").write_text(
        json.dumps(context_manifest),
        encoding="utf-8",
    )

    memory_dir = run.subdir("memory")
    candidate = {
        "schema": "agent_memory_candidate.v1",
        "id": "candidate_1",
        "agent": "coding",
        "status": "pending_review",
        "text": "Keep patches focused during feedback loops.",
    }
    episode = {
        "schema": "agent_learning_event.v1",
        "target_agent": "coding",
        "success": False,
    }
    (memory_dir / "memory_candidates.jsonl").write_text(
        json.dumps(candidate) + "\n",
        encoding="utf-8",
    )
    (memory_dir / "episode_memory.jsonl").write_text(
        json.dumps(episode) + "\n",
        encoding="utf-8",
    )

    packets = client.get(f"/api/runs/{run_id}/feedback-packets")
    assert packets.status_code == 200, packets.text
    assert packets.json()[0]["attempt"] == 2

    one_packet = client.get(f"/api/runs/{run_id}/feedback-packets/2")
    assert one_packet.status_code == 200, one_packet.text
    assert one_packet.json()["metadata"]["target_agent"] == "coding"

    candidates = client.get(f"/api/runs/{run_id}/memory-candidates")
    assert candidates.status_code == 200, candidates.text
    assert candidates.json()["items"][0]["id"] == "candidate_1"

    rejected = client.post(
        f"/api/agents/memory-candidates/{run_id}/candidate_1/reject",
        json={"reviewer_note": "not reusable"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    candidates_after_review = client.get(f"/api/runs/{run_id}/memory-candidates")
    assert candidates_after_review.status_code == 200, candidates_after_review.text
    assert candidates_after_review.json()["items"][0]["status"] == "rejected"

    episodes = client.get(f"/api/runs/{run_id}/episode-memory")
    assert episodes.status_code == 200, episodes.text
    assert episodes.json()["items"][0]["target_agent"] == "coding"

    levers = client.get(f"/api/runs/{run_id}/self-evolution/levers")
    assert levers.status_code == 200, levers.text
    lever_payload = levers.json()
    assert lever_payload["schema"] == "self_evolution_levers.v1"
    assert lever_payload["mutation_mode"] == "manual_review_only"
    assert "auto_mutate_prompt" not in lever_payload["allowed_actions"]
    assert lever_payload["counts"]["kb_finding"] >= 1

    context_item = context_store.create_agent_context_file(
        "coding",
        category="prompts",
        filename="feedback_api.md",
        content="Keep feedback patches narrow.",
    )
    mutation = client.post(
        f"/api/runs/{run_id}/self-evolution/mutations",
        json={
            "lever_id": "agent_context:coding:prompts/feedback_api.md",
            "agent": "coding",
            "path": context_item.path,
            "proposed_content": "Keep feedback patches narrow and preserve tests.",
            "rationale": "Commander attribution selected coding risk.",
        },
    )
    assert mutation.status_code == 200, mutation.text
    mutation_payload = mutation.json()
    assert mutation_payload["status"] == "pending_review"
    assert mutation_payload["eval_gate"]["passed"] is True

    mutations = client.get(f"/api/runs/{run_id}/self-evolution/mutations")
    assert mutations.status_code == 200, mutations.text
    assert mutations.json()["items"][0]["id"] == mutation_payload["id"]

    approved_mutation = client.post(
        f"/api/runs/{run_id}/self-evolution/mutations/{mutation_payload['id']}/approve",
        json={"reviewer_note": "approved in API test"},
    )
    assert approved_mutation.status_code == 200, approved_mutation.text
    assert approved_mutation.json()["status"] == "applied"
    updated_files = {
        item.path: item
        for item in context_store.list_agent_context_files("coding", include_runtime_code=False)
    }
    assert updated_files[context_item.path].content == (
        "Keep feedback patches narrow and preserve tests."
    )

    observability = client.get(f"/api/runs/{run_id}/commander-observability")
    assert observability.status_code == 200, observability.text
    observed = observability.json()
    assert observed["schema"] == "commander_observability.v1"
    assert observed["attempts"][0]["observability"]["feedback_was_injected"]

    run_observability = client.get(f"/api/runs/{run_id}/observability")
    assert run_observability.status_code == 200, run_observability.text
    run_view = run_observability.json()
    assert run_view["schema"] == "run_observability.v1"
    assert run_view["timeline"][0]["schema"] == "event.v1"
    assert run_view["audit"]["feedback_packet_count"] == 1
    assert run_view["audit"]["self_evolution_mutations"] == 1
    assert run_view["audit"]["self_evolution_mutation_reviews"] == 1
    assert run_view["audit"]["pending_self_evolution_mutations"] == 0

    health = client.get(f"/api/runs/{run_id}/health")
    assert health.status_code == 200, health.text
    assert health.json()["run_id"] == run_id

    run_events = client.get(f"/api/events/{run_id}", params={"stream": "agent"})
    assert run_events.status_code == 200, run_events.text
    assert run_events.json()[0]["kind"] == "agent.state_changed"


def test_tools_catalogue_endpoints_include_harness_and_bridge_tools(
    client: TestClient,
) -> None:
    listed = client.get("/api/tools")
    assert listed.status_code == 200, listed.text
    names = {item["name"] for item in listed.json()}
    assert "code.apply_patch" in names
    assert "search.arxiv_search" in names
    assert "run.create" in names

    spec = client.get("/api/tools/code.apply_patch")
    assert spec.status_code == 200, spec.text
    payload = spec.json()
    assert payload["policy"]["mutation_level"] == "write"
    assert payload["input_schema"]["anyOf"]
    assert payload["mcp_adapter"]["kind"] == "git"

    adapters = client.get("/api/tools/adapters")
    assert adapters.status_code == 200, adapters.text
    adapter_payload = adapters.json()
    kinds = {item["kind"] for item in adapter_payload}
    assert {"chroma", "filesystem", "git", "github"}.issubset(kinds)


def test_tool_approval_endpoint_replays_approved_mutation(
    client: TestClient,
    tmp_path: Path,
) -> None:
    from app.api import dependencies as deps

    repo = tmp_path / "project_repo"
    (repo / "libs").mkdir(parents=True)
    target = repo / "libs" / "delete_me.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    run = deps.get_run_store().create(task="approval", project="demo", entrypoint="pipeline")

    pending = asyncio.run(
        get_tool_registry().dispatch(
            "code.delete_file",
            {"path": "libs/delete_me.py"},
            ToolContext(
                run_id=run.run_id,
                project="demo",
                agent="coding",
                extra={
                    "run_root": str(run.root),
                    "project_repo_root": str(repo),
                },
            ),
        )
    )
    assert pending.status == "requires_approval"
    approval_id = str(pending.metadata["approval_id"])
    assert target.exists()

    listed = client.get(f"/api/runs/{run.run_id}/tools/approvals")
    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["approval_id"] == approval_id

    approved = client.post(
        f"/api/runs/{run.run_id}/tools/{approval_id}/approve",
        json={"actor": "tester"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["result"]["ok"] is True
    assert not target.exists()


def test_run_tool_audit_filters(
    client: TestClient,
    tmp_path: Path,
) -> None:
    from app.api import dependencies as deps

    run = deps.get_run_store().create(task="tool-filters", project="pimc", entrypoint="pipeline")
    asyncio.run(
        get_tool_registry().dispatch(
            "search.web_search",
            {"q": "massive mimo", "top_k": 1},
            ToolContext(
                run_id=run.run_id,
                project=run.project,
                agent="idea",
                extra={"run_root": str(run.root)},
            ),
        )
    )
    asyncio.run(
        get_tool_registry().dispatch(
            "execution.batch_runner",
            {"run_id": run.run_id, "steps": 1},
            ToolContext(
                run_id=run.run_id,
                project=run.project,
                agent="execution",
                extra={"run_root": str(run.root)},
            ),
        )
    )

    by_tool = client.get(f"/api/runs/{run.run_id}/tools", params={"tool": "execution.batch_runner"})
    assert by_tool.status_code == 200, by_tool.text
    assert by_tool.json()
    assert all(item["tool"] == "execution.batch_runner" for item in by_tool.json())

    by_status = client.get(f"/api/runs/{run.run_id}/tools", params={"status": "disabled"})
    assert by_status.status_code == 200, by_status.text
    assert all(item.get("status") == "disabled" for item in by_status.json())

    first_call_id = next(item["call_id"] for item in by_tool.json() if item.get("call_id"))
    by_call = client.get(f"/api/runs/{run.run_id}/tools", params={"call_id": first_call_id})
    assert by_call.status_code == 200, by_call.text
    assert all(item["call_id"] == first_call_id for item in by_call.json())

    limited = client.get(f"/api/runs/{run.run_id}/tools", params={"limit": 1})
    assert limited.status_code == 200, limited.text
    assert len(limited.json()) == 1


def test_create_run_blocked_when_production_not_ready(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "QWEN_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
        "LOCAL_VLLM_BASE_URL",
    ):
        monkeypatch.setenv(env, "")
    monkeypatch.setenv("MARS_RUNTIME_MODE", "production")
    monkeypatch.setenv("MARS_EXECUTION_BACKEND", "mock")
    import app.settings as settings_mod

    settings_mod._settings = None
    r = client.post(
        "/api/runs",
        json={"task": "prod-block", "project": "pimc", "entrypoint": "pipeline"},
    )
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    settings_mod._settings = None
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["runtime_mode"] == "production"
    assert not detail["ready"]


def test_get_unknown_run_returns_404(client: TestClient) -> None:
    r = client.get("/api/runs/no-such")
    assert r.status_code == 404


def test_execution_plot_endpoints(client: TestClient) -> None:
    r = client.post(
        "/api/runs",
        json={"task": "plot-smoke", "project": "pimc", "entrypoint": "pipeline"},
    )
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    from app.api import dependencies as deps

    run = deps.get_run_store().get(run_id)
    assert run is not None
    plot_dir = run.subdir("execution") / "live_plots"
    plot_dir.mkdir(parents=True, exist_ok=True)
    png = plot_dir / "expert_count_8_loss.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")

    listed = client.get(f"/api/execution/{run_id}/plots")
    assert listed.status_code == 200
    assert listed.json()[0]["filename"] == "expert_count_8_loss.png"

    image = client.get(f"/api/execution/{run_id}/plots/expert_count_8_loss.png")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"


def test_get_run_recovers_from_run_state_after_orchestrator_reset(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/runs",
        json={"task": "recover", "project": "pimc", "entrypoint": "pipeline"},
    )
    assert created.status_code == 200
    run_id = created.json()["run_id"]

    from app.api import dependencies as deps
    from app.bridge.orchestrator import Orchestrator

    assert deps._run_store is not None
    deps._orchestrator = Orchestrator(run_store=deps._run_store, bus=deps.get_event_bus())

    recovered = client.get(f"/api/runs/{run_id}")
    assert recovered.status_code == 200
    assert recovered.json()["run_id"] == run_id
    assert "diagnosis" not in recovered.json()["states"]
