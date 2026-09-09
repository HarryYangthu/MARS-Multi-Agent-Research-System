"""Pure scope/schema contracts and negative real-archive replay; no simulated executions."""
from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any

from jsonschema import Draft202012Validator
import pytest

from app.agents.base import ContextPack, RunRequest
from app.agents.idea.agent import IdeaAgent
from app.agents.idea.research_delegate import TOOL, ResearchSession, load_delegated_research, make_research_registry
from app.agents.idea.research_gap import STOP_CONTRACT, evidence_stop, material_state
from app.agents.idea.research_review import research_review_messages
from app.agents.idea.runtime_profile import resolve_idea_profile
from app.agents.idea.research_unit import (
    RESEARCH_UNIT_CONTRACT, UNIT_PARENT_GUIDANCE, configured_research_unit, research_unit_errors,
    unit_fields, unit_input_constraint, unit_messages, validate_unit_arguments,
)
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.stop import LoopStopView, stop_fingerprint
from app.harness.agent_loop.trace import digest
from app.harness.llm.model_registry import get_agent_config
from app.harness.tools.registry import ToolContext, ToolRegistry, get_registry


def _unit() -> dict[str, Any]:
    unit = configured_research_unit({"per_delegation_min_sources": 1})
    assert unit is not None
    return unit


def _args() -> dict[str, Any]:
    # Human-authored contract input, not a produced research assignment/result.
    return {"gap": "Find one relevant mechanism", "success_criteria": "Read its actual method and limitations",
            "context_refs": [], "min_sources": 1}


def _session(tmp_path: Path, *, enabled: bool) -> ResearchSession:
    config = get_agent_config("idea")
    if enabled:
        config = replace(config, raw={**config.raw, "research": {
            **config.raw.get("research", {}), "per_delegation_min_sources": 1}})
    request = RunRequest("pimc", "Overall task still needs at least two independent publications.",
                         extra={"run_root": str(tmp_path), "run_id": "unit-contract"})
    context = ContextPack("", "Actual supplied project constraints", request.user_request,
                          upstream={"baseline_code": "Caller supplied baseline"})
    make_research_registry(config, request, context)
    session = request.runtime["idea_research_session"]
    assert isinstance(session, ResearchSession)
    return session


@pytest.mark.parametrize("value", [None, True, False, 0, 2, 1.0, "1", {}, []])
def test_only_explicit_integer_one_enables_strategy(value: Any) -> None:
    with pytest.raises(ValueError, match="exactly the integer 1"):
        configured_research_unit({"per_delegation_min_sources": value})


def test_absent_policy_keeps_old_inputs_and_fields() -> None:
    assert configured_research_unit({"max_delegations": 3}) is None
    assert unit_fields(None) == {} and unit_messages(None, reviewing=False) == []
    assert research_unit_errors({}, {}, request=None, arguments=None) == []
    validate_unit_arguments({"min_sources": 2}, None)


@pytest.mark.parametrize("value", [None, 2, True, False, 1.0, "1", 0])
@pytest.mark.asyncio
async def test_invalid_count_is_rejected_before_any_child_or_provider(tmp_path: Path, value: Any) -> None:
    session = _session(tmp_path, enabled=True)
    args = _args()
    if value is None:
        del args["min_sources"]
    else:
        args["min_sources"] = value
    before = deepcopy(args)
    context = ToolContext("unit-contract", "pimc", "idea", extra={"run_root": str(tmp_path)})
    # Direct host guard also rejects integral floats; JSON Schema regards 1.0 as an integer.
    result = await session.dispatch(args, context)
    assert not result.ok and "explicit min_sources=1" in str(result.error)
    assert result.output["failure_type"] == "research_unit_argument_conflict"
    assert args == before and session.attempted == 0 and not session.receipts
    assert not (tmp_path / "idea/research_delegations").exists()
    assert not (tmp_path / "agent_traces").exists()
    registered = await session.registry.dispatch(TOOL, args, context)
    assert not registered.ok and session.attempted == 0
    assert not (tmp_path / "idea/research_delegations").exists()


