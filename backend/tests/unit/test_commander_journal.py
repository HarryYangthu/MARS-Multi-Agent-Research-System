"""Real durable writes and pure no-replay decisions, no substitute tool execution."""
from pathlib import Path
import json

from app.bridge.commander_journal import begin_action, complete_action, restore_action_receipts, unresolved_write, repeated_failure
from app.bridge.commander_session import ChatMessage, CommanderSession
from app.bridge.commander_native import native_commander_config, pack_native_commander_messages
from app.bridge.commander_native import template_options_rejected
from app.harness.llm.provider_base import LLMConfig, Message


def test_receipt_survives_gap_before_session_save_without_replaying_actual_write(tmp_path: Path) -> None:
    session = CommanderSession('durable', 'project')
    user = session.add(ChatMessage(role='user', content='write this file'))
    session.active_turn_id = user.id
    args = {'path': str(tmp_path / 'actual-result.txt')}
    identity = begin_action(tmp_path, session, 'file.write', args, mutation=True)
    assert unresolved_write(tmp_path, 'file.write', args)
    target = Path(args['path'])
    target.write_text('actual file content')
    result = {'ok': target.exists(), 'bytes': target.stat().st_size}
    complete_action(tmp_path, identity, result)
    stamp = target.stat().st_mtime_ns
    restored = CommanderSession('durable', 'project', messages=[user])
    assert restore_action_receipts(tmp_path, restored) == 1
    assert restore_action_receipts(tmp_path, restored) == 0
    assert restored.messages[-1].tool_result == result
    assert target.stat().st_mtime_ns == stamp and not unresolved_write(tmp_path, 'file.write', args)


def test_interrupted_mutation_never_becomes_a_success_receipt(tmp_path: Path) -> None:
    session = CommanderSession('interrupted', 'project')
    begin_action(tmp_path, session, 'file.write', {'path': 'unknown'}, mutation=True)
    assert restore_action_receipts(tmp_path, session) == 0
    assert unresolved_write(tmp_path, 'file.write', {'path': 'unknown'})
    assert not unresolved_write(tmp_path, 'file.read', {'path': 'unknown'})
    raw = json.loads((tmp_path / 'action_journal.json').read_text())
    assert 'result' not in raw['entries'][0]


def test_edited_user_request_never_restores_a_previous_execution_receipt(tmp_path: Path) -> None:
    session = CommanderSession('edit', 'project')
    user = session.add(ChatMessage(role='user', content='original request'))
    session.active_turn_id = user.id
    identity = begin_action(tmp_path, session, 'file.write', {}, mutation=True)
    complete_action(tmp_path, identity, {'ok': False, 'error': 'actual operation never executed'})
    user.content = 'different request'
    assert restore_action_receipts(tmp_path, session) == 0


def test_implicit_start_is_scoped_to_its_actual_run(tmp_path: Path) -> None:
    session = CommanderSession('scope', 'project', linked_run_id='run-one')
    begin_action(tmp_path, session, 'run.start', {}, mutation=True)
    assert unresolved_write(tmp_path, 'run.start', {}, session)
    session.linked_run_id = 'run-two'
    assert not unresolved_write(tmp_path, 'run.start', {}, session)


def test_failed_action_requires_changed_arguments_or_new_evidence() -> None:
    session = CommanderSession('failure', 'project', active_turn_id='turn')
    args = {'max_iters': 2, 'budget_unit': 'steps'}
    session.add(ChatMessage(role='tool', tool_name='run.existing_model', tool_args=args,
        tool_result={'ok': False, 'retryable': False, 'error': 'budget mismatch'}, content='validation input'))
    assert repeated_failure(session, 'run.existing_model', args)
    assert not repeated_failure(session, 'run.existing_model', {**args, 'budget_unit': 'epochs'})


def test_native_catalog_does_not_repeat_aliases_or_eagerly_load_discovery() -> None:
    base = LLMConfig(provider='custom', model='actual-id')
    full = native_commander_config(base, 30)
    packed = native_commander_config(base, 30, CommanderSession('tools', 'project'))
    names = {row['function']['name'] for row in packed.tools}
    assert 'mars_run_existing_model' in names and 'mars_tools_describe' in names
    assert 'mars_discovery_create' not in names and 'mars_create_and_start_run' not in names
    assert len(json.dumps(packed.tools)) < len(json.dumps(full.tools)) * .8


def test_minimal_gateway_retry_is_only_for_a_named_unsupported_template_option() -> None:
    assert template_options_rejected(400, {'error': {'message': 'Unknown parameter: chat_template_kwargs'}})
    assert template_options_rejected(422, {'error': {'param': 'chat_template_kwargs.enable_thinking'}})
    assert not template_options_rejected(429, {'error': {'message': 'Unknown parameter: chat_template_kwargs'}})
    assert not template_options_rejected(400, {'error': {'message': 'Invalid model identifier'}})
    assert not template_options_rejected(400, {'error': {'message': 'Thinking exceeds timeout'}})


def test_explicit_working_set_is_separate_from_advertised_model_window(tmp_path: Path) -> None:
    config = LLMConfig(provider='custom', model='actual-id', max_tokens=100,
        extra={'context_window': 200000, 'working_context_tokens': 12000, 'commander_text_transport': True})
    _, path = pack_native_commander_messages(CommanderSession('context', 'project'),
        [Message('user', 'Actual user request')], config, tmp_path)
    assert path is not None
    assert json.loads(path.read_text())['budget']['max'] == 12000
