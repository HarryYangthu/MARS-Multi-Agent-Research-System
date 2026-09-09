"""Explicit lead tool narrowing: configuration, real session wiring and old archives.

No model, network, fake provider or substituted tool is executed.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.agents.base import RunRequest
from app.agents.idea.research_delegate import ResearchSession, load_delegated_research
from app.agents.idea.research_stop import lead_evidence_stop
from app.agents.idea.runtime_profile import (
    PROFILE_FILE, SNAPSHOT_FILE, ResolvedIdeaProfile, _lead_profile_tools,
    bind_profile_snapshot, public_agent_configuration, resolve_idea_profile,
)
from app.agents.idea.service_agent import ServiceIdeaAgent
from app.harness.agent_loop.executor import action_instructions
from app.harness.agent_loop.stop import LoopStopView
from app.harness.agent_loop.trace import canonical
from app.harness.tools.config import tool_config
from app.harness.tools.registry import get_registry
from app.settings import Settings, repo_root


V4 = "experimental_research_pro_per_insight_v4"
V5 = "experimental_research_pro_per_insight_v5"
LEAD_TOOLS = ("idea.research_delegate", "knowledge.kb_query")
# Recorded before this change at 7ae5722; these old catalog entries stay immutable.
OLD_CONFIGURATION_SHA256 = (
    "afb241986539381285260008bbfb0eef09a8fe9b6cd7ad5aee2572a6cef6d804",
    "182d2baa2bcaddf004bd592616ba6644cc035b7465b59083022041a89f00e8a1",
    "3bc0f6bfe6d0838e21f665367041e41e011eaa13926dc1b3de581a208df8d69c",
    "947faeaca63827f16f3e42a990a9d5ab9d105f0fa61fa5c7302d862c64caa7f8",
)


def selected(name: str) -> ResolvedIdeaProfile:
    profile = resolve_idea_profile(name)
    assert profile is not None
    return profile


def test_v5_only_narrows_lead_tools_and_old_catalog_bytes_stay_unchanged() -> None:
    original_bytes = (repo_root() / PROFILE_FILE).read_bytes()
    assert hashlib.sha256(original_bytes[:8617]).hexdigest() == "0bda43490828872a3db4acdc452dc7035dd5d2cb8f1cb9439bc7f1f95f78c688"
    definitions = yaml.safe_load(original_bytes)["profiles"]
    expected = deepcopy(definitions[V4])
    expected["lead"]["tools"] = list(LEAD_TOOLS)
    assert definitions[V5] == expected
    for version, checksum in enumerate(OLD_CONFIGURATION_SHA256, 1):
        old = selected(f"experimental_research_pro_per_insight_v{version}")
        assert old.snapshot()["configuration_sha256"] == checksum
    old, new = selected(V4), selected(V5)
    assert new.child == old.child
    assert new.lead.tools == LEAD_TOOLS and new.lead.raw["tools"] == list(LEAD_TOOLS)
    a, b = public_agent_configuration(old.lead), public_agent_configuration(new.lead)
    assert {k:v for k,v in a.items() if k not in {"tools", "enabled_tools", "tool_configuration_sha256"}} == {k:v for k,v in b.items() if k not in {"tools", "enabled_tools", "tool_configuration_sha256"}}
    assert b["enabled_tools"] == list(LEAD_TOOLS)
    assert a["tool_configuration_sha256"] != b["tool_configuration_sha256"]
    assert new.snapshot()["status"] == "experimental" and new.snapshot()["validated"] is False
    assert Settings.model_fields["mars_idea_runtime_profile"].default == "baseline"
    assert Settings(_env_file=None, mars_idea_runtime_profile=V5).mars_idea_runtime_profile == V5  # type: ignore[call-arg,arg-type]


@pytest.mark.parametrize("tools", [[], ["knowledge.kb_query"], ["idea.research_delegate"] * 2,
    ["idea.research_delegate", "unregistered.tool"], ["idea.research_delegate", "search.cvf_search"],
    ["idea.research_delegate", "code.write_file"]])
def test_lead_override_requires_delegation_and_never_adds_capabilities(tools: list[str]) -> None:
    from app.harness.llm.model_registry import get_agent_config
    with pytest.raises(ValueError, match="unique subset"):
        _lead_profile_tools(get_agent_config("idea"), tools)


def test_actual_registered_read_subsets_and_agent_identity_are_checked() -> None:
    from app.harness.llm.model_registry import get_agent_config
    original = get_agent_config("idea")
    assert _lead_profile_tools(original, None) == original.tools
    assert _lead_profile_tools(original, list(LEAD_TOOLS)) == LEAD_TOOLS
    assert _lead_profile_tools(original, ["idea.research_delegate"]) == ("idea.research_delegate",)
    assert _lead_profile_tools(original, ["idea.research_delegate", "code.repo_reader"]) == ("idea.research_delegate", "code.repo_reader")
    for altered in (replace(original, enabled=False), replace(original, name="idea_research")):
        with pytest.raises(ValueError, match="unique subset"):
            _lead_profile_tools(altered, list(LEAD_TOOLS))
    # Invalid configuration inputs only: the actual registry/config remains intact.
    for name in ("code.write_file", "unregistered.tool", "search.cvf_search"):
        altered = replace(original, tools=(*original.tools, name))
        with pytest.raises(ValueError, match="permitted read tool"):
            _lead_profile_tools(altered, ["idea.research_delegate", name])


def test_v5_receipt_is_distinct_and_cross_profile_rebinding_never_writes(tmp_path: Path) -> None:
    v4, v5 = selected(V4), selected(V5)
    for first, second in ((v4, v5), (v5, v4)):
        root = tmp_path / first.profile_id
        bind_profile_snapshot(root, first)
        path = root / SNAPSHOT_FILE
        original = path.read_bytes()
        assert bind_profile_snapshot(root, first, resume=True) == first.snapshot()
        with pytest.raises(ValueError, match="configuration differs"):
            bind_profile_snapshot(root, second, resume=True)
        assert path.read_bytes() == original
        assert not (root / "agent_traces").exists()
    # Tool changes are bound independently of the selector name as well.
    changed = replace(v4, lead=v5.lead, configuration_json=canonical({
        "lead": public_agent_configuration(v5.lead), "child": public_agent_configuration(v4.child)}))
    path = tmp_path / V4 / SNAPSHOT_FILE
    before = path.read_bytes()
    with pytest.raises(ValueError, match="configuration differs"):
        bind_profile_snapshot(tmp_path / V4, changed, resume=True)
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_service_uses_real_session_binding_and_narrow_actual_action_schema(tmp_path: Path) -> None:
    environment = dict(os.environ)
    outputs: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for name in (V4, V5):
        profile = selected(name)
        root = tmp_path / name
        service = ServiceIdeaAgent(profile=profile)
        request = RunRequest(project="pimc", user_request="Investigate a method with two distinct sources.",
            extra={"run_id": name, "run_root": str(root), "idea_requirements": {
                "min_sources": 2, "min_pdfs": 2, "require_research_dossier": True}})
        context = await service.build_context(request)
        registry = service.loop_registry(request, context)
        session = request.runtime["idea_research_session"]
        assert isinstance(session, ResearchSession) and session.registry is registry
        assert session.config == profile.child and session.max_delegations == 3
        assert session.require_review and session.attempted == 0 and not session.receipts
        assert registry.has("idea.research_delegate")
        assert getattr(registry._tools["idea.research_delegate"], "__self__", None) is session
        assert service.required_review_tools(request) == ("idea.research_delegate",)
        assert request.extra["idea_requirements"]["min_sources"] == 2
        assert context.metadata["context_sources"] == {"project_rules": True, "code_repositories": True}
        specs = []
        for tool in service.config.tools:
            if not tool_config(tool).enabled:
                continue
            spec = registry.spec(tool)
            assert registry.has(tool) and spec is not None and not spec.bridge_only
            specs.append({"name": tool, "description": spec.description, "args_schema": spec.input_schema})
        schema = service.submission_schema(request)
        assert schema is not None
        instructions = action_instructions(specs, native=False, final_schema=schema)
        tools_text, final_text = instructions.split("\nTools:\n", 1)[1].split("\nComplete final.metadata JSON Schema:\n")
        assert json.loads(tools_text) == specs and json.loads(final_text) == schema
        snapshot = json.loads((root / SNAPSHOT_FILE).read_text())
        assert snapshot["configuration"]["lead"]["tools"] == [s["name"] for s in specs]
        assert not (root / "agent_traces").exists() and not (root / "events").exists()
        outputs[name] = schema, specs
    assert outputs[V4][0] == outputs[V5][0]
    assert [s["name"] for s in outputs[V5][1]] == list(LEAD_TOOLS)
    assert outputs[V5][1] == [s for s in outputs[V4][1] if s["name"] in LEAD_TOOLS]
    assert "search.fetch_sources" in [s["name"] for s in outputs[V4][1]]
    assert dict(os.environ) == environment
    assert not get_registry().has("idea.research_delegate")  # Never registered globally.


def test_real_run21_cannot_adopt_v5_or_turn_direct_reads_into_reports() -> None:
    configured = os.environ.get("MARS_TEST_IDEA_LEAD_TOOL_ARCHIVE")
    if not configured:
        pytest.skip("requires the real terminated run21; no execution substitute")
    root = Path(configured)
    trace = next((root / "agent_traces/idea").glob("*/checkpoint.json"))
    paths = [root / SNAPSHOT_FILE, trace, trace.with_name("events.jsonl"), *trace.parent.glob("tools/*.json")]
    originals = {path: path.read_bytes() for path in paths}
    state = json.loads(originals[trace])
    assert state["status"] == "evidence_unavailable" and not state["candidate"]
    assert state["counts"]["tool_dispatches"] == 5
    assert [o["tool"] for o in state["history"]] == ["idea.research_delegate"] + ["search.fetch_sources"] * 4
    assert load_delegated_research(root, state["history"])[0] == []
    profile = selected(V5)
    with pytest.raises(ValueError, match="configuration differs"):
        bind_profile_snapshot(root, profile, resume=True)
    stopped = lead_evidence_stop(LoopStopView("before_model", state["candidate"], state["history"], state["counts"], "act"),
        run_root=root, min_sources=2, max_delegations=3, max_tool_steps=5, can_delegate=True)
    assert stopped is not None and stopped.status == "evidence_unavailable"
    assert stopped.details["required_publications"] == 2 and stopped.details["verified_report_publications"] == 0
    first_request = next(json.loads(line) for line in originals[trace.with_name("events.jsonl")].splitlines()
                         if json.loads(line)["kind"] == "model_request")
    instruction = next(m["content"] for m in first_request["visible"] if "\nTools:\n" in m["content"])
    actual_specs = json.loads(instruction.split("\nTools:\n", 1)[1].split("\nComplete final.metadata JSON Schema:\n", 1)[0])
    assert [s["name"] for s in actual_specs] == list(selected(V4).lead.tools)
    assert len(actual_specs) == 8 and len(profile.lead.tools) == 2
    assert all(path.read_bytes() == original for path, original in originals.items())
