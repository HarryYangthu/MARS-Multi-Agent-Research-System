"""Authored dialogue inputs and real local storage; no model/tool substitutes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.chat import _conv_view
from app.bridge.commander_session import ChatMessage, CommanderSession, CommanderSessionStore, ConversationActivity
from app.bridge.conversation_edit import MessageEditConflict, MessageEditRequest, finish_user_turn_edit, replace_user_turn
from app.harness.runtime.conversation_state import ConversationState


def dialogue() -> CommanderSession:
    return CommanderSession(conv_id="authored-dialogue", project="presentation-input", messages=[
        ChatMessage(role="user", id="a", turn_id="a", content="original first input", timestamp="2026-10-10T01:00:00+00:00"),
        ChatMessage(role="assistant", id="a-reply", turn_id="a", content="old first output", timestamp="2026-10-10T01:00:02+00:00"),
        ChatMessage(role="tool", id="a-tool", turn_id="a", content="old observation", timestamp="2026-10-10T01:00:03+00:00"),
        ChatMessage(role="user", id="b", turn_id="b", content="later input", timestamp="2026-10-10T02:00:00+00:00"),
        ChatMessage(role="assistant", id="b-reply", turn_id="b", content="later output", timestamp="2026-10-10T02:00:02+00:00"),
    ], activities=[
        ConversationActivity(id="old-progress", kind="model", title="old progress", status="completed", turn_id="a", timestamp="2026-10-10T01:00:01+00:00"),
        ConversationActivity(id="later-progress", kind="model", title="later progress", status="completed", turn_id="b", timestamp="2026-10-10T02:00:01+00:00"),
    ])


def request(session: CommanderSession, index: int = 0) -> MessageEditRequest:
    message = session.messages[index]
    return MessageEditRequest(message.id, message.content, message.timestamp)


def test_replace_middle_turn_excludes_old_answer_and_later_context() -> None:
    session = dialogue()
    later = [message.to_dict() for message in session.messages[3:]]
    retained = replace_user_turn(session, request(session), "modified first input")
    assert [message.content for message in session.context_messages()] == ["modified first input"]
    assert [message.to_dict() for message in session.messages[1:]] == later
    session.add(ChatMessage(role="assistant", content="authored new first output"))
    assert [message.content for message in session.context_messages()] == ["modified first input", "authored new first output"]
    assert session.messages[1].turn_id == "a"
    assert [activity.id for activity in session.activities] == ["later-progress"]
    progress = session.begin_activity("model", "authored edit progress")
    assert progress.turn_id == "a"
    finish_user_turn_edit(session, retained)
    assert [message.to_dict() for message in session.messages[2:]] == later
    assert session.edit_cursor is None
    assert session.messages[0].id == "a"
    assert "old first output" not in json.dumps(_conv_view(session).model_dump())


def test_latest_turn_preserves_prefix_and_uses_new_final_state() -> None:
    session = dialogue()
    prefix = [message.to_dict() for message in session.messages[:3]]
    retained = replace_user_turn(session, request(session, 3), "modified last input")
    session.state = ConversationState.CLARIFYING
    session.add(ChatMessage(role="assistant", content="authored last output"))
    finish_user_turn_edit(session, retained)
    assert [message.to_dict() for message in session.messages[:3]] == prefix
    assert session.state == ConversationState.CLARIFYING
    assert len(session.messages) == 5
    assert [activity.id for activity in session.activities] == ["old-progress"]


def test_middle_edit_preserves_later_session_controls() -> None:
    session = dialogue()
    session.state = ConversationState.AWAITING_REVIEW
    session.linked_run_id = "existing-real-run-reference"
    session.auto_mode = True
    session.metric_targets = {"target": 2.0}
    retained = replace_user_turn(session, request(session), "modified input")
    session.state = ConversationState.CLARIFYING
    session.linked_run_id = None
    session.auto_mode = False
    session.metric_targets = {}
    finish_user_turn_edit(session, retained)
    assert session.state == ConversationState.AWAITING_REVIEW
    assert session.linked_run_id == "existing-real-run-reference"
    assert session.auto_mode
    assert session.metric_targets == {"target": 2.0}


@pytest.mark.parametrize("kind", ["content", "timestamp", "missing", "assistant", "busy", "empty"])
def test_invalid_edit_leaves_all_history_and_controls_unchanged(kind: str) -> None:
    session = dialogue()
    edit = request(session)
    text = "modified input"
    if kind == "content": edit = MessageEditRequest(edit.message_id, "stale content", edit.expected_timestamp)
    if kind == "timestamp": edit = MessageEditRequest(edit.message_id, edit.expected_content, "stale timestamp")
    if kind == "missing": edit = MessageEditRequest("absent", edit.expected_content, edit.expected_timestamp)
    if kind == "assistant": edit = request(session, 1)
    if kind == "busy": session.processing = True
    if kind == "empty": text = " \n "
    before = _conv_view(session).model_dump()
    with pytest.raises(MessageEditConflict):
        replace_user_turn(session, edit, text)
    assert _conv_view(session).model_dump() == before
    assert session.edit_cursor is None


def test_legacy_progress_and_compaction_do_not_reintroduce_deleted_content() -> None:
    session = dialogue()
    for activity in session.activities: activity.turn_id = None
    session.rolling_summary = "legacy rollup containing old first output"
    session.context_compaction = {"old": "old first output"}
    retained = replace_user_turn(session, request(session), "modified input")
    assert [activity.id for activity in session.activities] == ["later-progress"]
    assert session.rolling_summary == ""
    assert session.context_compaction == {}
    finish_user_turn_edit(session, retained)


def test_replaced_turn_survives_reload_without_old_versions(tmp_path: Path) -> None:
    session = dialogue()
    store = CommanderSessionStore(tmp_path)
    retained = replace_user_turn(session, request(session), "modified input")
    session.add(ChatMessage(role="assistant", content="authored new output"))
    finish_user_turn_edit(session, retained)
    store.persist(session)
    loaded = CommanderSessionStore(tmp_path).get(session.conv_id)
    assert loaded is not None
    assert [message.to_dict() for message in loaded.messages] == [message.to_dict() for message in session.messages]
    content = (tmp_path / session.conv_id / "messages.jsonl").read_text()
    assert "old first output" not in content and "old observation" not in content
    assert "old-progress" not in (tmp_path / session.conv_id / "session.json").read_text()


def test_restart_during_edit_retains_other_turns_and_never_restores_old_reply(tmp_path: Path) -> None:
    session = dialogue()
    replace_user_turn(session, request(session), "modified input")
    session.processing = True
    session.begin_activity("model", "authored unfinished progress")
    CommanderSessionStore(tmp_path).persist(session)
    loaded = CommanderSessionStore(tmp_path).get(session.conv_id)
    assert loaded is not None
    assert not loaded.processing and loaded.edit_cursor is None
    assert [message.content for message in loaded.messages] == ["modified input", "later input", "later output"]
    assert loaded.activities[-1].status == "interrupted"


def test_legacy_ids_are_stable_across_readers_and_turns_are_derived(tmp_path: Path) -> None:
    session = dialogue()
    store = CommanderSessionStore(tmp_path)
    store.persist(session)
    path = tmp_path / session.conv_id / "messages.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for row in rows:
        row.pop("id"); row.pop("turn_id")
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    first = CommanderSessionStore(tmp_path).get(session.conv_id)
    second = CommanderSessionStore(tmp_path).get(session.conv_id)
    assert first is not None and second is not None
    assert [message.id for message in first.messages] == [message.id for message in second.messages]
    assert first.messages[1].turn_id == first.messages[0].id
    assert first.messages[4].turn_id == first.messages[3].id
