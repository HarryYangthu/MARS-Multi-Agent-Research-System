"""CVF configuration contracts and real archived evidence; no execution substitutes."""
from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
import pytest
from pydantic import ValidationError

from app.agents.base import RunRequest
from app.agents.idea.research import evidence_inventory, material_errors
from app.agents.idea.research_gap import evidence_stop, material_state
from app.agents.idea.research_review_plan import matching_source_rows
from app.agents.idea.runtime_profile import _profile_tools, public_agent_configuration, resolve_idea_profile
from app.agents.idea.service_agent import ServiceIdeaAgent
from app.agents.idea.source_identity import SourceIdentityIndex, search_metadata_rows
from app.harness.agent_loop.stop import LoopStopView
from app.harness.agent_loop.trace import digest
from app.harness.llm.model_registry import get_agent_config
from app.harness.tools.config import load_tool_configs
from app.harness.tools.registry import ConfiguredReadToolScope, ToolContext, _allowed_for_agent, get_registry
from app.settings import Settings


def test_only_explicit_v4_adds_child_cvf_without_changing_models_budgets_or_lead() -> None:
    original = resolve_idea_profile("experimental_research_pro_per_insight_v3")
    selected = resolve_idea_profile("experimental_research_pro_per_insight_v4")
    assert original is not None and selected is not None
    assert public_agent_configuration(selected.lead) == public_agent_configuration(original.lead)
    assert selected.child.tools == (*original.child.tools, "search.cvf_search")
    assert selected.child.raw["tools"] == list(selected.child.tools)
    old, new = public_agent_configuration(original.child), public_agent_configuration(selected.child)
    assert new["research"]["review_mode"] == "per_insight_collect_then_whole"
    for key in set(old) - {"tools", "enabled_tools", "tool_configuration_sha256"}:
        assert old[key] == new[key]
    assert new["tools"] == old["tools"] + ["search.cvf_search"]
    assert new["enabled_tools"] == old["enabled_tools"] + ["search.cvf_search"]
    assert new["tool_configuration_sha256"] != old["tool_configuration_sha256"]
    assert selected.snapshot()["validated"] is False
    for name in ("idea", "idea_research"):
        assert "search.cvf_search" not in get_agent_config(name).tools
    for version in (1, 2, 3):
        baseline = resolve_idea_profile(f"experimental_research_pro_per_insight_v{version}")
        assert baseline is not None
        assert baseline.child.tools == get_agent_config("idea_research").tools
        assert baseline.lead.tools == get_agent_config("idea").tools


@pytest.mark.parametrize("tools", [[], ["search.fetch_sources"] * 2,
    ["search.fetch_sources", "unregistered.tool"], ["search.fetch_sources", "idea.research_delegate"],
    ["search.fetch_sources", "code.write_file"], ["search.fetch_sources", "code.repo_reader"], ["search.cvf_search"]])
def test_explicit_profile_tools_reject_missing_reads_unknown_mutating_or_disallowed_tools(tools: list[str]) -> None:
    with pytest.raises(ValueError):
        _profile_tools(get_agent_config("idea_research"), tools)


@pytest.mark.parametrize("change", [{"top_k": 4}, {"top_k": True}, {"year": "2022"},
    {"year": 1999}, {"year": 2101}, {"venue": "cvpr"}, {"query": " "}, {"query": "a" * 201},
    {"pdf_url": "https://example.invalid/not-a-tool-argument.pdf"}])
def test_registered_cvf_argument_contract_rejects_invalid_inputs(change: dict[str, Any]) -> None:
    spec = get_registry().spec("search.cvf_search")
    assert spec is not None
    args = {"query": "tensor representation", "venue": "CVPR", "year": 2022, **change}
    assert list(Draft202012Validator(spec.input_schema).iter_errors(args))


