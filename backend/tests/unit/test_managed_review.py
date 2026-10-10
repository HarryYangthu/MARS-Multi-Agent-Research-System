"""Real files/configuration and pure contracts; no model or service substitutes."""
from pathlib import Path
import json
from functools import partial

import pytest
import yaml

from app.bridge.managed_review import parse_decision, review_once, review_state, revision_count, record_state, finish_review
from app.bridge.orchestrator import Orchestrator, RunRequest, RunSession
from app.bridge.workflow_service import build_standalone
from app.bridge.commander_session import CommanderSessionStore, ChatMessage
from app.harness.llm import model_registry
from app.harness.runtime.event_bus import InProcessEventBus
from app.storage.run_store import RunStore
from app.storage.run_state_store import RunStateStore


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
