"""Immutable real failures and configuration contracts; no re-labelled review execution."""
from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import replace
from functools import partial
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.agents.idea.research_delegate import research_policy
from app.agents.idea.research_review_plan import (
    COLLECT_REVIEW_PLAN_CONTRACT, REVIEW_PLAN_CONTRACT, build_research_review_plan,
    research_plan_errors, research_review_contract, research_review_mode,
)
from app.agents.idea.runtime_profile import SNAPSHOT_FILE, bind_profile_snapshot, resolve_idea_profile
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.protocol import parse_action
from app.harness.agent_loop.review_plan import plan_payload, review_plan_fingerprint, review_plan_trace_errors, validate_review_plan_resume
from app.harness.agent_loop.trace import canonical, digest
from app.harness.llm.model_registry import get_agent_config
from app.harness.llm.provider_base import LLMConfig
from app.settings import Settings, repo_root
from scripts.run_idea_research_live import child_research_config, public_config


def test_new_mode_has_a_distinct_contract_without_changing_old_defaults() -> None:
    assert research_review_mode({}) == "whole_report"
    assert research_review_contract("whole_report") is None
    assert research_review_contract("per_insight_then_whole") == REVIEW_PLAN_CONTRACT
    mode = research_review_mode({"review_mode": "per_insight_collect_then_whole"})
    assert research_review_contract(mode) == COLLECT_REVIEW_PLAN_CONTRACT
    base = get_agent_config("idea_research")
    selected = child_research_config(base.raw["research"], {"review_mode": mode})
    changed = replace(base, raw={**base.raw, "research": selected})
    assert public_config(changed)["research_review_mode"] == mode
    assert research_policy(changed, require_review=True) == research_policy(base, require_review=True)
    with pytest.raises(ValueError, match="reflection"):
        research_policy(replace(changed, raw={**changed.raw, "loop": {"mode": "react", "trace": "full"}}), require_review=False)


def test_v3_profile_changes_only_the_child_review_mode_and_rejects_cross_profile_resume(tmp_path: Path) -> None:
    old = resolve_idea_profile("experimental_research_pro_per_insight_v2")
    new = resolve_idea_profile("experimental_research_pro_per_insight_v3")
    assert old is not None and new is not None
    expected = deepcopy(old.snapshot()["configuration"])
    expected["child"]["research"]["review_mode"] = "per_insight_collect_then_whole"
    assert new.snapshot()["configuration"] == expected
    assert new.snapshot()["status"] == "experimental" and new.snapshot()["validated"] is False
    settings = Settings(_env_file=None, mars_idea_runtime_profile="experimental_research_pro_per_insight_v3")  # type: ignore[call-arg]
    assert settings.mars_idea_runtime_profile == new.profile_id
    catalog = yaml.safe_load((repo_root() / "configs/idea_runtime_profiles.yaml").read_text())["profiles"]
    old_definition = deepcopy(catalog[old.profile_id])
    old_definition["child"]["research"]["review_mode"] = "per_insight_collect_then_whole"
    assert catalog[new.profile_id] == old_definition
    bind_profile_snapshot(tmp_path, old)
    before = (tmp_path / SNAPSHOT_FILE).read_bytes()
    with pytest.raises(ValueError, match="configuration differs"):
        bind_profile_snapshot(tmp_path, new, resume=True)
    assert (tmp_path / SNAPSHOT_FILE).read_bytes() == before


@pytest.fixture(scope="module")
def run19_child() -> Iterator[dict[str, Any]]:
    configured = os.environ.get("MARS_TEST_COLLECT_REVIEW_CHECKPOINT")
    if not configured:
        pytest.skip("requires the terminal first run-19 research child; no replacement archive is generated")
    checkpoint = Path(configured)
    assert hashlib.sha256(checkpoint.read_bytes()).hexdigest() == "10e378c56d04230ed0ef017350cffd6ab6253347f302830e7bcc7a49d3b5a156"
    trace, root = checkpoint.parent, checkpoint.parents[3]
    state = json.loads(checkpoint.read_text())
    assert state["status"] == "reflection_rejected"
    events_path = trace / "events.jsonl"
    assert hashlib.sha256(events_path.read_bytes()).hexdigest() == "b31368662c549ac2104c2c61ce5cdccb33e586349a23150111c4ec0a9cf18ed8"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    directory = root / "idea/research_delegations" / trace.name
    request = json.loads((directory / "request.json").read_text())
    paths = {p for folder in (trace, directory) for p in folder.rglob("*") if p.is_file()}
    for observation in state["history"]:
        output = observation.get("output")
        if isinstance(output, dict):
            for source in output.get("sources", []):
                if source.get("ok"):
                    paths.update(Path(source[key]) for key in ("read_receipt", "download_path") if source.get(key))
    original = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    context = {**request["review_context"], "gap": request["arguments"], "min_sources": request["min_sources"]}
    yield {"root": root, "trace": trace, "state": state, "events": events, "request": request, "context": context}
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == value for path, value in original.items())