def test_cvf_is_registered_read_only_with_bounded_configured_inputs_and_metadata_budget() -> None:
    registry = get_registry()
    spec = registry.spec("search.cvf_search")
    assert registry.has("search.cvf_search") and spec is not None
    assert spec.policy.allowed_agents == ("idea_research",)
    assert spec.policy.mutation_level == "read" and spec.policy.network
    assert not spec.policy.requires_approval and not spec.bridge_only
    validator = Draft202012Validator(spec.input_schema)
    for venue in ("CVPR", "ICCV", "WACV"):
        assert not list(validator.iter_errors({"query": "tensor representation", "venue": venue, "year": 2022, "top_k": 3}))
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.mars_cvf_total_timeout_seconds == 60
    assert settings.mars_cvf_directory_max_mib == 8 and settings.mars_cvf_landing_max_kib == 256
    maximum = Settings(_env_file=None, mars_cvf_total_timeout_seconds=180)  # type: ignore[call-arg]
    assert load_tool_configs()["search.cvf_search"].timeout_seconds > maximum.mars_cvf_total_timeout_seconds
    assert Settings(_env_file=None, mars_idea_runtime_profile="experimental_research_pro_per_insight_v4").mars_idea_runtime_profile.endswith("v4")  # type: ignore[call-arg]
    for kwargs in ({"mars_cvf_total_timeout_seconds": 181}, {"mars_cvf_directory_max_mib": 17}, {"mars_cvf_landing_max_kib": 1025}):
        with pytest.raises(ValidationError):
            Settings.model_validate(kwargs)


def test_new_metadata_source_never_accepts_an_unarchived_claim() -> None:
    # Deliberately untrusted negative input, not a simulated successful tool.
    forged = {"tool": "search.cvf_search", "ok": True,
              "output": {"source": "cvf", "hits": [{"title": "Unverified claim", "url": "https://openaccess.thecvf.com/claim"}]}}
    assert search_metadata_rows(forged) == []
    assert SourceIdentityIndex([forged]).hits == []
    assert evidence_inventory([forged])["counts"]["unique_search_sources"] == 0


@pytest.mark.asyncio
async def test_service_v4_passes_effective_child_scope_through_the_actual_research_session(tmp_path: Path) -> None:
    profile = resolve_idea_profile("experimental_research_pro_per_insight_v4")
    assert profile is not None
    service = ServiceIdeaAgent(profile=profile)
    request = RunRequest(project="pimc", user_request="Read the supplied project constraints.",
                         extra={"run_root": str(tmp_path)})
    context = await service.build_context(request)
    registry = service.loop_registry(request, context)
    session = request.runtime["idea_research_session"]
    scope_context = session.research_tool_context(run_id="component-contract", project="pimc", root=tmp_path)
    assert scope_context.agent == "idea_research"
    assert scope_context.configured_read_scope.tools == profile.child.tools
    assert scope_context.extra == {"run_root": str(tmp_path)}
    # Real registry calls with intentionally invalid arguments. A successful
    # permission check reaches schema rejection without invoking the network tool.
    scoped = await registry.dispatch("search.cvf_search", {}, scope_context)
    assert scoped.ok is False and scoped.status == "error" and "required" in str(scoped.error)
    default = ToolContext(run_id="component-contract", project="pimc", agent="idea_research",
                          extra={"run_root": str(tmp_path), "configured_read_scope": scope_context.configured_read_scope})
    refused = await registry.dispatch("search.cvf_search", {}, default)
    assert refused.status == "not_allowed" and refused.ok is False
    # No permissions leak into the global registry/default researcher configuration.
    spec = registry.spec("search.cvf_search")
    assert spec is not None and not _allowed_for_agent(spec.name, "idea_research", spec)
    baseline = ServiceIdeaAgent(profile=resolve_idea_profile("experimental_research_pro_per_insight_v3"))
    baseline_request = RunRequest(project="pimc", user_request="Read the supplied project constraints.",
                                 extra={"run_root": str(tmp_path / "baseline")})
    baseline_context = await baseline.build_context(baseline_request)
    baseline.loop_registry(baseline_request, baseline_context)
    old_session = baseline_request.runtime["idea_research_session"]
    assert old_session.research_tool_context(run_id="legacy", project="pimc", root=tmp_path).configured_read_scope is None