def test_effective_schema_only_tightens_private_registry(tmp_path: Path) -> None:
    original = get_registry().spec(TOOL)
    assert original is not None
    before = deepcopy(original)
    old, new = _session(tmp_path / "old", enabled=False), _session(tmp_path / "new", enabled=True)
    old_spec, new_spec = old.registry.spec(TOOL), new.registry.spec(TOOL)
    assert old_spec is not None and new_spec is not None
    assert old_spec == before == get_registry().spec(TOOL)
    assert new_spec.input_schema == {**old_spec.input_schema, "allOf": [unit_input_constraint()]}
    assert new_spec.policy == old_spec.policy and new_spec.output_schema == old_spec.output_schema
    assert new_spec.bridge_only == old_spec.bridge_only
    assert new.registry._gates == old.registry._gates
    validator = Draft202012Validator(new_spec.input_schema)
    assert not list(validator.iter_errors(_args()))
    for update in ({"min_sources": 2}, {"gap": "short"}, {"unexpected": 1}):
        assert list(validator.iter_errors({**_args(), **update}))
    constraint = unit_input_constraint()
    new.registry.constrain_input_schema(TOOL, constraint)
    constraint["properties"]["min_sources"]["const"] = 2
    final_spec = new.registry.spec(TOOL)
    assert final_spec is not None
    assert not list(Draft202012Validator(final_spec.input_schema).iter_errors(_args()))
    assert original == before
    with pytest.raises(ValueError, match="unregistered"):
        ToolRegistry().constrain_input_schema("unregistered", {})


def test_actual_v6_profile_reaches_run_local_delegate_schema(tmp_path: Path) -> None:
    profile = resolve_idea_profile("experimental_research_pro_per_insight_v6")
    old_profile = resolve_idea_profile("experimental_research_pro_per_insight_v5")
    assert profile is not None and old_profile is not None
    request = RunRequest("pimc", "An overall two-publication task.", extra={"run_root": str(tmp_path)},
                         runtime={"idea_research_config": profile.child})
    registry = make_research_registry(profile.lead, request, ContextPack("", "Project constraints", request.user_request))
    session = request.runtime["idea_research_session"]
    assert isinstance(session, ResearchSession) and session.research_unit == _unit()
    spec = registry.spec(TOOL)
    assert spec is not None and spec.input_schema["allOf"] == [unit_input_constraint()]
    assert profile.lead.tools == old_profile.lead.tools
    assert profile.child.tools == old_profile.child.tools + ("search.neurips_search",)
    assert session.config is profile.child
    for config, old in ((profile.lead, old_profile.lead), (profile.child, old_profile.child)):
        assert config.raw["model"] == old.raw["model"] and config.raw["loop"] == old.raw["loop"]
    assert profile.child.raw["research"]["review_mode"] == "per_insight_collect_then_whole"
    with pytest.raises(ValueError, match="cannot change"):
        make_research_registry(old_profile.lead, request, session.context)


def test_added_constraint_preserves_root_refs_and_existing_allof(tmp_path: Path) -> None:
    session = _session(tmp_path, enabled=False)
    spec = session.registry.spec(TOOL)
    assert spec is not None
    # Human-authored schema only. The real handler is never invoked or replaced.
    schema = {"type": "object", "additionalProperties": False,
              "$defs": {"positive": {"type": "integer", "minimum": 1}},
              "properties": {"value": {"$ref": "#/$defs/positive"}},
              "allOf": [{"required": ["value"]}]}
    session.registry._specs[TOOL] = replace(spec, input_schema=schema)
    session.registry.constrain_input_schema(TOOL, {"properties": {"value": {"maximum": 2}}})
    constrained = session.registry.spec(TOOL)
    assert constrained is not None
    validator = Draft202012Validator(constrained.input_schema)
    assert validator.is_valid({"value": 2})
    assert all(not validator.is_valid(value) for value in ({}, {"value": 0}, {"value": 3}, {"extra": 1}))
    assert schema["allOf"] == [{"required": ["value"]}]


@pytest.mark.asyncio
async def test_parent_scope_adds_only_explicit_guidance_and_preserves_final_contract() -> None:
    old = IdeaAgent()
    config = replace(old.config, raw={**old.config.raw, "research": {
        **old.config.raw.get("research", {}), "per_delegation_min_sources": 1}})
    new = IdeaAgent(agent_config=config)
    request = RunRequest("pimc", "The final proposal requires two independent relevant papers.",
        upstream_artifacts={"baseline_code": "Caller supplied baseline"},
        extra={"context_sources": {"project_rules": False, "code_repositories": False},
               "idea_requirements": {"min_sources": 2, "require_research_dossier": True}})
    before = deepcopy(request.extra)
    prior, current = await old.build_context(request), await new.build_context(request)
    assert current.task == prior.task + UNIT_PARENT_GUIDANCE
    assert current.project == prior.project and current.upstream == prior.upstream
    assert old.submission_schema(request) == new.submission_schema(request)
    assert request.extra == before and request.extra["idea_requirements"]["min_sources"] == 2


