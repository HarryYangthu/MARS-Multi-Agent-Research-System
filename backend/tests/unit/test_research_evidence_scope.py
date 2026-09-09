"""Real archived inputs and pure contracts; no provider, tool or scientific-success substitutes."""
from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from functools import partial
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.agents.base import ContextPack, RunRequest
from app.agents.idea.agent import IdeaAgent
from app.agents.idea.research_delegate import ResearchSession, load_delegated_research
from app.agents.idea.research_review import RESEARCH_EVIDENCE_SCOPE_GUIDANCE, RESEARCH_REVIEW_RUBRIC, research_review_messages
from app.agents.idea.research_review_plan import (
    LEGACY_REVIEW_PLAN_CONTRACT, REVIEW_PLAN_CONTRACT, STATISTICAL_REVIEW_PLAN_CONTRACT,
    build_research_review_plan, insight_fields, parse_insight_review,
)
from app.harness.agent_loop.context import pack_context
from app.harness.agent_loop.executor import budget_message, reflection_instruction
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.protocol import parse_action
from app.harness.agent_loop.review_plan import plan_payload, review_plan_fingerprint, validate_review_plan_resume
from app.harness.agent_loop.trace import digest
from app.harness.llm.model_registry import get_agent_config
from app.harness.llm.provider_base import LLMConfig
from app.harness.schema.frontmatter_parser import parse
from app.harness.tools.registry import ToolRegistry


