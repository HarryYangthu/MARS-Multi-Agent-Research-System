"""Continuation selection over real session records; no provider or tool substitutes."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.bridge.commander_session import ChatMessage, CommanderSession, CommanderSessionStore
from app.bridge.conversation_continue import continuation_message, conversation_continuation


def blocked_question() -> CommanderSession:
    session = CommanderSession(conv_id='conv_continue', project='project_a', linked_run_id='finished_run')
    old = session.add(ChatMessage(role='user', content='降低模型参数量'))
    latest = session.add(ChatMessage(role='user', content='最终模型是什么'))
    session.add(ChatMessage(role='assistant', content='模型接口限流或额度不足。'))
    session.completion_state = {'turn_id': latest.id, 'status': 'blocked', 'goal': None,
                                'previous_goal': {'source_turn_id': old.id, 'intent': 'action'},
                                'previous_status': 'fulfilled'}
    return session


def test_latest_unanswered_question_takes_precedence_over_fulfilled_research() -> None:
    session = blocked_question()
    view = conversation_continuation(session)
    assert view['enabled']
    assert continuation_message(session, source_message_id=view['source_message_id'], token=view['token']) == '最终模型是什么'
    assert len(session.messages) == 3
    assert session.linked_run_id == 'finished_run'


def test_reload_retains_button_and_snapshot(tmp_path: Path) -> None:
    store = CommanderSessionStore(tmp_path)
    session = store.create(project='project_a')
    user = session.add(ChatMessage(role='user', content='继续核对原任务'))
    session.add(ChatMessage(role='assistant', content='模型调用超时'))
    session.completion_state = {'turn_id': user.id, 'status': 'blocked'}
    store.persist(session)
    restored = CommanderSessionStore(tmp_path).get(session.conv_id)
    assert restored is not None
    assert conversation_continuation(restored) == conversation_continuation(session)


def test_server_interrupted_model_call_can_continue_after_reload(tmp_path: Path) -> None:
    store = CommanderSessionStore(tmp_path)
    session = store.create(project='project_a')
    user = session.add(ChatMessage(role='user', content='解释刚才的结果'))
    session.active_turn_id = user.id
    session.processing = True
    session.begin_activity('model', '总控正在调用模型')
    session.completion_state = {'turn_id': user.id, 'status': 'unknown'}
    store.persist(session)
    restored = CommanderSessionStore(tmp_path).get(session.conv_id)
    assert restored is not None and not restored.processing
    assert conversation_continuation(restored)['enabled']


def test_unknown_completion_without_an_interrupted_call_is_not_replayed() -> None:
    session = blocked_question()
    session.completion_state['status'] = 'unknown'
    assert not conversation_continuation(session)['enabled']


@pytest.mark.parametrize('status', ['fulfilled', 'needs_user', 'answer_sufficient', ''])
def test_completed_or_user_decision_requests_are_not_replayed(status: str) -> None:
    session = blocked_question()
    session.completion_state['status'] = status
    assert not conversation_continuation(session)['enabled']


def test_active_request_cannot_be_started_again() -> None:
    session = blocked_question()
    view = conversation_continuation(session)
    session.processing = True
    assert not conversation_continuation(session)['enabled']
    with pytest.raises(ValueError, match='状态已变化'):
        continuation_message(session, source_message_id=view['source_message_id'], token=view['token'])


@pytest.mark.parametrize('change', ['message', 'model', 'project', 'run', 'completion'])
def test_old_button_snapshot_cannot_repeat_or_redirect_a_request(change: str) -> None:
    session = blocked_question()
    view = conversation_continuation(session)
    if change == 'message':
        user = session.add(ChatMessage(role='user', content='最终模型是什么'))
        session.completion_state['turn_id'] = user.id
    elif change == 'model':
        session.selected_model_id = 'another_model'
    elif change == 'project':
        session.project = 'another_project'
    elif change == 'run':
        session.linked_run_id = 'another_run'
    else:
        session.completion_state['status'] = 'fulfilled'
    with pytest.raises(ValueError, match='状态已变化'):
        continuation_message(session, source_message_id=view['source_message_id'], token=view['token'])


def test_newer_human_message_does_not_inherit_old_failure() -> None:
    session = blocked_question()
    session.add(ChatMessage(role='user', content='先不要继续'))
    assert not conversation_continuation(session)['enabled']


def test_exact_original_content_is_used_without_wrapping_or_dropping_constraints() -> None:
    session = blocked_question()
    latest = next(message for message in reversed(session.messages) if message.role == 'user')
    latest.content = '训练2个epoch；seed=2026。\n保留现有数据，不改评价。'
    view = conversation_continuation(session)
    assert continuation_message(session, source_message_id=latest.id, token=view['token']) == latest.content


def test_empty_conversation_has_no_continuation() -> None:
    assert not conversation_continuation(CommanderSession(conv_id='empty', project='project_a'))['enabled']
