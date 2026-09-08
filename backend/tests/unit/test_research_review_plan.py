"""Pure contracts and immutable real archives; no provider or tool substitutes."""
from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.agents.idea.research_delegate import load_delegated_research, postprocessing_failure, research_policy, research_review_errors
from app.agents.idea.research_review_plan import (
    REVIEW_PLAN_CONTRACT, build_research_review_plan, insight_fields, parse_insight_review,
    research_plan_errors, research_review_mode,
)
from app.harness.agent_loop.review_plan import plan_payload, prepare_review_plan
from app.harness.agent_loop.trace import canonical, digest
from app.harness.llm.model_registry import get_agent_config
from app.harness.llm.provider_base import Message
from app.harness.schema.frontmatter_parser import parse
from app.settings import repo_root
from scripts.idea_research_continuation import Continuation, check_configuration
from scripts.run_idea_research_live import child_research_config, public_config


@pytest.mark.parametrize("value", [None, [], {"review_mode": None}, {"review_mode": True}, {"review_mode": "per_source"}])
def test_ambiguous_review_mode_is_rejected(value: object) -> None:
    with pytest.raises(ValueError, match="research"):
        research_review_mode(value)


def test_mode_is_optional_and_research_configuration_preserves_existing_limits() -> None:
    config = get_agent_config("idea_research")
    assert research_review_mode({}) == research_review_mode(config.raw["research"]) == "whole_report"
    assert "research_review_mode" not in public_config(config)
    selected = child_research_config(config.raw["research"], {"review_mode": "per_insight_then_whole"})
    assert selected["excerpt_context_chars"] == config.raw["research"]["excerpt_context_chars"]
    changed = replace(config, raw={**config.raw, "research": selected})
    assert public_config(changed)["research_review_mode"] == "per_insight_then_whole"
    assert research_policy(config, require_review=True) == research_policy(changed, require_review=True)
    with pytest.raises(ValueError, match="reflection"):
        research_policy(replace(changed, raw={**changed.raw, "loop": {"mode": "react", "trace": "full"}}), require_review=False)
    with pytest.raises(ValueError, match="only"):
        child_research_config({}, {"max_model_calls": 100})


def test_new_variant_changes_only_declared_review_mode_and_lead_effort() -> None:
    root = repo_root() / "configs/evaluation"
    baseline = yaml.safe_load((root / "idea_research_thinking_json_real.yaml").read_text())
    selected = yaml.safe_load((root / "idea_research_per_insight_real.yaml").read_text())
    assert selected.pop("child_research") == {"review_mode": "per_insight_then_whole"}
    assert selected["model"]["reasoning_effort"] == "low"
    selected["model"]["reasoning_effort"] = "high"
    assert selected == baseline
    assert baseline["loop"]["max_model_calls"] == 36 and baseline["child_loop"]["max_model_calls"] == 16


def test_continuation_configuration_rejects_changed_review_mode(tmp_path: Path) -> None:
    # Configuration comparison input, not a claimed execution checkpoint.
    config = get_agent_config("idea_research")
    child = public_config(config)
    messages = [Message("user", "Manually authored task configuration.")]
    source = Continuation(tmp_path, {"lead_config": {}, "child_config": child,
                                    "messages": [asdict(message) for message in messages]},
                          {}, tmp_path / "absent.json", {}, {}, {}, 1.0)
    check_configuration(source, lead={}, child=child, messages=messages)
    changed = replace(config, raw={**config.raw, "research": {"review_mode": "per_insight_then_whole"}})
    with pytest.raises(ValueError, match="configuration differs"):
        check_configuration(source, lead={}, child=public_config(changed), messages=messages)


def _human_contract(verdict: str) -> dict[str, Any]:
    # Deliberately human-authored parser input; it is never a model/tool response.
    return {"checks": [{"field": field, "claims": [{"statement": "A manually authored contract claim.",
                "assumptions": "Contract input only.", "verification": "No execution or scientific result is claimed.",
                "verdict": verdict}]} for field in ("paper_finding", "transfer_idea", "limitations[0]")],
            "issues": [], "rationale": "Human-authored parser contract."}


@pytest.mark.parametrize("verdict", ["supported", "hypothesis"])
def test_complete_nonblocking_contract_normalizes_decision_without_filling_checks(verdict: str) -> None:
    authored = _human_contract(verdict)
    result = parse_insight_review(json.dumps(authored), fields=("paper_finding", "transfer_idea", "limitations[0]"))
    assert result.decision == {"accept": True, "issues": [], "rationale": authored["rationale"]}
    assert result.details == authored


