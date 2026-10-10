from __future__ import annotations

from pathlib import Path

import pytest

from app.bridge.commander_session import ChatMessage, CommanderSessionStore
from app.bridge.run_conversation import open_run_conversation
from app.bridge.experiment_service import _load, require_experiment
from app.harness.runtime.conversation_state import ConversationState
from app.storage.run_store import RunStore


def test_existing_run_opens_idempotently_and_survives_reload(tmp_path: Path) -> None:
    runs = RunStore(tmp_path / "runs")
    run = runs.create(project="research", task="literature", entrypoint="idea", user_request="Read relevant papers.")
    root = tmp_path / "conversations"
    sessions = CommanderSessionStore(root)
    before = {str(path.relative_to(run.root)): path.read_bytes() for path in run.root.rglob("*") if path.is_file()}
    conversation = open_run_conversation(run.run_id, project=run.project, sessions=sessions, runs=runs)
    assert conversation.linked_run_id == run.run_id
    assert conversation.auto_mode is False
    assert conversation.processing is False
    assert len(conversation.messages) == 1
    assert conversation.messages[0].role == "system"
    assert "Read relevant papers." in conversation.messages[0].content
    assert "没有重新启动、批准或重试" in conversation.messages[0].content
    reopened = open_run_conversation(run.run_id, project=run.project, sessions=CommanderSessionStore(root), runs=runs)
    assert reopened.conv_id == conversation.conv_id
    assert len(reopened.messages) == 1
    assert len(list(root.iterdir())) == 1
    after = {str(path.relative_to(run.root)): path.read_bytes() for path in run.root.rglob("*") if path.is_file()}
    assert before == after
    assert len(runs.list()) == 1


def test_existing_live_conversation_is_reused_without_altering_history(tmp_path: Path) -> None:
    runs = RunStore(tmp_path / "runs")
    run = runs.create(project="research", task="existing")
    sessions = CommanderSessionStore(tmp_path / "conversations")
    session = sessions.create(project=run.project)
    session.linked_run_id = run.run_id
    session.state = ConversationState.AWAITING_REVIEW
    session.auto_mode = True
    session.add(ChatMessage(role="user", content="Original request"))
    sessions.persist(session)
    session.processing = True
    actual = open_run_conversation(run.run_id, project=run.project, sessions=sessions, runs=runs)
    assert actual is session
    assert actual.processing is True
    assert actual.auto_mode is True
    assert actual.state == ConversationState.AWAITING_REVIEW
    assert [message.content for message in actual.messages] == ["Original request"]


def test_project_mismatch_and_missing_run_do_not_create_conversations(tmp_path: Path) -> None:
    runs = RunStore(tmp_path / "runs")
    run = runs.create(project="a", task="private")
    sessions = CommanderSessionStore(tmp_path / "conversations")
    for run_id, project in [(run.run_id, "b"), ("missing", "a")]:
        with pytest.raises(FileNotFoundError):
            open_run_conversation(run_id, project=project, sessions=sessions, runs=runs)
    with pytest.raises(ValueError):
        open_run_conversation("../private", project="a", sessions=sessions, runs=runs)
    assert sessions.list() == []


def test_experiment_scope_is_derived_and_validated(tmp_path: Path) -> None:
    runs = RunStore(tmp_path / "runs")
    run = runs.create(project="research", task="scoped", experiment_id="experiment-a")
    sessions = CommanderSessionStore(tmp_path / "conversations")
    with pytest.raises(ValueError, match="实验不匹配"):
        open_run_conversation(run.run_id, project=run.project, experiment_id="experiment-b", sessions=sessions, runs=runs)
    assert sessions.list() == []
    actual = open_run_conversation(run.run_id, project=run.project, sessions=sessions, runs=runs)
    assert actual.experiment_id == "experiment-a"
    assert open_run_conversation(run.run_id, project=run.project, experiment_id="experiment-a", sessions=sessions, runs=runs).conv_id == actual.conv_id


def test_existing_experiment_metadata_is_read_and_invalid_schema_rejected(tmp_path: Path) -> None:
    path = tmp_path / "experiment.yaml"
    path.write_text("schema: experiment.v1\nid: recorded\nproject: research\n", encoding="utf-8")
    assert _load(path)["project"] == "research"
    path.write_text("schema: invalid\n", encoding="utf-8")
    with pytest.raises(ValueError, match="无效"):
        _load(path)


def test_unscoped_conversation_requires_no_experiment_record() -> None:
    assert require_experiment("research", "") == {}