def test_a_constructed_scope_cannot_cross_agents_or_authorize_writes() -> None:
    registry = get_registry()
    spec = registry.spec("search.cvf_search")
    assert spec is not None
    scope = registry.scope_for_read_tools("idea_research", ("search.cvf_search",))
    assert _allowed_for_agent(spec.name, "idea_research", spec, configured_scope=scope)
    assert not _allowed_for_agent(spec.name, "idea", spec, configured_scope=scope)
    assert not _allowed_for_agent(spec.name, "idea", spec,
                                  configured_scope=ConfiguredReadToolScope("idea", (spec.name,)))
    write = registry.spec("code.write_file")
    assert write is not None
    assert not _allowed_for_agent(write.name, "coding", write,
                                  configured_scope=ConfiguredReadToolScope("coding", (write.name,)))
    with pytest.raises(ValueError):
        registry.scope_for_read_tools("coding", (write.name,))


def test_original_default_permission_denial_is_preserved_in_its_real_archive() -> None:
    configured = os.environ.get("MARS_TEST_CVF_DENIED_RESULT")
    if not configured:
        pytest.skip("requires the actual first registered-query denial archive")
    path = Path(configured)
    data = path.read_bytes()
    result = json.loads(data)
    # The component records its real ToolResult; it must remain a failure after
    # adding the explicit scope path, not be relabeled as a successful search.
    tool_result = result.get("tool_result", result)
    assert tool_result["ok"] is False and tool_result["status"] == "not_allowed"
    assert path.read_bytes() == data


