"""Pure native argument parsing and the actual provider wire builder."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from app.bridge.commander_native import (
    STEP, INTERVENTION, commander_step_tool, commander_native_tools, native_tool_names,
    commander_tool_input_schema, native_commander_config, native_decision_unavailable,
    observation_only_messages, parse_native_commander_completion,
    pack_native_commander_messages,
)
from app.bridge.commander_session import ChatMessage, CommanderSession
from app.harness.agent_loop.trace import digest
from app.harness.context.runtime_manifest import record_usage
from app.bridge.commander_tools import TOOLS
from app.harness.llm.openai_provider import _OpenAICompatProvider
from app.harness.llm.provider_base import Completion, LLMConfig, Message, ToolCall
from app.harness.tools.config import load_tool_configs


def completed(raw: dict[str, Any]) -> Completion:
    # A data container for pure parsing, never a model/provider/tool substitute.
    return Completion(text='Text is not the executable protocol', provider='wire', model='parser',
        tool_calls=(ToolCall('real-call-shape', STEP, json.dumps(raw)),))


def test_function_exposes_exact_current_tool_names_and_never_execution_handlers() -> None:
    tool = commander_step_tool()
    assert tool['function']['name'] == STEP
    names = tool['function']['parameters']['properties']['actions']['items']['properties']['tool']['enum']
    assert names == sorted(TOOLS)
    assert 'handler' not in json.dumps(tool)


def test_native_arguments_are_exact_and_text_json_is_never_dispatched() -> None:
    raw = {'status': 'action', 'actions': [{'tool': 'run.status', 'args': {'run_id': 'existing-id'}}]}
    result = parse_native_commander_completion(completed(raw))
    assert result['actions'] == raw['actions'] and result['native_status'] == 'action'
    text = Completion(text=json.dumps(raw), provider='wire', model='parser')
    assert parse_native_commander_completion(text) == {
        'actions': [], 'reply': '', 'native_status': 'unknown', 'reason': 'native_tool_call_missing'}


def test_plain_answer_is_available_only_when_explicitly_enabled() -> None:
    completion = Completion(text='这是当前指标的含义。', provider='wire', model='parser')
    assert parse_native_commander_completion(completion)['native_status'] == 'unknown'
    assert parse_native_commander_completion(completion, accept_text_reply=True) == {
        'actions': [], 'reply': completion.text, 'native_status': 'text'}


def test_enabled_text_reply_never_extracts_json_actions() -> None:
    completion = Completion(text=json.dumps({'actions': [{'tool': 'run.start', 'args': {'run_id': 'r'}}]}),
                            provider='wire', model='parser')
    result = parse_native_commander_completion(completion, accept_text_reply=True)
    assert result == {'actions': [], 'reply': completion.text, 'native_status': 'text'}


def test_text_permission_does_not_bypass_native_argument_validation() -> None:
    with pytest.raises(ValidationError):
        parse_native_commander_completion(completed({'status': 'action', 'actions': [
            {'tool': 'invented.start', 'args': {}}]}), accept_text_reply=True)


@pytest.mark.parametrize('raw', [
    {'status': 'action', 'actions': [{'tool': 'invented.start', 'args': {}}]},
    {'status': 'action', 'actions': [{'tool': 'run.start', 'args': 'missing-object'}]},
    {'status': 'action', 'actions': [{'tool': 'run.start', 'args': None, 'note': 'not an argument'}]},
    {'status': 'blocked', 'actions': [{'tool': 'run.status', 'args': {'run_id': 'r'}}], 'reason': 'blocked'},
    {'status': 'action', 'actions': [{'tool': 'run.status', 'args': {'run_id': 'r'}}], 'question': 'Which? '},
])
def test_invalid_or_ambiguous_native_args_are_rejected_as_a_whole(raw: dict[str, Any]) -> None:
    with pytest.raises((ValueError, ValidationError)):
        parse_native_commander_completion(completed(raw))


@pytest.mark.parametrize('arguments', ['{"actions":[],"actions":[],"status":"action"}',
                                      '{"status":"action","actions":[],"extra":NaN}'])
def test_duplicate_keys_and_nonfinite_values_are_not_repaired(arguments: str) -> None:
    value = Completion('', 'wire', 'parser', tool_calls=(ToolCall('id', STEP, arguments),))
    with pytest.raises(ValueError):
        parse_native_commander_completion(value)


def test_parallel_decisions_and_wrong_native_function_are_not_replayed() -> None:
    call = ToolCall('id', STEP, '{"status":"action","actions":[]}')
    for calls in ((call, call), (ToolCall('id', 'another-function', '{}'),)):
        with pytest.raises(ValueError):
            parse_native_commander_completion(Completion('', 'wire', 'parser', tool_calls=calls))


def test_explanatory_wrapper_fields_are_ignored_and_nonempty_actions_imply_omitted_status() -> None:
    args = {'run_id': 'exact-existing-run'}
    raw: dict[str, Any] = {'explanation': 'transport note only', 'actions': [{'tool': 'run.status', 'args': args,
           'reason': 'actual observation needed', 'description': {'note': 'metadata'}}]}
    result = parse_native_commander_completion(completed(raw))
    assert result['native_status'] == 'action'
    assert result['actions'] == [{'tool': 'run.status', 'args': args}]
    assert 'status' not in raw and raw['actions'][0]['args'] == args


@pytest.mark.parametrize('error', [ValueError('format'), ValidationError('bad args'), TimeoutError('deadline')])
def test_transport_failure_is_unknown_without_actions_or_success_claim(error: Exception) -> None:
    assert native_decision_unavailable(error) == {'actions': [], 'reply': '', 'native_status': 'unknown',
        'reason': 'native_decision_unavailable:' + type(error).__name__}


@pytest.mark.parametrize('raw,status,reply', [
    ({'status': 'action', 'actions': [], 'reply': '我现在启动'}, 'unknown', ''),
    ({'status': 'question', 'actions': [], 'question': '请选择已存在的模型。'}, 'question', '请选择已存在的模型。'),
    ({'status': 'blocked', 'actions': [], 'reason': '真实读取观察明确显示配置不存在。'}, 'blocked', '真实读取观察明确显示配置不存在。'),
    ({'status': 'blocked', 'actions': [], 'reply': '我会继续'}, 'unknown', ''),
])
def test_empty_action_requires_explicit_intervention(raw: dict[str, Any], status: str, reply: str) -> None:
    result = parse_native_commander_completion(completed(raw))
    assert result['native_status'] == status and result['reply'] == reply


def test_actual_glm_wire_builder_keeps_thinking_and_uses_observation_only_history() -> None:
    original = LLMConfig(provider='zhipu', model='glm-5.3', thinking_enabled=True,
                         reasoning_effort='low', max_tokens=8192, json_mode=True, extra={'context_window': 100000})
    historical = [Message('system', 'Original system'), Message('user', 'Original user'),
                  Message('assistant', 'I will start', (ToolCall('old', 'run.start', '{"run_id":"r"}'),)),
                  Message('tool', '{"ok":false}', tool_call_id='old'), Message('user', '继续')]
    messages = observation_only_messages(historical)
    assert messages[0] is historical[0] and messages[1] is historical[1] and messages[-2] is historical[-1]
    assert all(m.role not in {'assistant', 'tool'} and not m.tool_calls and m.tool_call_id is None for m in messages)
    assert 'not a verified execution receipt' in messages[2].content
    assert json.loads(messages[3].content.split('\n', 1)[1])['content'] == '{"ok":false}'
    config = native_commander_config(original, 20)
    provider = _OpenAICompatProvider(api_key='unused-pure-wire-builder', base_url='http://127.0.0.1:1/v1', provider_name='zhipu')
    wire = provider._request_kwargs(messages, config)
    assert {tool['function']['name'] for tool in wire['tools']} == set(native_tool_names()) | {INTERVENTION}
    assert STEP not in {tool['function']['name'] for tool in wire['tools']}
    assert wire['parallel_tool_calls'] is False
    assert wire['extra_body']['thinking']['type'] == 'enabled'
    assert config.thinking_enabled is True and config.reasoning_effort == 'low'
    assert config.extra['native_observation_history'] is True and config.request_timeout_seconds == 20
    assert config.max_retries == 0 and not config.json_mode and config.response_schema is None
    assert original.extra == {'context_window': 100000} and provider._client is None


def test_native_context_records_actual_payload_tools_state_and_usage(tmp_path: Path) -> None:
    session = CommanderSession('conv-native-context', 'fixture-project')
    messages = [Message('system', 'Rules'), Message('assistant', 'Historical answer'),
                Message('user', '[observation tool=code.repo_read_lines] {"ok":true,"content":"source"}'),
                Message('user', 'Explain the result')]
    config = native_commander_config(LLMConfig(provider='zhipu', model='glm-5.3',
                                      thinking_enabled=True, max_tokens=2048), 20)
    wire, manifest_path = pack_native_commander_messages(session, messages, config, tmp_path)
    assert manifest_path is not None
    manifest = json.loads(manifest_path.read_text())
    payload = {'messages': [message.to_wire() for message in wire], 'tools': config.tools}
    assert manifest['diagnostics']['payload_sha256'] == digest(payload)
    assert manifest['diagnostics']['runtime_context']['payload_sha256'] == digest(payload)
    assert manifest['diagnostics']['runtime_context']['tool_schema_upper_bound_tokens'] > 0
    assert manifest['project'] == session.project
    assert any(segment['source'] == 'commander completed reply' for segment in manifest['segments'])
    assert json.loads((tmp_path / 'context/commander_state.json').read_text()) == session.context_compaction
    assert (tmp_path / 'context/runtime_policy.v3.json').exists()
    assert all(message.role not in {'assistant', 'tool'} and not message.tool_calls for message in wire)
    assert sum('普通问答可以直接自然语言答复' in message.content for message in wire) == 1
    assert not any(message.content.startswith('本轮用户操作目标尚未完成') for message in wire)
    record_usage(manifest_path, {'prompt_tokens': 123, 'completion_tokens': 10})
    assert json.loads(manifest_path.read_text())['diagnostics']['provider_usage']['prompt_tokens'] == 123


def test_legacy_native_history_keeps_legacy_context_policy_unmodified(tmp_path: Path) -> None:
    session = CommanderSession('conv-native-legacy', 'fixture-project', context_version=2)
    wire, manifest_path = pack_native_commander_messages(session,
        [Message('assistant', 'Prior claim'), Message('user', 'Question')],
        native_commander_config(LLMConfig(provider='zhipu', model='glm-5.3', thinking_enabled=True), 20), tmp_path)
    assert manifest_path is None and not (tmp_path / 'context').exists()
    assert all(message.role in {'system', 'user'} for message in wire)


def test_actual_native_function_identity_needs_no_nested_action_tool_field() -> None:
    names = native_tool_names()
    assert names['mars_run_create'] == 'run.create' and names['mars_code_repo_read_lines'] == 'code.repo_read_lines'
    args = {'entrypoint': 'coding', 'user_request': 'Keep the exact current user request',
            'repository_scope': 'required'}
    result = parse_native_commander_completion(Completion('', 'wire', 'parser',
        tool_calls=(ToolCall('actual-call', 'mars_run_create', json.dumps(args)),)))
    assert result == {'actions': [{'tool': 'run.create', 'args': args}], 'reply': '', 'native_status': 'action'}
    advertised = {tool['function']['name']: tool['function'] for tool in commander_native_tools()}
    schema = advertised['mars_run_create']['parameters']
    assert 'actions' not in schema['properties'] and 'tool' not in schema['properties']
    assert 'user_request' in schema['properties'] and STEP not in advertised


@pytest.mark.parametrize('arguments', ['[]', 'null', '{"seed":1,"seed":2}', '{"seed":NaN}'])
def test_direct_native_arguments_reject_ambiguous_or_nonobject_inputs(arguments: str) -> None:
    with pytest.raises(ValueError):
        parse_native_commander_completion(Completion('', 'wire', 'parser',
            tool_calls=(ToolCall('actual-call', 'mars_run_create', arguments),)))


def test_direct_native_name_never_guesses_operation_from_args() -> None:
    args = {'tool': 'run.create', 'args': {'entrypoint': 'coding'}}
    result = parse_native_commander_completion(Completion('', 'wire', 'parser',
        tool_calls=(ToolCall('actual-call', 'mars_run_status', json.dumps(args)),)))
    assert result['actions'] == [{'tool': 'run.status', 'args': args}]
    with pytest.raises(ValueError):
        parse_native_commander_completion(Completion('', 'wire', 'parser',
            tool_calls=(ToolCall('actual-call', 'mars_invented_create', json.dumps(args)),)))
    for status, text in [('question', '请选择哪个真实候选？'), ('blocked', '当前存在真实审核阻点')]:
        field = 'question' if status == 'question' else 'reason'
        parsed = parse_native_commander_completion(Completion('', 'wire', 'parser', tool_calls=(
            ToolCall('actual-call', INTERVENTION, json.dumps({'status': status, field: text})),)))
        assert parsed['actions'] == [] and parsed['reply'] == text and parsed['native_status'] == status


def test_actual_native_batch_preserves_order_but_rejects_unknown_or_control_conflicts() -> None:
    first = ToolCall('create', 'mars_run_create', '{"entrypoint":"coding"}')
    second = ToolCall('start', 'mars_run_start', '{}')
    parsed = parse_native_commander_completion(Completion('', 'wire', 'parser', tool_calls=(first, second)))
    assert parsed['actions'] == [{'tool': 'run.create', 'args': {'entrypoint': 'coding'}},
                                 {'tool': 'run.start', 'args': {}}]
    for other in (ToolCall('unknown', 'mars_invented_create', '{}'),
                  ToolCall('question', INTERVENTION, '{"status":"question","question":"Which?"}'),
                  ToolCall('bad', 'mars_run_start', '[]'), first):
        with pytest.raises(ValueError):
            parse_native_commander_completion(Completion('', 'wire', 'parser', tool_calls=(first, other)))


def test_actual_commander_message_builder_has_one_native_transport_without_editing_user_content() -> None:
    from app.bridge.commander import Commander
    session = CommanderSession('native-prompt-builder', 'native-prompt-test-unbound', context_version=2)
    user_text = 'Explain this literal string: the decision object (reply / next_state / actions). Do not run anything.'
    user = ChatMessage(role='user', content=user_text)
    session.active_turn_id = user.id
    session.add(user)
    # Exercise the real pure message builder without resolving/creating a provider.
    commander = Commander.__new__(Commander)
    messages = commander._build_messages(session, continuation='An authorized operation is still pending.')
    assert any(message.role == 'user' and message.content == user_text for message in messages)
    assert '给出完整操作对象' not in messages[0].content
    assert '工具操作交接示例：{"actions"' not in messages[0].content
    assert '直接调用当前 mars_ 原生工具函数' in messages[0].content
    assert 'call its native function directly' in messages[-1].content
    wire = observation_only_messages(messages)
    assert all(message.role in {'system', 'user'} for message in wire)
    assert any(message.content == user_text for message in wire)


def test_advertised_read_schemas_are_exact_current_execution_contracts_and_copied() -> None:
    configured = load_tool_configs()
    for name in ('code.repo_list', 'code.repo_search', 'code.repo_read_lines', 'context.read_material'):
        schema = commander_tool_input_schema(name, configured)
        actual = configured[name].input_schema
        assert actual is not None and schema == actual and schema is not actual
        schema['properties']['invalid_added_field'] = {'type': 'string'}
        assert 'invalid_added_field' not in actual['properties']


@pytest.mark.parametrize('name,args', [
    ('code.repo_search', {'query': 'value', 'limit': '10'}),
    ('code.repo_list', {'depth': '2'}),
    ('code.repo_read_lines', {'path': 'configs/example.yaml', 'start_line': '1'}),
    ('create_and_start_run', {'entrypoint': 'coding', 'repository_scope': 'default'}),
    ('run.create', {'entrypoint': 'invented_stage'}),
    ('run.existing_model', {'config_path': 'configs/example.yaml', 'max_iters': '2', 'budget_unit': 'epochs'}),
    ('run.existing_model', {'config_path': 'configs/example.yaml', 'max_iters': 2, 'budget_unit': 'epochs', 'seed': -1}),
    ('run.existing_model', {'config_path': 'configs/example.yaml', 'max_iters': 2, 'budget_unit': 'epochs', 'start': 'true'}),
    ('run.recover', {'action': 'invented_recovery'}),
])
def test_real_invalid_argument_shapes_are_rejected_by_exported_metadata_without_execution(
        name: str, args: dict[str, Any]) -> None:
    schema = commander_tool_input_schema(name, load_tool_configs())
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(args)


def test_all_actual_commander_tools_have_typed_input_metadata_and_aliases_agree() -> None:
    configured = load_tool_configs()
    advertised = commander_native_tools()
    assert len(advertised) == len(TOOLS) + 1
    for name in TOOLS:
        schema = commander_tool_input_schema(name, configured)
        Draft202012Validator.check_schema(schema)
        assert schema.get('type') == 'object'
        for field in schema.get('properties', {}).values():
            assert any(key in field for key in ('type', '$ref', 'anyOf', 'oneOf', 'allOf'))
    for aliases in [('run.create', 'create_and_start_run'), ('run.status', 'get_run_status'),
                    ('diagnosis.failure_analysis', 'get_diagnosis'), ('artifact.review', 'user.approval')]:
        assert commander_tool_input_schema(aliases[0], configured) == commander_tool_input_schema(aliases[1], configured)
    create = commander_tool_input_schema('run.create', configured)
    assert create['properties']['repository_scope']['default'] == 'required'
    assert 'user_request' not in create.get('required', [])  # Actual handler retains its empty-string default.
    Draft202012Validator(create).validate({'entrypoint': 'coding', 'repository_scope': 'required'})
    existing = commander_tool_input_schema('run.existing_model', configured)
    Draft202012Validator(existing).validate({'config_path': 'configs/example.yaml', 'max_iters': 2, 'budget_unit': 'epochs'})
    assert 'seed' not in existing.get('required', []) and 'default' not in existing['properties']['seed']


def test_discovery_spec_metadata_preserves_pydantic_definitions_and_open_payload_boundaries() -> None:
    from app.bridge.discovery_types import DiscoveryRunSpec
    schema = commander_tool_input_schema('discovery.create', load_tool_configs())
    actual = DiscoveryRunSpec.model_json_schema()
    assert schema['$defs'] == actual.pop('$defs')
    assert schema['properties']['spec'] == actual
    # Resolves real nested $refs; this is input-shape validation, never a run.
    errors = list(Draft202012Validator(schema).iter_errors({'spec': {'task': 'shape-only'}, 'idempotency_key': 'key'}))
    assert errors and all(error.validator != '$ref' for error in errors)
    overrides = commander_tool_input_schema('run.existing_model', load_tool_configs())['properties']['parameter_overrides']
    assert overrides == {'type': 'object'}