@pytest.mark.parametrize("reviewing", [False, True])
def test_original_gap_conflicts_remain_visible_not_rewritten(tmp_path: Path, reviewing: bool) -> None:
    session = _session(tmp_path, enabled=True)
    args = {**_args(), "gap": "Find two papers despite the declared single-paper scope"}
    original = deepcopy(args)
    policy = AgentLoopPolicy.from_mapping(session.config.raw["loop"])
    if reviewing:
        before = research_review_messages(task=session.request.user_request, project=session.context.project,
                                         gap=args, supplied_context=session.context.upstream)
        after = research_review_messages(task=session.request.user_request, project=session.context.project,
                                        gap=args, supplied_context=session.context.upstream, research_unit=_unit())
    else:
        prior = _session(tmp_path / "old", enabled=False)
        before = prior.author_messages(args, refs=["baseline_code"], minimum=1, policy=policy)
        after = session.author_messages(args, refs=["baseline_code"], minimum=1, policy=policy)
    assert after == before + unit_messages(_unit(), reviewing=reviewing)
    assert "explicitly report that assignment conflict" in after[-1].content
    assert args == original
    assert any(args["gap"] in message.content for message in after)
    assert any(session.request.user_request in message.content for message in after)
    assert digest([message.to_wire() for message in before]) != digest([message.to_wire() for message in after])
    assert not session.receipts and session.attempted == 0


@pytest.mark.parametrize("field", ["manifest", "output", "request"])
def test_partial_new_claim_cannot_upgrade_historical_records(field: str) -> None:
    # Negative host-claim inputs, not model/provider records.
    records: dict[str, dict[str, Any]] = {name: {} for name in ("manifest", "output", "request")}
    records[field].update(unit_fields(_unit()))
    assert research_unit_errors(records["manifest"], records["output"], request=records["request"], arguments=_args())


@pytest.fixture(scope="module")
def actual_child() -> Iterator[dict[str, Any]]:
    configured = os.environ.get("MARS_TEST_SINGLE_PUBLICATION_CHECKPOINT")
    if not configured:
        pytest.skip("requires terminal run22 first child; no research/provider result is synthesized")
    checkpoint = Path(configured)
    root = checkpoint.parents[3]
    request_path = root / "idea/research_delegations" / checkpoint.parent.name / "request.json"
    paths = [checkpoint, checkpoint.parent / "events.jsonl", request_path]
    state = json.loads(checkpoint.read_text())
    for observation in state["history"]:
        for source in observation.get("output", {}).get("sources", []):
            if source.get("read_receipt"):
                path = Path(source["read_receipt"])
                paths.append(path)
                receipt = json.loads(path.read_text())
                paths.append(Path(receipt["download_path"]))
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    assert before[checkpoint] == "070e01837279f640ac7a00f7376f85391600e0a7c87ae091cc05e0c43a7b8238"
    yield {"root": root, "state": state, "request": json.loads(request_path.read_text()),
           "events": [json.loads(line) for line in (checkpoint.parent / "events.jsonl").read_text().splitlines()]}
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths} == before


def test_actual_minimum_two_request_is_rejected_unchanged(actual_child: dict[str, Any]) -> None:
    args = actual_child["request"]["arguments"]
    before = deepcopy(args)
    assert args["min_sources"] == 2
    with pytest.raises(ValueError, match="explicit min_sources=1"):
        validate_unit_arguments(args, _unit())
    assert list(Draft202012Validator(unit_input_constraint()).iter_errors(args))
    assert args == before


