"""Pure read-boundary/payload checks; no model or tool success substitutes."""
from __future__ import annotations

from dataclasses import replace
import json
from typing import Any

import pytest

from app.bridge.commander import _completion_prompt
from app.bridge.commander_answers import evidence_answer_messages, observation_read_pending, read_answer_ready, repeated_read_request
from app.bridge.commander_completion import CompletionVerdict, goal_state, parse_turn_goal
from app.bridge.commander_native import INTERVENTION, native_commander_config, native_tool_names
from app.bridge.commander_session import ChatMessage, CommanderSession
from app.bridge.commander_tools import commander_tool_is_read_only
from app.harness.llm.provider_base import LLMConfig


def _session() -> CommanderSession:
    session = CommanderSession('read-boundary', 'actual-project')
    user = ChatMessage(role='user', content='目前最佳性能是哪个模型')
    session.active_turn_id = user.id
    session.add(user)
    return session


def test_repeat_boundary_is_scoped_to_current_successful_registered_read() -> None:
    session = _session()
    action: dict[str, Any] = {'tool': 'knowledge.research_memory', 'args': {'query': 'existing results'}}
    receipt = ChatMessage(role='tool', content='reducer input', tool_name=action['tool'],
        tool_args=action['args'], tool_result={'ok': True, 'output': {'hits': []}})
    session.add(receipt)
    assert repeated_read_request(session, [action], None)
    receipt.tool_result = {'ok': False, 'error': 'unavailable'}
    assert not repeated_read_request(session, [action], None)
    receipt.tool_result = {'ok': True, 'output': {'hits': []}}
    receipt.turn_id = 'previous-turn'
    assert not repeated_read_request(session, [action], None)


def test_read_boundary_never_silences_outstanding_mutations_or_new_reads() -> None:
    session = _session()
    read = {'tool': 'run.status', 'args': {}}
    session.add(ChatMessage(role='tool', content='reducer input', tool_name='run.status',
        tool_args={}, tool_result={'ok': True, 'states': {}}))
    goal = parse_turn_goal({'intent': 'action', 'required_effects': ['run_started'], 'run_target': 'new'}, session)
    assert goal is not None and not repeated_read_request(session, [read], goal)
    for actions in ([], [read, {'tool': 'run.start', 'args': {}}],
                    [read, {'tool': 'artifact.read', 'args': {'agent': 'writing'}}],
                    [{'tool': 'invented.read', 'args': {}}], [{'tool': 'run.status', 'args': []}]):
        assert not repeated_read_request(session, actions, None)


def test_delivery_requires_interpreted_read_intent_and_reduced_observation() -> None:
    session = _session()
    goal = parse_turn_goal({'intent': 'observe', 'required_effects': ['observed'], 'run_target': 'none'}, session)
    assert goal is not None
    verdict = CompletionVerdict(status='pending', reason='observation_answer_not_established',
                                missing=('semantic_answer',))
    assert read_answer_ready(goal, verdict)
    assert '只缺向用户交付答复' in _completion_prompt(session, goal, verdict)
    assert not read_answer_ready(replace(goal, intent='action'), verdict)
    assert not read_answer_ready(None, verdict)
    for changed in (replace(verdict, status='blocked'), replace(verdict, status='unknown'),
                    replace(verdict, missing=('observed',)), replace(verdict, missing=('run_started',))):
        assert not read_answer_ready(goal, changed)


def test_answer_payload_keeps_current_question_receipt_identity_and_comparison_limits() -> None:
    session = _session()
    result = {'ok': True, 'output': {'hits': [{'id': 'record-one', 'metrics': {'RES': 7.46},
        'comparison': {'compatible': False, 'missing': ['evaluation']},
        'annotations': {'limitations': 'different budget'}}]}}
    old = session.add(ChatMessage(role='tool', content='reducer input', tool_name='knowledge.research_memory',
        tool_args={'query': 'results'}, tool_result={**result, 'request_id': 'old', 'duration_ms': 10}))
    new = session.add(ChatMessage(role='tool', content='reducer input', tool_name='knowledge.research_memory',
        tool_args={'query': 'results'}, tool_result={**result, 'request_id': 'new', 'duration_ms': 100}))
    session.add(ChatMessage(role='tool', content='old-turn-secret', tool_name='knowledge.research_memory',
        tool_result={'ok': True, 'output': {'title': 'old-turn-secret'}}, turn_id='other'))
    session.add(ChatMessage(role='tool', content='not an observation', tool_name='run.start',
        tool_result={'ok': True, 'run_id': 'not-read-evidence'}))
    messages = evidence_answer_messages(session)
    payload = '\n'.join(message.content for message in messages)
    assert old.id not in payload and new.id in payload
    assert 'old-turn-secret' not in payload and 'not-read-evidence' not in payload
    assert '"compatible": false' in payload and 'different budget' in payload
    assert '"RES": 7.46' in payload and 'record-one' in payload
    assert json.loads(messages[-1].content)['current_user']['content'] == '目前最佳性能是哪个模型'
    assert all(message.role in {'system', 'user'} and not message.tool_calls for message in messages)
    assert old.tool_result == {**result, 'request_id': 'old', 'duration_ms': 10}


def test_answer_payload_never_fabricates_a_missing_current_user() -> None:
    with pytest.raises(ValueError, match='bound current user'):
        evidence_answer_messages(CommanderSession('empty', 'project'))


def test_missing_observation_requests_real_read_tools_without_new_mutation_permission() -> None:
    session = _session()
    goal = parse_turn_goal({'intent': 'observe', 'required_effects': ['observed'], 'run_target': 'none'}, session)
    assert goal is not None
    session.completion_state = {'goal': goal_state(goal), 'status': 'pending', 'missing': ['observed']}
    assert observation_read_pending(session)
    config = native_commander_config(LLMConfig(provider='zhipu', model='glm-5.3'), 20, session)
    names = native_tool_names()
    advertised = {tool['function']['name'] for tool in config.tools}
    assert INTERVENTION in advertised
    assert 'mars_knowledge_research_memory' in advertised
    assert all(name == INTERVENTION or commander_tool_is_read_only(names[name]) for name in advertised)
    assert 'mars_run_start' not in advertised and 'mars_artifact_review' not in advertised
    assert '先调用' in _completion_prompt(session, goal, CompletionVerdict('pending', '', missing=('observed',)))
    session.completion_state['missing'] = ['semantic_answer']
    assert not observation_read_pending(session)
    session.completion_state['missing'] = ['observed']
    session.messages[0].content = 'Change the goal'
    assert not observation_read_pending(session)
