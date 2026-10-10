"""Replace exactly one user turn; later messages are neither deleted nor replayed."""
from __future__ import annotations

from dataclasses import dataclass

from app.bridge.commander_session import ChatMessage, CommanderSession
from app.harness.runtime.conversation_state import ConversationState


class MessageEditConflict(ValueError):
    """A stale or invalid edit must leave the conversation untouched."""


@dataclass(frozen=True)
class MessageEditRequest:
    message_id: str
    expected_content: str
    expected_timestamp: str


@dataclass(frozen=True)
class EditedTurn:
    has_later_turns: bool
    state: ConversationState
    linked_run_id: str | None
    auto_mode: bool
    metric_targets: dict[str, float]


def replace_user_turn(session: CommanderSession, request: MessageEditRequest, text: str) -> EditedTurn:
    """Validate first, then discard this turn's old answer and public progress."""
    if session.processing:
        raise MessageEditConflict("当前对话正在处理，请完成后再编辑。")
    if not text.strip():
        raise MessageEditConflict("消息内容不能为空。")
    index = next((i for i, message in enumerate(session.messages) if message.id == request.message_id), None)
    if index is None:
        raise MessageEditConflict("消息已变化，请重新读取对话后再编辑。")
    original = session.messages[index]
    if original.role != "user" or original.content != request.expected_content or original.timestamp != request.expected_timestamp:
        raise MessageEditConflict("消息已变化，请重新读取对话后再编辑。")
    end = next((i for i in range(index + 1, len(session.messages)) if session.messages[i].role == "user"), len(session.messages))
    boundary = session.messages[end].timestamp if end < len(session.messages) else None
    turn_id = original.turn_id or original.id
    retained = EditedTurn(end < len(session.messages), session.state, session.linked_run_id, session.auto_mode, dict(session.metric_targets))
    replacement = ChatMessage(role="user", content=text.strip(), id=original.id, turn_id=turn_id, state=original.state)
    session.messages[index:end] = [replacement]
    session.activities = [activity for activity in session.activities if not (
        activity.turn_id == turn_id or (activity.turn_id is None and activity.timestamp >= original.timestamp
        and (boundary is None or activity.timestamp < boundary))
    )]
    session.edit_cursor = index + 1
    session.active_turn_id = turn_id
    session.rolling_summary = ""  # A legacy rollup may contain the discarded answer.
    session.summary_updated_at = None
    session.context_compaction = {}
    session.updated_at = replacement.timestamp
    return retained


def finish_user_turn_edit(session: CommanderSession, retained: EditedTurn) -> None:
    session.edit_cursor = None
    if retained.has_later_turns:
        session.state = retained.state
        session.linked_run_id = retained.linked_run_id
        session.auto_mode = retained.auto_mode
        session.metric_targets = retained.metric_targets
