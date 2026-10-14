"""Real files/configuration and pure contracts; no model or service substitutes."""
from pathlib import Path
import json
from functools import partial
from dataclasses import replace

import pytest
import yaml

from app.bridge.managed_review import parse_decision, review_once, review_state, revision_count, record_state, finish_review
from app.bridge.managed_review import ReviewPolicy, review_input_manifest, review_policy
from app.harness.agent_loop.trace import digest
from app.harness.llm.provider_base import Message
from app.bridge.orchestrator import Orchestrator, RunRequest, RunSession
from app.bridge.workflow_service import build_standalone
from app.bridge.commander_session import CommanderSessionStore, ChatMessage
from app.harness.llm import model_registry
from app.harness.runtime.event_bus import InProcessEventBus


def test_execution_review_carries_exact_approvals_and_separates_budget_units(tmp_path: Path) -> None:
    import hashlib
    from app.bridge.managed_review import execution_review_payload
    run = RunStore(tmp_path).create(project='synthetic_regression', task='approved input review')
    contents = {'experiment/experiment_plan.approved.md': 'Human-authored plan: 100 epochs.\n',
                'coding/code_spec.approved.md': 'Human-authored approved command binding.\n'}
    for ref, content in contents.items():
        (run.root / ref).write_bytes(content.encode("utf-8"))
    configuration = {'confirmed': False, 'jobs': [], 'blockers': [],
        'budget': {'unit': 'model_requests', 'used': 92, 'limit': 100, 'required': 0},
        'experiments': [{'name': 'candidate', 'config': {'budget_unit': 'epochs', 'max_iters': 100}}]}
    payload = execution_review_payload(run, goal='Approved protocol', configuration=configuration)
    assert payload['configuration'] == configuration
    assert payload['review_contract']['confirmed_false_means'] == 'awaiting_this_decision'
    assert payload['review_contract']['empty_jobs_means'] == 'no_simulations_launched'
    assert payload['review_contract']['model_budget_unit'] == 'model_requests'
    assert set(payload['evidence_refs']) == {'execution_configuration', *contents}
    for item in payload['approved_artifacts']:
        assert item['content'] == contents[item['ref']]
        assert item['sha256'] == hashlib.sha256(contents[item['ref']].encode()).hexdigest()
    assert not (run.root / 'execution/confirmations').exists()


@pytest.mark.asyncio
async def test_execution_review_identity_tracks_approvals_not_live_usage(tmp_path: Path) -> None:
    from app.bridge.managed_review import execution_review_payload
    run = RunStore(tmp_path).create(project='synthetic_regression', task='upstream approval identity')
    source = run.root / 'coding/code_spec.approved.md'
    source.write_text('Authored approval version one.\n')
    for used in (92, 93):
        configuration = {'token': 'same-host-confirmation', 'inputs_token': 'same-host-inputs',
            'blockers': ['Host admission blocked'], 'budget': {'unit': 'model_requests', 'used': used}}
        payload = execution_review_payload(run, goal='Inspect approved inputs', configuration=configuration)
        assert await review_once(run=run, node='execution', kind='execution_configuration', payload=payload,
            generation=1, eligible=lambda: True, bus=InProcessEventBus(), blocker='Host admission blocked') is None
    assert len(list((run.root / 'hitl/managed_reviews').glob('*.json'))) == 1
    source.write_text('Authored approval version two.\n')
    changed = execution_review_payload(run, goal='Inspect approved inputs', configuration=configuration)
    assert await review_once(run=run, node='execution', kind='execution_configuration', payload=changed,
        generation=1, eligible=lambda: True, bus=InProcessEventBus(), blocker='Host admission blocked') is None
    assert len(list((run.root / 'hitl/managed_reviews').glob('*.json'))) == 2
    assert not list((run.root / 'agent_traces/commander').glob('*/events.jsonl'))
from app.storage.run_store import RunStore
from app.storage.run_state_store import RunStateStore


def test_complete_review_over_legacy_character_limit_fits_configured_context() -> None:
    policy = review_policy().model_copy(update={"input_token_budget": 128000})
    agent = replace(model_registry.get_agent_config("commander"), max_tokens=393216,
                    raw={"loop": {"input_token_budget": 128000}, "model": {"context_window": 1048576}})
    messages = [Message("system", "Review the entire authored input."), Message("user", "a" * 61000)]
    manifest = review_input_manifest(messages, policy, agent, {"input_budget": 128000, "safety_margin": 1000})
    assert manifest["within_budget"] and not manifest["truncated"]
    assert manifest["input_token_budget"] == 128000
    assert manifest["complete_input_sha256"] == digest([m.to_wire() for m in messages])
    assert len(messages[-1].content) == 61000