def test_actual_one_paper_can_only_continue_under_hypothetical_minimum_one(actual_child: dict[str, Any]) -> None:
    state = actual_child["state"]
    before = deepcopy(state)
    assert state["status"] == "evidence_unavailable" and not state["candidate"]
    assert state["counts"]["reflections"] == 0 and not state["reflection_accepted"]
    assert material_state(state["history"])["counts"]["distinct_read_publications"] == 1
    view = LoopStopView("before_model", state["candidate"], state["history"], state["counts"], "act")
    params: dict[str, Any] = {"max_tool_steps": 10, "tools": tuple(actual_child["request"]["tools"]), "project": "pimc"}
    original = evidence_stop(view, min_sources=2, **params)
    assert original is not None and original.status == "evidence_unavailable"
    assert evidence_stop(view, min_sources=1, **params) is None  # no host stop; no report/review acceptance
    assert load_delegated_research(actual_child["root"], state["history"]) == ([], [])
    assert state == before
    condition = lambda item: evidence_stop(item, min_sources=1, **params)
    assert stop_fingerprint("original", condition, STOP_CONTRACT) != stop_fingerprint(
        "original", condition, STOP_CONTRACT + "+" + RESEARCH_UNIT_CONTRACT)


@pytest.mark.parametrize("mode", ["whole_report", "per_insight_collect_then_whole"])
def test_real_old_trace_cannot_be_relabelled_as_new_unit(actual_child: dict[str, Any], mode: str) -> None:
    # Derive only a rejected claim from an actual failed archive. No candidate or
    # successful response is made up, and none of these changes reach the archive.
    request = deepcopy(actual_child["request"])
    request.update(unit_fields(_unit()), min_sources=1, review_mode=mode)
    request["arguments"]["min_sources"] = 1
    claim = {**unit_fields(_unit()), "min_sources": 1, "request_ref": "request.json",
             "request_sha256": digest(request), "model_review_required": True, "model_review_passed": True}
    errors = research_unit_errors(claim, claim, request=request, arguments=request["arguments"],
                                 events=actual_child["events"])
    assert any("actual author/whole-review input" in error for error in errors)
    assert actual_child["state"]["status"] == "evidence_unavailable"


@pytest.mark.parametrize("mode", ["whole_report", "per_insight_collect_then_whole"])
def test_loader_requires_original_unit_request_even_in_whole_mode(tmp_path: Path, mode: str) -> None:
    configured = os.environ.get("MARS_TEST_SOURCE_IDENTITY_CHECKPOINT")
    if not configured:
        pytest.skip("requires actual accepted run13 report; no accepted fixture is generated")
    child = Path(configured)
    root = child.parents[3]
    parent = next(root.glob("agent_traces/idea/*/checkpoint.json"))
    observation = next(row for row in json.loads(parent.read_text())["history"]
                       if row.get("tool") == TOOL and row.get("ok")
                       and row["output"]["delegation_id"] == child.parent.name)
    original_manifest = root / observation["output"]["manifest_ref"]
    manifest = json.loads(original_manifest.read_text())
    paths = [parent, child, original_manifest, root / manifest["report_ref"]]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    # Existing real report still loads under its original whole-report contract.
    reports, _ = load_delegated_research(root, [observation])
    assert len(reports) == 1 and "research_unit" not in manifest
    for path in paths[1:]:
        target = tmp_path / path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    # Negative claim only: its authentic passed candidate cannot supply a missing
    # scope-bound request. The original report and checkpoint bytes remain exact.
    changed = deepcopy(observation)
    manifest.update(unit_fields(_unit()), review_mode=mode,
                    request_ref=f"idea/research_delegations/{child.parent.name}/request.json",
                    request_sha256="0" * 64)
    copied_manifest = tmp_path / observation["output"]["manifest_ref"]
    copied_manifest.write_text(json.dumps(manifest))
    changed["output"].update(unit_fields(_unit()), review_mode=mode,
        manifest_sha256=hashlib.sha256(copied_manifest.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="outside its run namespace or missing"):
        load_delegated_research(tmp_path, [changed])
    # A canonical host request also reveals the new contract when BOTH public
    # claims have been stripped. This must not fall through the legacy loader.
    request_path = root / "idea/research_delegations" / child.parent.name / "request.json"
    request = json.loads(request_path.read_text())
    request.update(unit_fields(_unit()), min_sources=1)
    request["arguments"]["min_sources"] = 1
    (tmp_path / request_path.relative_to(root)).write_text(json.dumps(request))
    shutil.copyfile(original_manifest, copied_manifest)
    with pytest.raises(ValueError, match="requires a run-relative path"):
        load_delegated_research(tmp_path, [observation])
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths} == before