def test_real_old_v4_rejections_keep_their_own_candidates_and_exact_inputs(run19_child: dict[str, Any]) -> None:
    archive = run19_child
    state = archive["state"]
    before = deepcopy(state)
    plans = [*state["review_plan_history"], state["review_plan"]]
    assert len(plans) == 2 and plans[0]["candidate_sha256"] != plans[1]["candidate_sha256"]
    for author_request, stored in zip((8, 11), plans, strict=True):
        response = next(event for event in archive["events"]
                        if event["kind"] == "model_response" and event["request"] == author_request)
        candidate = parse_action(response["visible"])["final"]
        assert digest(candidate) == stored["candidate_sha256"]
        built = build_research_review_plan(candidate, state["history"], **archive["context"], contract_id=REVIEW_PLAN_CONTRACT)
        payload = plan_payload(built)
        assert all(stored[key] == value for key, value in payload.items())
        assert digest(payload) == stored["plan_sha256"]
        assert "failure_mode" not in payload
        for result in stored["results"]:
            wire = next(event for event in archive["events"]
                        if event["kind"] == "model_response" and event["request"] == result["request"])
            unit = next(unit for unit in built.units if unit.unit_id == result["unit_id"])
            parsed = unit.parse_response(wire["visible"])
            assert parsed.decision == result["decision"] and parsed.details == result["details"]
    assert [(result["request"], result["decision"]["accept"]) for plan in plans for result in plan["results"]] == [(9, False), (12, True), (13, False)]
    assert state["counts"]["reflections"] == 2 and all(plan["status"] == "rejected" for plan in plans)
    assert review_plan_trace_errors(state, archive["trace"]) == []
    assert state == before


def test_v5_changes_only_plan_execution_contract_not_real_old_candidate_unit_inputs(run19_child: dict[str, Any]) -> None:
    archive = run19_child
    original = deepcopy(archive["state"])
    response = next(event for event in archive["events"]
                    if event["kind"] == "model_response" and event["request"] == 8)
    candidate = parse_action(response["visible"])["final"]
    old = build_research_review_plan(candidate, archive["state"]["history"], **archive["context"], contract_id=REVIEW_PLAN_CONTRACT)
    new = build_research_review_plan(candidate, archive["state"]["history"], **archive["context"], contract_id=COLLECT_REVIEW_PLAN_CONTRACT)
    old_payload, new_payload = plan_payload(old), plan_payload(new)
    assert new.failure_mode == "collect_units" and old.failure_mode == "fail_fast"
    assert new_payload == {**old_payload, "contract_id": COLLECT_REVIEW_PLAN_CONTRACT,
                           "failure_mode": "collect_units", "review_plan_runtime_version": 2}
    assert new_payload["units"] == old_payload["units"] and new.candidate_sha256 == old.candidate_sha256
    config, policy = LLMConfig(provider="deepseek", model="deepseek-v4-pro"), AgentLoopPolicy(mode="reflection", trace="full")
    factory = partial(build_research_review_plan, **archive["context"])
    assert review_plan_fingerprint("same-input", factory, old.contract_id, config, policy) != review_plan_fingerprint(
        "same-input", factory, new.contract_id, config, policy)
    with pytest.raises(ValueError, match="original explicit contract"):
        validate_review_plan_resume(archive["state"], archive["trace"], contract_id=COLLECT_REVIEW_PLAN_CONTRACT)
    assert archive["state"] == original
    # No model response is generated, reassigned to another candidate, or
    # represented as having completed these new v5 units.


def test_old_manifest_cannot_gain_collect_semantics_from_mode_flags(run19_child: dict[str, Any]) -> None:
    archive = run19_child
    manifest = {"review_mode": "per_insight_collect_then_whole", "model_review_required": True,
                "model_review_passed": True, "review_plan": {"contract_id": REVIEW_PLAN_CONTRACT}}
    request = {**archive["request"], "review_mode": "per_insight_collect_then_whole"}
    assert any("failure-mode contract disagree" in error for error in research_plan_errors(
        manifest, deepcopy(manifest), archive["state"], archive["state"]["candidate"],
        trace_root=archive["trace"], request_record=request))