def test_review_counts_multibyte_input_and_keeps_all_evidence_when_too_large() -> None:
    policy = review_policy().model_copy(update={"input_token_budget": 128000})
    agent = replace(model_registry.get_agent_config("commander"), max_tokens=393216,
                    raw={"loop": {}, "model": {"context_window": 1048576}})
    messages = [Message("user", "证" * 50000)]
    manifest = review_input_manifest(messages, policy, agent, {"input_budget": 128000, "safety_margin": 1000})
    assert not manifest["within_budget"] and not manifest["truncated"]
    assert manifest["estimated_upper_bound_tokens"] > 150000
    assert messages[0].content == "证" * 50000


@pytest.mark.parametrize("loop_budget,window,expected", [(64000, 1048576, 64000), (128000, 420000, 25784)])
def test_review_respects_smaller_role_or_model_input_space(loop_budget: int, window: int, expected: int) -> None:
    policy = review_policy().model_copy(update={"input_token_budget": 128000, "max_output_tokens": 393216})
    agent = replace(model_registry.get_agent_config("commander"), max_tokens=393216,
                    raw={"loop": {"input_token_budget": loop_budget}, "model": {"context_window": window}})
    manifest = review_input_manifest([Message("user", "Authored review")], policy, agent,
                                     {"input_budget": 128000, "safety_margin": 1000})
    assert manifest["input_token_budget"] == expected


def test_legacy_review_policy_migrates_to_common_input_budget() -> None:
    raw = {**review_policy().model_dump(), "input_token_budget": None, "max_input_characters": 60000}
    policy = ReviewPolicy.model_validate(raw)
    assert "max_input_characters" not in policy.model_dump()
    assert "max_input_characters" not in ReviewPolicy.model_json_schema()["properties"]
    assert raw["max_input_characters"] == 60000
    agent = replace(model_registry.get_agent_config("commander"), raw={"loop": {}, "model": {}})
    manifest = review_input_manifest([Message("user", "a" * 61000)], policy, agent,
                                     {"input_budget": 128000, "safety_margin": 1000})
    assert manifest["within_budget"] and manifest["input_token_budget"] == 128000


def test_review_parser_requires_bound_evidence() -> None:
    decision = parse_decision('{"decision":"approve","reason":"Checked the supplied document","evidence_refs":["candidate.md"]}', ["candidate.md"])
    assert decision.decision == "approve"


@pytest.mark.parametrize("text", [
    '{"decision":"approve","reason":"Unchecked","evidence_refs":["invented.md"]}',
    '{"decision":"approve","decision":"needs_user","reason":"Duplicate","evidence_refs":["candidate.md"]}',
    '{"decision":"approve","reason":" ","evidence_refs":["candidate.md"]}',
    '{"decision":"approve","reason":"No evidence","evidence_refs":[]}',
    '{"decision":"approve","reason":"Wrong type","evidence_refs":"candidate.md"}',
    '{"decision":"approve","reason":"Unknown field","evidence_refs":["candidate.md"],"action":"start"}',
    '{"decision":"approve"}',
])
def test_invalid_decisions_never_become_approval(text: str) -> None:
    with pytest.raises(ValueError):
        parse_decision(text, ["candidate.md"])


def saved_run(tmp_path: Path) -> tuple[Orchestrator, RunSession]:
    runs = RunStore(tmp_path / "runs")
    run = runs.create(project="synthetic_regression", task="real review preference persistence", entrypoint="idea")
    orchestrator = Orchestrator(run_store=runs)
    session = RunSession(run, build_standalone("idea"), RunRequest(task=run.task, project=run.project, entrypoint="idea"), InProcessEventBus())
    orchestrator._sessions[run.run_id] = session
    orchestrator._persist_state(session, status="created")
    return orchestrator, session


def test_toggle_persists_reviewer_without_starting_pending_research(tmp_path: Path) -> None:
    orchestrator, session = saved_run(tmp_path)
    run_id = session.run.run_id
    assert session.request.review_mode == "manual"
    orchestrator.set_review_mode(run_id, project=session.run.project, enabled=True)
    snapshot = RunStateStore(session.run).load()
    assert snapshot and snapshot.request["review_mode"] == "commander"
    assert snapshot.request["auto_approve"] is False
    assert orchestrator.owned_tasks.active(run_id) is None
    orchestrator.discard_session(run_id)
    recovered = orchestrator.session(run_id)
    assert recovered.request.review_mode == "commander"
    orchestrator.set_review_mode(run_id, project=session.run.project, enabled=False)
    assert orchestrator.session(run_id).request.review_mode == "manual"
    assert recovered.request.review_generation == 2


def test_scope_failure_cannot_modify_mode(tmp_path: Path) -> None:
    orchestrator, session = saved_run(tmp_path)
    with pytest.raises(ValueError):
        orchestrator.set_review_mode(session.run.run_id, project="another-project", enabled=True)
    assert session.request.review_mode == "manual"