@pytest.mark.parametrize("verdict", ["incorrect", "unverifiable"])
def test_blocking_verdict_cannot_be_accepted_or_get_a_host_authored_reason(verdict: str) -> None:
    authored = _human_contract(verdict)
    fields = ("paper_finding", "transfer_idea", "limitations[0]")
    with pytest.raises(ValueError, match="paper_finding.*transfer_idea.*limitations"):
        parse_insight_review(json.dumps(authored), fields=fields)
    authored["issues"] = ["An explicit human-authored negative contract reason."]
    result = parse_insight_review(json.dumps(authored), fields=fields)
    assert result.decision["accept"] is False and result.decision["issues"] == authored["issues"]


def test_nonblocking_claims_cannot_produce_unrelated_blocking_feedback() -> None:
    authored = _human_contract("hypothesis")
    authored["issues"] = ["Human-authored inconsistency: block despite all claims being nonblocking."]
    with pytest.raises(ValueError, match="issues require an incorrect or unverifiable claim"):
        parse_insight_review(json.dumps(authored), fields=("paper_finding", "transfer_idea", "limitations[0]"))


def test_field_parser_rejects_missing_duplicate_or_undeclared_fields() -> None:
    original = _human_contract("supported")
    fields = ("paper_finding", "transfer_idea", "limitations[0]")
    values = []
    missing = deepcopy(original)
    missing["checks"].pop()
    values.append(missing)
    duplicate = deepcopy(original)
    duplicate["checks"][1] = deepcopy(duplicate["checks"][0])
    values.append(duplicate)
    empty = deepcopy(original)
    empty["checks"][0]["claims"][0]["verification"] = " "
    values.append(empty)
    values.append({**original, "accept": True})
    for value in values:
        with pytest.raises(ValueError):
            parse_insight_review(json.dumps(value), fields=fields)
    with pytest.raises(ValueError, match="duplicate"):
        parse_insight_review('{"checks":[],"checks":[],"issues":[],"rationale":"x"}', fields=fields)


def test_legacy_review_cannot_gain_per_insight_acceptance_from_flags() -> None:
    manifest: dict[str, Any] = {}
    assert research_plan_errors(manifest, {}, {}, "historical") == []
    assert research_plan_errors(manifest, {"review_mode": "per_insight_then_whole"}, {}, "historical")
    claim = {"review_mode": "per_insight_then_whole", "model_review_required": True, "model_review_passed": True}
    assert research_review_errors(claim, claim, {}, "historical")
    assert research_plan_errors({}, {}, {"review_plan": {}}, "historical")


@pytest.fixture
def archive() -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    configured = os.environ.get("MARS_TEST_SOURCE_IDENTITY_CHECKPOINT")
    if not configured:
        pytest.skip("requires actual run13 research checkpoint; no execution substitute is generated")
    checkpoint = Path(configured)
    state = json.loads(checkpoint.read_text())
    root = checkpoint.parents[3]
    request_path = root / "idea/research_delegations" / checkpoint.parent.name / "request.json"
    request = json.loads(request_path.read_text())
    input_path = root / "input/request.json"
    initial = json.loads(input_path.read_text())
    events_path = checkpoint.parent / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    first = next(event["visible"] for event in events if event["kind"] == "model_request")
    assert request["context_refs"] == []
    context = {"task": initial["scenario"]["question"], "project": first[1]["content"],
               "gap": request["arguments"], "min_sources": request["min_sources"], "supplied_context": {}}
    paths = {checkpoint, request_path, input_path, events_path}
    for observation in state["history"]:
        if observation.get("tool") == "search.fetch_sources" and observation.get("ok"):
            for row in observation["output"]["sources"]:
                if row.get("ok") and row.get("read_receipt"):
                    paths.update((Path(row["read_receipt"]), Path(row["download_path"])))
    original = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    yield state, context
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == value for path, value in original.items())