@pytest.fixture
def archive() -> Iterator[list[dict[str, Any]]]:
    configured = os.environ.get("MARS_TEST_CVF_EVIDENCE_TRACE")
    if not configured:
        pytest.skip("requires actual registered CVF + PDF component events; no tool response is fabricated")
    path = Path(configured)
    original: dict[Path, str] = {path: hashlib.sha256(path.read_bytes()).hexdigest()}
    events = [json.loads(line) for line in path.read_text().splitlines()]
    observations = [event["visible"] for event in events if event["kind"] == "observation"]
    assert observations and any(row["tool"] == "search.cvf_search" and row["ok"] for row in observations)
    assert any(row["tool"] == "search.fetch_sources" and row["ok"] for row in observations)
    for event in events:
        if "visible" in event:
            assert event["visible_sha256"] == digest(event["visible"])

    def protect(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                protect(item)
        elif isinstance(value, list):
            for item in value:
                protect(item)
        elif isinstance(value, str) and value.startswith("/") and len(value) < 1000:
            candidate = Path(value)
            if candidate.is_file() and candidate not in original:
                data = candidate.read_bytes()
                original[candidate] = hashlib.sha256(data).hexdigest()
                if candidate.suffix == ".json":
                    protect(json.loads(data))
    protect(observations)
    yield observations
    assert all(hashlib.sha256(name.read_bytes()).hexdigest() == value for name, value in original.items())


def test_real_metadata_registers_sources_but_cannot_satisfy_reading_floor(archive: list[dict[str, Any]]) -> None:
    metadata_only = [row for row in archive if row["tool"] == "search.cvf_search"]
    hits = SourceIdentityIndex(metadata_only).hits
    assert hits
    inventory = evidence_inventory(metadata_only)
    assert inventory["counts"]["unique_search_sources"] > 0
    assert inventory["counts"]["unique_pdf_contents"] == inventory["counts"]["read_windows"] == 0
    assert material_state(metadata_only)["counts"]["distinct_read_publications"] == 0
    with pytest.raises(ValueError, match="actual matching source reads"):
        matching_source_rows(hits[0], metadata_only)
    view = LoopStopView("before_model", "", metadata_only, {"tool_dispatches": 1}, "act")
    assert evidence_stop(view, min_sources=1, max_tool_steps=1, tools=("search.fetch_sources",), project="pimc") is not None


def test_real_cvf_pdf_requires_exact_metadata_and_read_receipt(archive: list[dict[str, Any]]) -> None:
    state = material_state(archive)
    assert state["counts"]["distinct_read_publications"] >= 1
    read = state["read_sources"][0]
    index = SourceIdentityIndex(archive)
    hits = index.matching_hits(read["url"], read_receipt=read["read_receipt"])
    assert hits and all(hit["source"] == "cvf" for hit in hits)
    source = hits[0]
    rows, bindings = matching_source_rows(source, archive)
    assert any(row["tool"] == "search.cvf_search" for row in rows)
    assert any(row["tool"] == "search.fetch_sources" for row in rows)
    metadata_binding = next(row for row in bindings if row["tool"] == "search.cvf_search")
    assert metadata_binding["search_receipt_sha256"] == source["search_receipt_sha256"]
    assert metadata_binding["metadata_response_sha256"] == source["metadata_response_sha256"]
    metadata = {"related_literature": [{key: source[key] for key in ("title", "url")}]}
    args = {"min_sources": 1, "min_pdfs": 1, "require_budget": False, "max_ratio": 1.0}
    errors = material_errors(metadata, archive, **args)  # type: ignore[arg-type]
    assert not any(error.startswith(("/related_literature", "/evidence")) for error in errors)
    metadata["related_literature"][0]["url"] += "?unobserved-document=1"
    errors = material_errors(metadata, archive, **args)  # type: ignore[arg-type]
    assert any(error.startswith("/related_literature") for error in errors)
    assert any(error.startswith("/evidence") for error in errors)


def test_real_metadata_or_pdf_mutation_cannot_gain_evidence_credit(archive: list[dict[str, Any]]) -> None:
    search = next(row for row in archive if row["tool"] == "search.cvf_search" and row["ok"])
    assert search_metadata_rows(search)
    for key, value in (("title", "changed title"), ("pdf_url", "https://openaccess.thecvf.com/unobserved.pdf"),
                       ("metadata_response_sha256", "0" * 64)):
        changed = deepcopy(search)
        changed["output"]["hits"][0][key] = value
        assert search_metadata_rows(changed) == []
    changed_history = deepcopy(archive)
    for observation in changed_history:
        if observation["tool"] == "search.fetch_sources":
            for row in observation["output"].get("sources", []):
                row["download_url"] = str(row.get("download_url", "")) + "?wrong-pdf=1"
    assert material_state(changed_history)["counts"]["distinct_read_publications"] == 0
    reading_only = [row for row in archive if row["tool"] == "search.fetch_sources"]
    assert material_state(reading_only)["counts"]["distinct_read_publications"] == 0


def test_original_profile_configuration_rebuilds_without_changing_its_receipt() -> None:
    configured = os.environ.get("MARS_TEST_CVF_LEGACY_PROFILE_RECEIPT")
    if not configured:
        pytest.skip("requires an actual prior v1/v2/v3 runtime profile receipt")
    path = Path(configured)
    data = path.read_bytes()
    archived = json.loads(data)
    assert archived["profile_id"].endswith(("_v1", "_v2", "_v3"))
    selected = resolve_idea_profile(archived["profile_id"])
    assert selected is not None
    assert selected.snapshot()["configuration"] == archived["configuration"]
    assert selected.snapshot()["configuration_sha256"] == archived["configuration_sha256"]
    # A changed catalog source hash does not authorize resuming the old run.
    # This is a read-only configuration reconstruction, not receipt adoption.
    assert path.read_bytes() == data
