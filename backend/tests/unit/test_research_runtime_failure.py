"""Pure budget contracts and actual overflow replay, without executing a provider."""
from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.agents.idea.research_delegate import load_delegated_research, resumed_delegation_count
from app.agents.idea.research_gap import failure_record
from app.agents.idea.research_review import RESEARCH_REVIEW_RUBRIC, research_review_messages
from app.agents.idea.research_stop import lead_evidence_stop
from app.harness.agent_loop.context import pack_context, token_upper_bound
from app.harness.agent_loop.executor import budget_message, missing_review_evidence, reflection_instruction
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.stop import LoopStopView


@pytest.mark.parametrize("budget", [4000, 96000, 128000, 256000, 512000])
def test_expanded_input_budget_is_explicit_and_fingerprinted(budget: int) -> None:
    policy = AgentLoopPolicy.from_mapping({"input_token_budget": budget})
    assert policy.input_token_budget == budget
    assert policy.fingerprint_data()["input_token_budget"] == budget
    assert policy.max_model_calls == AgentLoopPolicy().max_model_calls
    assert policy.max_tool_steps == AgentLoopPolicy().max_tool_steps


@pytest.mark.parametrize("budget", [3999, 512001, -1, True, 256000.0, "256000", None])
def test_expanded_input_budget_remains_bounded_and_integer(budget: object) -> None:
    with pytest.raises(ValueError, match="input_token_budget"):
        AgentLoopPolicy.from_mapping({"input_token_budget": budget})


def test_larger_input_budget_does_not_make_old_checkpoint_fingerprint_identical() -> None:
    previous = AgentLoopPolicy(input_token_budget=96000)
    expanded = replace(previous, input_token_budget=256000)
    assert previous.fingerprint_data() != expanded.fingerprint_data()
    assert {key: value for key, value in previous.fingerprint_data().items() if key != "input_token_budget"} == {
        key: value for key, value in expanded.fingerprint_data().items() if key != "input_token_budget"}


CHILD = "9fa062bc7ac445b9a9d1adaa3c966056"
PARENT = "3c0c1017f59a4674a51cd322857f29e2"


def _read_json(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_text())
    return value


@pytest.fixture
def actual_overflow_run() -> Iterator[Path]:
    configured = os.environ.get("MARS_TEST_RESEARCH_OVERFLOW_RUN")
    if not configured:
        pytest.skip("requires actual run 12 overflow records; no substitute execution is generated")
    root = Path(configured)
    paths = [root / "input/request.json", root / f"idea/research_delegations/{CHILD}/request.json",
             root / f"agent_traces/idea_research/{CHILD}/checkpoint.json",
             root / f"agent_traces/idea_research/{CHILD}/events.jsonl",
             root / f"agent_traces/idea/{PARENT}/checkpoint.json"]
    snapshots = {path: path.read_bytes() for path in paths}
    assert hashlib.sha256(snapshots[paths[2]]).hexdigest() == (
        "b44eb1b1934ce7754dcc1e7e5c7bde0be109b66a00fe8dfd4619a279ee6f92b3")
    yield root
    assert all(path.read_bytes() == original for path, original in snapshots.items())


