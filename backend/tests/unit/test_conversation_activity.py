"""Public milestone lifecycle and recovery using real local persistence."""
from pathlib import Path

import pytest

from app.api.chat import _conv_view
from app.bridge import commander_session
from app.bridge.commander_session import ChatMessage, CommanderSession, CommanderSessionStore


def test_progress_is_separate_from_model_context() -> None:
    session = CommanderSession(conv_id="test", project="project")
    session.add(ChatMessage(role="user", content="user request"))
    operation = session.begin_activity("model", "调用模型")
    session.processing = True
    view = _conv_view(session)
    assert view.processing
    assert view.activities[0]["status"] == "running"
    assert [item.content for item in session.context_messages()] == ["user request"]
    session.finish_activity(operation)
    assert _conv_view(session).activities[0]["ended_at"]
    assert operation.status == "completed"


def test_recovery_keeps_completed_progress_and_interrupts_unfinished_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only the storage path is configured; no model/tool/service is replaced.
    monkeypatch.setattr(commander_session, "repo_root", lambda: tmp_path)
    store = CommanderSessionStore()
    session = store.create(project="project")
    done = session.begin_activity("tool", "读取资料")
    session.finish_activity(done)
    pending = session.begin_activity("model", "调用模型")
    session.processing = True
    store.persist(session)
    loaded = CommanderSessionStore().get(session.conv_id)
    assert loaded is not None
    assert not loaded.processing
    assert loaded.activities[0].id == done.id
    assert loaded.activities[0].status == "completed"
    assert loaded.activities[1].id == pending.id
    assert loaded.activities[1].status == "interrupted"
    assert loaded.context_messages() == []


def test_failure_is_not_presented_as_success() -> None:
    session = CommanderSession(conv_id="test", project="project")
    operation = session.begin_activity("tool", "执行工具")
    session.finish_activity(operation, "failed")
    session.interrupt_activities()
    assert _conv_view(session).activities[0]["status"] == "failed"


def test_public_run_activity_excludes_reasoning_and_raw_model_content() -> None:
    from app.bridge.research_activity import public_activity_event
    record = {"event_id": "position:1", "timestamp": "now", "kind": "model_response",
              "source": {"component": "agent_loop", "agent": "experiment"},
              "payload": {"model": "configured-model", "visible": {"reasoning_content": "private"},
                          "content": "private", "api_key": "private"}}
    projected = public_activity_event(record)
    assert projected is not None
    assert projected["payload"] == {"model": "configured-model"}
    assert "private" not in str(projected)
    assert public_activity_event({**record, "kind": "reflection"}) is None
