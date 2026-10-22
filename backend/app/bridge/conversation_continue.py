"""Bind a continuation to the latest unfinished human request, without inference."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.bridge.commander_session import CommanderSession


def conversation_continuation(session: CommanderSession) -> dict[str, Any]:
    result: dict[str, Any] = {'enabled': False, 'source_message_id': '', 'token': '',
                              'message': '当前没有未完成的对话请求。'}
    if session.processing:
        return {**result, 'message': '当前请求正在处理，无需重复继续。'}
    source = next((message for message in reversed(session.messages) if message.role == 'user'), None)
    completion = session.completion_state
    interrupted = source is not None and any(activity.turn_id == source.id and activity.status == 'interrupted'
                                             for activity in session.activities)
    unfinished = completion.get('status') in {'blocked', 'pending'} or (
        completion.get('status') == 'unknown' and interrupted)
    if (source is None or not source.content.strip()
            or not unfinished
            or completion.get('turn_id') != source.id):
        return result
    # Include the last message and current model/owner so a stale tab cannot
    # replay an old request after a new turn, model change or run handoff.
    identity = {'conversation': session.conv_id, 'project': session.project,
                'run_id': session.linked_run_id, 'model': session.selected_model_id,
                'source': {'id': source.id, 'content': source.content, 'timestamp': source.timestamp},
                'completion': completion, 'last_message': {
                    'id': session.messages[-1].id, 'content': session.messages[-1].content,
                    'timestamp': session.messages[-1].timestamp}, 'count': len(session.messages)}
    token = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {**result, 'enabled': True, 'source_message_id': source.id, 'token': token,
            'message': '继续上一条尚未完成的请求，保留对话和已有结果。'}


def continuation_message(session: CommanderSession, *, source_message_id: str, token: str) -> str:
    view = conversation_continuation(session)
    if not view['enabled'] or view['source_message_id'] != source_message_id or view['token'] != token:
        raise ValueError('对话状态已变化，请核对最新进度后继续。')
    # Re-submit the exact latest question, not a bare "continue" that could be
    # interpreted as restarting a previously fulfilled research goal.
    return next(message.content for message in reversed(session.messages)
                if message.role == 'user' and message.id == source_message_id)