def test_actual_overflow_failure_preserves_evidence_and_consumed_delegation(actual_overflow_run: Path) -> None:
    root = actual_overflow_run
    trace = root / f"agent_traces/idea_research/{CHILD}"
    checkpoint = _read_json(trace / "checkpoint.json")
    original = deepcopy(checkpoint)
    request = _read_json(root / f"idea/research_delegations/{CHILD}/request.json")
    scenario = _read_json(root / "input/request.json")["scenario"]
    policy = AgentLoopPolicy.from_mapping(scenario["child_loop"])
    events = [json.loads(line) for line in (trace / "events.jsonl").read_text().splitlines()]
    error = next(event for event in events if event["event_seq"] == 64)
    assert error["kind"] == "error" and error["error_type"] == "ValueError"
    assert "exceed input budget" in error["message"]
    assert checkpoint["status"] == "error" and checkpoint["next_phase"] == "reflect"
    assert checkpoint["reflection_accepted"] is False

    record = failure_record(delegation_id=CHILD, trace_ref=trace.relative_to(root).as_posix(),
        checkpoint=checkpoint, min_sources=request["min_sources"], max_tool_steps=policy.max_tool_steps,
        max_model_calls=policy.max_model_calls, gap=request["arguments"]["gap"], project=scenario["project"])
    # Attach the archived exception as a derived negative record, not a replayed tool result.
    record["runtime_error"] = {"type": error["error_type"], "message": error["message"]}
    assert record["delegation_id"] == CHILD
    assert record["status"] == "error" and record["failure_type"] == "research_runtime_failed"
    assert record["usable_as_final_evidence"] is False and record["scientific_validated"] is False
    assert record["remaining_budget"] == {"tool_calls": 4, "model_calls": 3}
    assert len(record["attempts"]) == 6 and len(record["read_sources"]) == 4
    assert record["observed_material_counts"]["distinct_read_publications"] == 1
    assert record["remaining_gaps"]
    assert checkpoint == original

    parent_path = root / f"agent_traces/idea/{PARENT}/checkpoint.json"
    parent = _read_json(parent_path)
    history = parent["history"][:3]
    assert history[2]["args"] == request["arguments"]
    assert history[2]["ok"] is False and history[2]["output"] is None
    # The original failure lost its ID, so resuming that untouched archive must fail closed.
    with pytest.raises(ValueError, match="unreconciled started research delegation"):
        resumed_delegation_count(root, history, run_id=root.name, parent_invocation=str(parent_path.parent))
    before = LoopStopView("before_model", "", history, {"tool_dispatches": 3}, "act")
    assert lead_evidence_stop(before, run_root=root, min_sources=2, max_delegations=3,
                              max_tool_steps=5, can_delegate=True) is None

    # Apply only the new failure-record transformation to the real failed observation in memory.
    derived_history = deepcopy(history)
    derived_history[2]["output"] = record
    assert resumed_delegation_count(root, derived_history, run_id=root.name,
                                    parent_invocation=str(parent_path.parent)) == 3
    after = replace(before, observations=derived_history)
    stop = lead_evidence_stop(after, run_root=root, min_sources=2, max_delegations=3,
                              max_tool_steps=5, can_delegate=True)
    assert stop is not None and stop.status == "evidence_unavailable"
    assert len(stop.details["started_delegations"]) == 3
    assert stop.details["verified_report_publications"] == 0
    assert load_delegated_research(root, derived_history) == ([], [])
    assert parent["history"][2]["output"] is None


def test_actual_review_overflow_fits_expanded_budget_with_complete_observations(actual_overflow_run: Path) -> None:
    root = actual_overflow_run
    trace = root / f"agent_traces/idea_research/{CHILD}"
    checkpoint = _read_json(trace / "checkpoint.json")
    request = _read_json(root / f"idea/research_delegations/{CHILD}/request.json")
    scenario = _read_json(root / "input/request.json")["scenario"]
    events = [json.loads(line) for line in (trace / "events.jsonl").read_text().splitlines()]
    first = next(event["visible"] for event in events if event["kind"] == "model_request")
    task_prefix = "Overall research task:\n"
    assert first[2]["content"].startswith(task_prefix)
    assert request["context_refs"] == [] and not checkpoint["protocol_output"]
    assert checkpoint["next_phase"] == "reflect"
    old_policy = AgentLoopPolicy.from_mapping(scenario["child_loop"])
    assert old_policy.input_token_budget == 96000
    expanded = replace(old_policy, input_token_budget=256000)
    pinned = research_review_messages(task=first[2]["content"].removeprefix(task_prefix),
        project=first[1]["content"], gap=request["arguments"])
    pinned += [budget_message(expanded, checkpoint["counts"]), reflection_instruction(RESEARCH_REVIEW_RUBRIC)]
    kwargs: dict[str, Any] = {"observation_chars": expanded.observation_chars, "native": True,
        "reviewing": True, "review_issues": checkpoint["review_issues"],
        "validation_issues": checkpoint["validation_issues"], "required_review_tools": ("search.fetch_sources",)}
    with pytest.raises(ValueError, match="exceed input budget; nothing was silently dropped"):
        pack_context(pinned, checkpoint["history"], checkpoint["feedback"], checkpoint["candidate"],
                     budget=old_policy.input_token_budget, **kwargs)
    messages, manifest = pack_context(pinned, checkpoint["history"], checkpoint["feedback"], checkpoint["candidate"],
                                     budget=expanded.input_token_budget, **kwargs)
    assert old_policy.input_token_budget < token_upper_bound(messages) <= expanded.input_token_budget
    assert manifest["estimator"] == "utf8_byte_upper_bound"
    assert not manifest["compressed_history"] and not manifest["omitted_history"]
    assert manifest["preserved_review_history"] == [0, 1, 4, 5]
    expected = [row for row in checkpoint["history"] if row["tool"] == "search.fetch_sources" and row["ok"]]
    actual = [json.loads(message.content.split("\n", 1)[1]) for message in messages
              if message.content.startswith("[untrusted complete review Observation]\n")]
    assert actual == expected and len(actual) == 4
    assert missing_review_evidence(checkpoint["history"], manifest, ("search.fetch_sources",),
                                   observation_chars=expanded.observation_chars) == []
    assert all(message.role in {"system", "user"} and not message.tool_calls for message in messages)
    assert any(message.content.endswith(checkpoint["candidate"]) for message in messages)
    assert not checkpoint["reflection_accepted"]  # Packing a review does not execute or accept it.