def _hashes(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def _archive(environment: str, identifier: str, contract: str) -> Iterator[dict[str, Any]]:
    configured = os.environ.get(environment)
    if not configured:
        pytest.skip(f"requires actual terminal archive via {environment}; no replacement is generated")
    root = Path(configured)
    before = _hashes(root)
    trace = root / "agent_traces/idea_research" / identifier
    state = json.loads((trace / "checkpoint.json").read_text())
    request = json.loads((root / "idea/research_delegations" / identifier / "request.json").read_text())
    assert state["review_plan"]["contract_id"] == contract
    context = {**request["review_context"], "gap": request["arguments"], "min_sources": request["min_sources"]}
    events = [json.loads(line) for line in (trace / "events.jsonl").read_text().splitlines()]
    yield {"root": root, "trace": trace, "state": state, "request": request, "context": context, "events": events}
    assert _hashes(root) == before


@pytest.fixture(scope="module")
def run17() -> Iterator[dict[str, Any]]:
    yield from _archive("MARS_TEST_RESEARCH_SCOPE_RUN", "575b2ab6bd2c443bab5cb4c0b1a54ee6", STATISTICAL_REVIEW_PLAN_CONTRACT)


@pytest.fixture(scope="module")
def run16() -> Iterator[dict[str, Any]]:
    yield from _archive("MARS_TEST_RESEARCH_SCOPE_V2_RUN", "c6def1a97e50434ca95783a84b7eba64", LEGACY_REVIEW_PLAN_CONTRACT)


def _assert_exact_legacy_plan(archive: dict[str, Any]) -> None:
    state, context = archive["state"], archive["context"]
    original = deepcopy(state)
    rebuilt = build_research_review_plan(state["candidate"], state["history"], **context,
                                         contract_id=state["review_plan"]["contract_id"])
    payload = plan_payload(rebuilt)
    assert all(state["review_plan"][key] == value for key, value in payload.items())
    assert digest(payload) == state["review_plan"]["plan_sha256"]
    assert all(RESEARCH_EVIDENCE_SCOPE_GUIDANCE not in unit.messages[0].content for unit in rebuilt.units)
    parent_path = next(archive["root"].glob("agent_traces/idea/*/checkpoint.json"))
    parent = json.loads(parent_path.read_text())
    reports, observations = load_delegated_research(archive["root"], parent["history"])
    assert len(reports) == 1
    assert observations == state["history"]
    assert state == original


def test_actual_v2_inputs_and_manifest_remain_exact(run16: dict[str, Any]) -> None:
    _assert_exact_legacy_plan(run16)


def test_actual_v3_inputs_and_manifest_remain_exact(run17: dict[str, Any]) -> None:
    _assert_exact_legacy_plan(run17)


def test_v4_changes_only_unit_system_guidance_and_contract(run17: dict[str, Any]) -> None:
    state, context = run17["state"], run17["context"]
    original = deepcopy(state)
    old = build_research_review_plan(state["candidate"], state["history"], **context,
                                     contract_id=STATISTICAL_REVIEW_PLAN_CONTRACT)
    new = build_research_review_plan(state["candidate"], state["history"], **context)
    assert new.contract_id == REVIEW_PLAN_CONTRACT == "idea.research_per_insight_then_whole.v4"
    assert old.candidate_sha256 == new.candidate_sha256
    for before, after in zip(old.units, new.units, strict=True):
        assert after.messages[0].content == before.messages[0].content + " " + RESEARCH_EVIDENCE_SCOPE_GUIDANCE
        assert after.messages[0].role == before.messages[0].role == "system"
        assert after.messages[1:] == before.messages[1:]
        assert after.response_schema == before.response_schema
        assert after.evidence_bindings == before.evidence_bindings
    assert digest(plan_payload(old)) != digest(plan_payload(new))
    assert state == original


def test_v4_cannot_resume_v3_and_unknown_review_still_cannot_replay(run17: dict[str, Any]) -> None:
    state = run17["state"]
    original = deepcopy(state)
    with pytest.raises(ValueError, match="original explicit contract"):
        validate_review_plan_resume(state, run17["trace"], contract_id=REVIEW_PLAN_CONTRACT)
    # Negative mutation of a real checkpoint exercises refusal, not a successful run.
    interrupted = deepcopy(state)
    interrupted["pending"] = "model"
    with pytest.raises(ValueError, match="outcome unknown"):
        validate_review_plan_resume(interrupted, run17["trace"], contract_id=STATISTICAL_REVIEW_PLAN_CONTRACT)
    config = LLMConfig(provider="deepseek", model="deepseek-v4-pro")
    policy = AgentLoopPolicy(mode="reflection", trace="full")
    factory = partial(build_research_review_plan, **run17["context"])
    old = review_plan_fingerprint("authored base identity", factory, STATISTICAL_REVIEW_PLAN_CONTRACT, config, policy)
    new = review_plan_fingerprint("authored base identity", factory, REVIEW_PLAN_CONTRACT, config, policy)
    assert old != new and state == original


def test_real_run17_author_assembly_changes_only_added_guidance(run17: dict[str, Any]) -> None:
    root, context = run17["root"], run17["context"]
    first = next(event["visible"] for event in run17["events"] if event["kind"] == "model_request")
    # Preserve the original wire argument order, while checking the archived request.
    args = json.loads(first[3]["content"].split("\n", 1)[1])
    assert args == run17["request"]["arguments"]
    failures = [json.loads((root / "idea/research_delegations/58ff4c98c3cc423bba8909ce746f8ec9/failure.json").read_text())]
    request = RunRequest("pimc", context["task"])
    pack = ContextPack("", context["project"], context["task"], upstream=context["supplied_context"])
    session = ResearchSession(request, pack, get_agent_config("idea_research"), ToolRegistry(), 3, failures=failures)
    profile = json.loads((root / "input/idea_runtime_profile.v1.json").read_text())
    policy = AgentLoopPolicy.from_mapping(profile["configuration"]["child"]["loop"])
    original = deepcopy((args, failures, pack, request))
    messages = session.author_messages(args, refs=args["context_refs"], minimum=run17["request"]["min_sources"], policy=policy)
    wire = [message.to_wire() for message in messages]
    assert wire[0]["content"] == first[0]["content"] + " " + RESEARCH_EVIDENCE_SCOPE_GUIDANCE
    assert wire[1:] == first[1:len(wire)]
    assert (args, failures, pack, request) == original
    assert not session.receipts and session.attempted == 0


def test_real_run17_whole_review_retains_every_other_message(run17: dict[str, Any]) -> None:
    state, context = run17["state"], run17["context"]
    original = deepcopy(state)
    recorded = next(event["visible"] for event in run17["events"]
                    if event["kind"] == "model_request" and event["request"] == 14)
    profile = json.loads((run17["root"] / "input/idea_runtime_profile.v1.json").read_text())
    policy = AgentLoopPolicy.from_mapping(profile["configuration"]["child"]["loop"])
    counts = {**state["counts"], "model_requests": 13}
    pinned = research_review_messages(task=context["task"], project=context["project"], gap=context["gap"],
                                      supplied_context=context["supplied_context"])
    pinned += [budget_message(policy, counts), reflection_instruction(RESEARCH_REVIEW_RUBRIC)]
    messages, manifest = pack_context(pinned, state["history"], "", state["candidate"],
        budget=policy.input_token_budget, observation_chars=policy.observation_chars,
        reviewing=True, required_review_tools=("search.fetch_sources",))
    wire = [message.to_wire() for message in messages]
    changed = [index for index, (new, old) in enumerate(zip(wire, recorded, strict=True)) if new != old]
    assert changed == [5]
    assert wire[5]["content"].replace(" " + RESEARCH_EVIDENCE_SCOPE_GUIDANCE, "", 1) == recorded[5]["content"]
    assert manifest["omitted_history"] == [] and manifest["compressed_history"] == []
    assert state == original


@pytest.fixture(scope="module")
def run18_prefix() -> Iterator[tuple[Path, list[dict[str, Any]]]]:
    configured = os.environ.get("MARS_TEST_RESEARCH_SCOPE_EVENTS")
    if not configured:
        pytest.skip("requires actual run18 event prefix; no model response is synthesized")
    path = Path(configured)
    prefix = b""
    events = []
    with path.open("rb") as stream:
        for line in stream:
            event = json.loads(line)
            prefix += line
            events.append(event)
            if event["event_seq"] == 89:
                break
    assert hashlib.sha256(prefix).hexdigest() == "06dfc405fa02b01a480d2bd72e08659b36e04f25f4521c23730b91be89d6ef46"
    yield path, events
    with path.open("rb") as stream:
        assert stream.read(len(prefix)) == prefix


def test_real_partial_coverage_and_original_semantic_miss_remain_visible(
    run18_prefix: tuple[Path, list[dict[str, Any]]],
) -> None:
    path, events = run18_prefix
    original = deepcopy(events)
    request = next(event for event in events if event["kind"] == "model_request" and event["request"] == 13)
    response = next(event for event in events if event["kind"] == "model_response" and event["request"] == 13)
    unit_input = request["visible"]
    payload = json.loads(unit_input[4]["content"].split("\n", 1)[1])
    rows = json.loads(unit_input[5]["content"].split("\n", 1)[1])
    reads = [row["source_row"] for row in rows if row["tool"] == "search.fetch_sources"]
    assert all(row["pdf_pages"] == 13 and row["full_document_read"] is False for row in reads)
    assert reads[-1]["extracted_pages"] == [4, 5, 6, 7, 8, 9]
    assert [page["page"] for page in reads[-1]["visible_pages"]] == [4, 5, 6, 7]
    last_page = reads[-1]["visible_pages"][-1]
    assert last_page["page"] == 7 and last_page["truncated"] is True
    assert len(last_page["text"]) == 614 < last_page["full_page_text_chars"] == 5397
    assert "没有给出" in payload["insight"]["limitations"][1]
    parsed = parse_insight_review(response["visible"], fields=insight_fields(payload["insight"]))
    check = next(item for item in parsed.details["checks"] if item["field"] == "limitations[1]")
    claim = check["claims"][1]
    assert claim["statement"].startswith("在可见页段中未提供") and claim["verdict"] == "hypothesis"
    assert parsed.decision["accept"] is True  # Observed old semantic miss; the parser does not rejudge it.

    record_path = path.parents[3] / "idea/research_delegations" / path.parent.name / "request.json"
    record_before = record_path.read_bytes()
    record = json.loads(record_before)
    candidate = parse_action(next(event["visible"] for event in events
        if event["kind"] == "model_response" and event["request"] == 11))["final"]
    history = [event["visible"] for event in events if event["kind"] == "observation"]
    plan = build_research_review_plan(candidate, history, **record["review_context"],
                                     gap=record["arguments"], min_sources=record["min_sources"])
    unit = next(unit for unit in plan.units if unit.unit_id == "I2")
    assert unit.messages[0].content == unit_input[0]["content"] + " " + RESEARCH_EVIDENCE_SCOPE_GUIDANCE
    assert [message.to_wire() for message in unit.messages[1:]] == unit_input[1:len(unit.messages)]
    assert "statement, assumptions or verification" in unit.messages[0].content
    assert "explicit author revision" in unit.messages[0].content
    assert "loose upper bound" in unit.messages[0].content
    assert events == original and record_path.read_bytes() == record_before


def test_actual_upper_bound_objection_is_not_rewritten_by_the_parser(
    run18_prefix: tuple[Path, list[dict[str, Any]]],
) -> None:
    _, events = run18_prefix
    response = next(event for event in events if event["kind"] == "model_response" and event["request"] == 10)
    candidate = parse_action(next(event["visible"] for event in events
        if event["kind"] == "model_response" and event["request"] == 9))["final"]
    insight = parse(candidate).metadata["insights"][0]
    parsed = parse_insight_review(response["visible"], fields=insight_fields(insight))
    assert parsed.details == json.loads(response["visible"])
    assert parsed.decision["accept"] is False and "上界" in parsed.decision["issues"][0]
    # New guidance is not a deterministic correction or a fresh model review.


@pytest.mark.asyncio
async def test_main_author_and_review_receive_the_same_scope_rule_once() -> None:
    agent = IdeaAgent()
    request = RunRequest("pimc", "Human-authored context assembly check, not a scientific result.", extra={
        "context_sources": {"project_rules": False, "code_repositories": False},
        "idea_requirements": {"require_research_dossier": True, "min_sources": 2}})
    original = deepcopy(request)
    context = await agent.build_context(request)
    assert context.task.count(RESEARCH_EVIDENCE_SCOPE_GUIDANCE) == 1
    review = agent.review_messages(request, context)
    assert sum(message.content.count(RESEARCH_EVIDENCE_SCOPE_GUIDANCE) for message in review) == 1
    assert any(context.task in message.content for message in review)
    assert request == original
