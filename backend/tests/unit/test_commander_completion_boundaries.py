"""Pure controller boundaries; these inputs do not replace any model or tool."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.bridge.commander import (
    Decision, _begin_turn_completion, _completion_fingerprint, _decision_goal, _parse_decision,
    _prevent_duplicate_creation, _receipt_ends_action_batch, _receipt_question, _store_turn_completion,
)
from app.bridge.commander_completion import CompletionVerdict, goal_state, parse_turn_goal
from app.bridge.commander_session import ChatMessage, CommanderSession


def _session() -> CommanderSession:
    session = CommanderSession('boundaries', 'actual-project')
    user = ChatMessage(role='user', content='换个学习率，原配置与新配置各跑完整两轮。')
    session.active_turn_id = user.id
    session.add(user)
    return session


def test_optional_goal_format_never_rejects_valid_action() -> None:
    for metadata in ('null', '[]', '"description"', '{"extra":"free text"}'):
        decision = _parse_decision('{"actions":[{"tool":"run.start","args":{}}],"goal":' + metadata + '}')
        assert decision.actions == [{'tool': 'run.start', 'args': {}}]


def test_repeated_identical_results_are_not_progress() -> None:
    session = _session()
    session.add(ChatMessage(role='tool', content='read', tool_name='code.repo_read_lines',
        tool_args={'path': 'config.yaml'}, tool_result={'ok': True, 'output': {'sha256': 'same'},
                                                       'duration_ms': 1, 'request_id': 'one'}))
    before = _completion_fingerprint(session, None)
    session.add(ChatMessage(role='tool', content='read again', tool_name='code.repo_read_lines',
        tool_args={'path': 'config.yaml'}, tool_result={'ok': True, 'output': {'sha256': 'same'},
                                                       'duration_ms': 200, 'request_id': 'two'}))
    assert _completion_fingerprint(session, None) == before
    session.add(ChatMessage(role='tool', content='new read', tool_name='code.repo_read_lines',
        tool_args={'path': 'config.yaml'}, tool_result={'ok': True, 'output': {'sha256': 'changed'}}))
    assert _completion_fingerprint(session, None) != before


def test_same_batch_exact_mutations_are_deduplicated_and_create_start_remains() -> None:
    session = _session()
    create = {'tool': 'run.create', 'args': {'entrypoint': 'coding', 'user_request': 'requested'}}
    start = {'tool': 'run.start', 'args': {}}
    result = _prevent_duplicate_creation(session, Decision(actions=[create, dict(create), start, dict(start)]))
    assert result.actions == [create, start]
    assert not _receipt_ends_action_batch('run.create', 2)
    assert _receipt_ends_action_batch('create_and_start_run', 2)


def test_allocated_task_filters_recreation_but_preserves_its_start() -> None:
    session = _session()
    session.linked_run_id = 'allocated'
    # A reducer receipt input, not a substituted successful tool execution.
    session.add(ChatMessage(role='tool', content='allocated', tool_name='run.create',
        tool_args={'entrypoint': 'coding'}, tool_result={'ok': True, 'run_id': 'allocated'}))
    start = {'tool': 'run.start', 'args': {}}
    assert _prevent_duplicate_creation(session, Decision(actions=[
        {'tool': 'run.create', 'args': {'entrypoint': 'coding', 'slug': 'another'}}, start])).actions == [start]
    session.add(ChatMessage(role='tool', content='started', tool_name='run.start',
        tool_args={}, tool_result={'ok': True, 'run_id': 'allocated'}))
    assert _prevent_duplicate_creation(session, Decision(actions=[start])).actions == []


def test_unknown_interpretations_preserve_original_pending_source_and_initial_link() -> None:
    session = _session()
    goal = parse_turn_goal({'intent': 'action', 'run_target': 'new', 'required_effects': ['run_started']}, session)
    assert goal is not None
    old = goal_state(goal)
    session.completion_state = {'goal': old, 'status': 'pending'}
    session.linked_run_id = 'old-run'
    session.completion_state = _begin_turn_completion(session)
    assert session.completion_state['previous_goal'] == old
    session.linked_run_id = 'new-run'
    unknown = replace(goal, intent='unknown', required_effects=())
    _store_turn_completion(session, unknown, CompletionVerdict(status='unknown', reason='unresolved'))
    assert session.completion_state['initial_linked_run_id'] == 'old-run'
    assert session.completion_state['previous_goal'] == old
    session.completion_state = _begin_turn_completion(session)
    assert session.completion_state['previous_goal'] == old
    _store_turn_completion(session, None, CompletionVerdict(status='unknown', reason='optional metadata missing'))
    assert session.completion_state['previous_goal'] == old


def test_real_choice_reducer_yields_a_specific_question_instead_of_a_promise() -> None:
    session = _session()
    receipt = session.add(ChatMessage(role='tool', content='choice', tool_name='run.existing_model',
        tool_result={'ok': False, 'choices': [{'config_path': 'configs/a.yaml'}, {'config_path': 'configs/b.yaml'}]}))
    question = _receipt_question(session, CompletionVerdict(status='needs_user', reason='choice', evidence_ids=(receipt.id,)))
    assert 'configs/a.yaml' in question and 'configs/b.yaml' in question and '请确认' in question


def test_legacy_approval_cannot_be_queued_or_replayed_twice() -> None:
    session = _session()
    action: dict[str, Any] = {'tool': 'approve_node', 'args': {'agent': 'coding'}}
    assert _prevent_duplicate_creation(session, Decision(actions=[action, action])).actions == [action]
    session.add(ChatMessage(role='tool', content='approval reducer input', tool_name='approve_node',
        tool_args=action['args'], tool_result={'ok': True}))
    assert _prevent_duplicate_creation(session, Decision(actions=[action])).actions == []


def test_creation_alone_cannot_shorten_an_undeclared_user_goal_to_creation() -> None:
    session = _session()
    create = {'tool': 'run.create', 'args': {'entrypoint': 'coding'}}
    assert _decision_goal(session, Decision(actions=[create])) is None
    launch = _decision_goal(session, Decision(actions=[create, {'tool': 'run.start', 'args': {}}]))
    assert launch is not None and launch.required_effects == ('run_created', 'run_started')
    declared = _decision_goal(session, Decision(actions=[create], goal={
        'intent': 'action', 'required_effects': ['run_created'], 'run_target': 'new'}))
    assert declared is not None and declared.required_effects == ('run_created',)