def test_new_conversation_preference_survives_reload_without_starting_a_run(tmp_path: Path) -> None:
    sessions = CommanderSessionStore(tmp_path)
    session = sessions.create(project="synthetic_regression")
    assert session.auto_mode is False
    session.add(ChatMessage(role="user", content="Authored persistence input; no Agent dispatch."))
    session.auto_mode = True
    sessions.persist(session)
    loaded = CommanderSessionStore(tmp_path).get(session.conv_id)
    assert loaded and loaded.auto_mode is True and loaded.linked_run_id is None
    assert [message.content for message in loaded.messages] == [message.content for message in session.messages]


@pytest.mark.asyncio
async def test_host_block_keeps_review_pending_without_dispatching_model(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(project="synthetic_regression", task="host evidence rejection")
    attempt = partial(review_once, run=run, node="idea", kind="artifact", payload={"evidence_refs": ["candidate.md"]},
                generation=1, eligible=lambda: True, bus=InProcessEventBus(), blocker="Missing evaluation reports")
    assert await attempt() is None
    state = review_state(run)
    assert state and state["status"] == "needs_user"
    assert not list((run.root / "agent_traces/commander").glob("*/events.jsonl"))
    size = (run.root / "events/agent_events.jsonl").stat().st_size
    assert await attempt() is None
    assert (run.root / "events/agent_events.jsonl").stat().st_size == size
    assert revision_count(run, "idea") == 0
    assert not (run.root / "idea/idea_proposal.approved.md").exists()


@pytest.mark.asyncio
async def test_disabled_review_cannot_send_a_model_request(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(project="synthetic_regression", task="disabled reviewer")
    assert await review_once(run=run, node="idea", kind="artifact", payload={}, generation=1,
                             eligible=lambda: False, bus=InProcessEventBus()) is None
    assert review_state(run) is None


@pytest.mark.asyncio
async def test_live_usage_counters_do_not_repeat_the_same_configuration_review(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(project="synthetic_regression", task="stable configuration identity")
    for used in (1, 2, 3):
        assert await review_once(run=run, node="execution", kind="execution_configuration",
            payload={"goal": "Real config review identity", "configuration": {"token": "authored-identity",
                     "inputs_token": "authored-input-identity", "budget": {"used": used}, "blockers": ["Missing executable"]}},
            generation=1, eligible=lambda: True, bus=InProcessEventBus(), blocker="Missing executable") is None
    assert len(list((run.root / "hitl/managed_reviews").glob("*.json"))) == 1
    assert len((run.root / "events/agent_events.jsonl").read_text().splitlines()) == 1


@pytest.mark.asyncio
async def test_parallel_review_decisions_update_their_own_receipts(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(project="synthetic_regression", task="independent review receipts")
    bus = InProcessEventBus()
    for identity, node in [("a" * 64, "idea"), ("b" * 64, "experiment")]:
        await record_state(run, bus, {"identity": identity, "node": node, "status": "decision_ready"})
    await finish_review(run, bus, "approved", "Checked authored receipt", identity="a" * 64)
    first = json.loads((run.root / "hitl/managed_reviews" / ("a" * 64 + ".json")).read_text())
    second = json.loads((run.root / "hitl/managed_reviews" / ("b" * 64 + ".json")).read_text())
    assert first["status"] == "approved" and first["node"] == "idea"
    assert second["status"] == "decision_ready" and second["node"] == "experiment"


@pytest.mark.asyncio
async def test_actual_refused_endpoint_stops_once_without_approving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs = tmp_path / "model_config/configs"
    configs.mkdir(parents=True)
    (configs / "agents.yaml").write_text(yaml.safe_dump({"commander": {
        "model": {"provider": "local_vllm", "model": "unavailable-local-review", "base_url": "http://127.0.0.1:1/v1"},
        "generation": {"max_tokens": 256}, "request_timeout_seconds": 2, "max_retries": 0}}))
    (configs / "models.yaml").write_text("providers: {}\n")
    # Substitute only the location of real temporary configuration files.
    monkeypatch.setattr(model_registry, "repo_root", lambda: configs.parent)
    model_registry.reset_cache_for_tests()
    try:
        run = RunStore(tmp_path / "runs").create(project="synthetic_regression", task="real connection refusal")
        attempt = partial(review_once, run=run, node="idea", kind="artifact", payload={"candidate": "Authored network-test input", "evidence_refs": ["candidate.md"]},
                    generation=1, eligible=lambda: True, bus=InProcessEventBus())
        assert await attempt() is None
        assert (review_state(run) or {})["status"] == "needs_user"
        traces = list((run.root / "agent_traces/commander").glob("*/events.jsonl"))
        assert len(traces) == 1
        rows = [json.loads(line) for line in traces[0].read_text().splitlines()]
        assert any(row["kind"] == "model_error" for row in rows)
        assert not any(row["kind"] == "model_response" for row in rows)
        assert await attempt() is None
        assert len(list((run.root / "agent_traces/commander").glob("*/events.jsonl"))) == 1
        assert not (run.root / "idea/idea_proposal.approved.md").exists()
    finally:
        model_registry.reset_cache_for_tests()