def test_actual_plan_keeps_full_insight_and_all_its_read_windows_without_other_insights(
    archive: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    state, context = archive
    original = deepcopy(state)
    plan = build_research_review_plan(state["candidate"], state["history"], **context)
    report = parse(state["candidate"]).metadata
    assert plan.contract_id == REVIEW_PLAN_CONTRACT and plan.candidate_sha256 == digest(state["candidate"])
    assert [unit.unit_id for unit in plan.units] == [insight["id"] for insight in report["insights"]]
    assert len(plan.units) == 3  # This actual run13 report, not a configured quota.
    for insight, unit in zip(report["insights"], plan.units, strict=True):
        payload = json.loads(next(message.content.split("\n", 1)[1] for message in unit.messages
                                 if message.content.startswith("[untrusted complete insight")))
        assert payload["insight"] == insight
        assert payload["source"] == next(source for source in report["sources"] if source["source_id"] == insight["source_id"])
        rows = json.loads(next(message.content.split("\n", 1)[1] for message in unit.messages
                              if message.content.startswith("[untrusted actual matching")))
        reads = [row for row in rows if row["tool"] == "search.fetch_sources"]
        assert len(reads) == (2 if insight["source_id"] == report["sources"][0]["source_id"] else 1)
        assert any(row["read_receipt"] == insight["read_receipt"] for row in reads)
        for row in rows:
            observation = state["history"][row["observation_index"]]
            originals = observation["output"]["sources" if row["tool"] == "search.fetch_sources" else "hits"]
            assert row["source_row"] == originals[row["source_index"]]
            assert row["source_row_sha256"] == digest(row["source_row"])
        combined = "\n".join(message.content for message in unit.messages)
        assert context["task"] in combined and canonical(context["gap"]) in combined
        assert "its factual assumptions are not evidence" in combined
        assert "reflection_accepted" not in combined and "review_issues" not in combined
        for other in report["insights"]:
            if other["id"] != insight["id"]:
                assert other["paper_finding"] not in combined
        assert unit.response_schema["properties"]["checks"]["minItems"] == len(insight_fields(insight))
    assert plan_payload(plan)["candidate_sha256"] == digest(state["candidate"])
    assert state == original


def test_actual_review_plan_rebuild_survives_canonical_request_archiving(
    archive: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    state, context = archive
    original = {**context, "gap": dict(reversed(list(context["gap"].items()))),
                "supplied_context": {"z": "Manually supplied original baseline context.", "a": "Original task note."}}
    first = build_research_review_plan(state["candidate"], state["history"], **original)
    archived = json.loads(canonical(original))
    rebuilt = build_research_review_plan(state["candidate"], json.loads(canonical(state["history"])), **archived)
    assert plan_payload(first) == plan_payload(rebuilt)


def test_actual_plan_rejects_version_tampering_instead_of_reviewing_unverified_sources(
    archive: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    state, context = archive
    report = parse(state["candidate"]).metadata
    report["sources"][0]["url"] = report["sources"][0]["url"].replace("v1", "v2")
    candidate = "---\n" + yaml.safe_dump(report, allow_unicode=True) + "---\n\n" + report["human_summary"]
    with pytest.raises(ValueError, match="invalid dossier"):
        build_research_review_plan(candidate, state["history"], **context)


def test_actual_candidate_cannot_start_plan_without_budget_for_whole_review(
    archive: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    state, context = archive
    original = deepcopy(state)
    plan = build_research_review_plan(state["candidate"], state["history"], **context)
    derived = deepcopy(state)
    # Pure negative budget transition over a real candidate, not a replayed model.
    assert prepare_review_plan(derived, plan, contract_id=REVIEW_PLAN_CONTRACT,
        max_model_calls=state["counts"]["model_requests"] + len(plan.units)) is False
    assert derived["status"] == "budget_exhausted" and not derived["reflection_accepted"]
    assert derived["review_plan"]["results"] == []
    assert state == original


def test_actual_whole_report_manifest_does_not_prove_new_review_mode(
    archive: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    state, _ = archive
    checkpoint = Path(os.environ["MARS_TEST_SOURCE_IDENTITY_CHECKPOINT"])
    root = checkpoint.parents[3]
    path = root / "idea/research_delegations" / checkpoint.parent.name / "manifest.json"
    before = path.read_bytes()
    manifest = json.loads(before)
    parent = json.loads(next(root.glob("agent_traces/idea/*/checkpoint.json")).read_text())
    output = next(row["output"] for row in parent["history"] if row.get("tool") == "idea.research_delegate"
                  and row.get("ok") and row["output"].get("delegation_id") == checkpoint.parent.name)
    assert research_review_errors(manifest, output, state, state["candidate"]) == []
    declared = {**manifest, "review_mode": "per_insight_then_whole"}
    assert research_review_errors(declared, {**output, "review_mode": "per_insight_then_whole"}, state,
                                  state["candidate"], trace_root=checkpoint.parent)
    assert path.read_bytes() == before


def test_actual_archive_with_broken_new_claim_keeps_failed_delegation_identity(
    archive: tuple[dict[str, Any], dict[str, Any]], tmp_path: Path,
) -> None:
    state, context = archive
    checkpoint = Path(os.environ["MARS_TEST_SOURCE_IDENTITY_CHECKPOINT"])
    original_root = checkpoint.parents[3]
    identifier = checkpoint.parent.name
    target_ref = "idea/research_delegations/" + identifier
    manifest_ref = target_ref + "/manifest.json"
    manifest = json.loads((original_root / manifest_ref).read_text())
    parent = json.loads(next(original_root.glob("agent_traces/idea/*/checkpoint.json")).read_text())
    observation = next(row for row in parent["history"] if row.get("tool") == "idea.research_delegate"
                       and row.get("ok") and row["output"].get("delegation_id") == identifier)
    # Immutable real report/checkpoint/request copies, with ONLY a negative new-mode
    # claim mutation. This is a loader failure input, never an executed tool result.
    for reference in (manifest["report_ref"], manifest["checkpoint_ref"], target_ref + "/request.json"):
        target = tmp_path / reference
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((original_root / reference).read_bytes())
    manifest.update({"review_mode": "per_insight_then_whole", "request_ref": target_ref + "/request.json",
                     "request_sha256": hashlib.sha256((tmp_path / target_ref / "request.json").read_bytes()).hexdigest()})
    (tmp_path / manifest_ref).write_text(json.dumps(manifest))
    tampered = deepcopy(observation)
    tampered["output"].update({"review_mode": "per_insight_then_whole",
                               "manifest_sha256": hashlib.sha256((tmp_path / manifest_ref).read_bytes()).hexdigest()})
    with pytest.raises(ValueError, match="review") as caught:
        load_delegated_research(tmp_path, [tampered])
    policy = research_policy(get_agent_config("idea_research"), require_review=True)
    failed = postprocessing_failure(root=original_root, trace=checkpoint.parent, delegation_id=identifier,
        error=caught.value, min_sources=context["min_sources"], policy=policy,
        gap=context["gap"]["gap"], project=parse(state["candidate"]).metadata["project"])
    assert failed["delegation_id"] == identifier and failed["status"] == "error"
    assert failed["failure_type"] == "research_result_verification_failed"
    assert failed["checkpoint_status"] == state["status"] == "passed"
    assert failed["checkpoint_ref"] == checkpoint.relative_to(original_root).as_posix()
    assert failed["checkpoint_sha256"] == hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    assert failed["attempts"] and failed["read_sources"]
    assert failed["usable_as_final_evidence"] is False and failed["scientific_validated"] is False


def test_original_real_field_review_is_parsed_without_revising_its_checks() -> None:
    configured = os.environ.get("MARS_TEST_RESEARCH_FIELD_REVIEW")
    if not configured:
        pytest.skip("requires archived real field-review response; no provider is invoked")
    path = Path(configured) / "response.json"
    before = path.read_bytes()
    response = json.loads(before)
    fields = ("paper_finding", "transfer_idea", "limitations[0]", "limitations[1]", "limitations[2]")
    result = parse_insight_review(response["text"], fields=fields)
    assert result.details == json.loads(response["text"])
    assert result.decision["accept"] is False
    assert any(claim["verdict"] == "incorrect" for check in result.details["checks"] for claim in check["claims"])
    assert path.read_bytes() == before


def test_real_scope_failure_is_rejected_as_inconsistent_without_rewriting_the_response() -> None:
    configured = os.environ.get("MARS_TEST_INSIGHT_SCOPE_COMPONENT")
    if not configured:
        pytest.skip("requires the actual run15 scope-review component; no substitute response is generated")
    root = Path(configured)
    paths = (root / "request.json", root / "original_review_response.json", root / "response.json")
    original = {path: path.read_bytes() for path in paths}
    request = json.loads(original[paths[0]])
    fields = tuple(request["response_contract"]["properties"]["checks"]["items"]["properties"]["field"]["enum"])
    response = json.loads(original[paths[1]])["visible"]
    assert all(claim["verdict"] in {"supported", "hypothesis"}
               for check in json.loads(response)["checks"] for claim in check["claims"])
    assert json.loads(response)["issues"]
    with pytest.raises(ValueError, match="issues require an incorrect or unverifiable claim"):
        parse_insight_review(response, fields=fields)
    # A separate, actual one-call scope comparison is parsed as its own response;
    # it neither repairs nor supersedes the original run's rejected decision.
    revised_response = json.loads(original[paths[2]])["text"]
    result = parse_insight_review(revised_response, fields=fields)
    assert result.details == json.loads(revised_response)
    assert result.decision["accept"] is True
    assert all(path.read_bytes() == value for path, value in original.items())
