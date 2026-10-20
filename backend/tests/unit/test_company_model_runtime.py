"""Pure wire/configuration contracts and real refused transport; no model doubles."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import socket
from typing import Any

from openai import APIConnectionError
import pytest
import yaml

from app.harness.llm.model_registry import configured_runtime_options, get_agent_config, select_provider
from app.harness.llm.openai_provider import CustomEndpointProvider, LocalVllmProvider
from app.harness.llm.provider_base import LLMConfig, LLMCompletionError, Message
from app.bridge.model_connection_test import ConnectionTestRequest, ProbePolicy, matching_probe_runtime
from app.bridge.commander_compat import parse_text_decision

ROOT = Path(__file__).resolve().parents[3]
spec = spec_from_file_location('company_profile', ROOT / 'deploy/windows-native/configure_slow_model.py')
assert spec and spec.loader
profile = module_from_spec(spec)
spec.loader.exec_module(profile)


@pytest.mark.parametrize('model,capacity,profile_name', [
    ('company/Qwen3.8-27B-InternalAlias', 200000, 'company_qwen_200k'),
    ('gemma-4-35b-it', 160000, 'company_gemma_160k'),
])
def test_company_profile_keeps_exact_model_endpoint_secret_and_unrelated_routes(
    tmp_path: Path, model: str, capacity: int, profile_name: str,
) -> None:
    config = tmp_path / 'configs'
    config.mkdir()
    selected = {'provider': 'custom', 'model': model, 'base_url': 'http://company.invalid/v1',
                'api_key_env': 'INTERNAL_TOKEN', 'thinking': {'enabled': True}, 'reasoning_effort': 'high',
                'max_tokens': 8192}
    agents = {'commander': {'model': selected}, 'coding': {'model': dict(selected), 'loop': {
        'reflection_thinking_enabled': True, 'reflection_reasoning_effort': 'high'}},
        'idea': {'model': {'provider': 'custom', 'model': 'separate-research-model', 'context_window': 64000}}}
    (config / 'agents.yaml').write_text(yaml.safe_dump(agents))
    (config / 'models.yaml').write_text('providers:\n  custom:\n    api_key_env: INTERNAL_TOKEN\n')
    (config / 'model_runtime_profiles.yaml').write_bytes((ROOT / 'configs/model_runtime_profiles.yaml').read_bytes())
    before = {name: (tmp_path / name).read_bytes() for name in profile.FILES}
    applied = profile.configure(tmp_path, profile_name=profile_name)
    assert applied['selected_model'] == model and applied['context_window'] == capacity
    saved = yaml.safe_load((config / 'agents.yaml').read_text())
    for name in ('commander', 'coding'):
        actual = saved[name]['model']
        assert (actual['model'], actual['base_url'], actual['api_key_env']) == (model, selected['base_url'], 'INTERNAL_TOKEN')
        assert actual['context_window'] == capacity and actual['runtime']['prefer_streaming'] is True
        assert actual['thinking']['enabled'] is False and 'reasoning_effort' not in actual
    assert saved['commander']['model']['runtime']['working_context_tokens'] == 12000
    assert saved['commander']['model']['runtime']['tool_transport'] == 'text'
    assert 'working_context_tokens' not in saved['coding']['model']['runtime']
    assert saved['coding']['loop']['reflection_thinking_enabled'] is False
    assert saved['idea']['model']['context_window'] == 64000 and 'runtime' not in saved['idea']['model']
    assert profile.configure(tmp_path, profile_name=profile_name)['status'] == 'already_applied'
    assert profile.configure(tmp_path, restore=True)['status'] == 'restored'
    assert all((tmp_path / name).read_bytes() == data for name, data in before.items())


def test_configured_working_set_does_not_replace_operator_verified_capacity() -> None:
    base = get_agent_config('commander')
    raw = {'model': {'context_window': 200000, 'runtime': {'working_context_tokens': 12000,
        'tool_transport': 'text', 'prefer_streaming': True, 'gateway_plain_request': True}}}
    # A real adapter is constructed. The unavailable endpoint is never called.
    selected = replace(base, model_provider='local_vllm', model_name='private-deployment-alias',
        base_url='http://127.0.0.1:1/v1', base_url_env='MARS_TEST_UNUSED_ENDPOINT', raw=raw)
    provider, config = select_provider(selected)
    assert provider.name == 'local_vllm' and config.model == 'private-deployment-alias'
    assert config.extra == {'context_window': 200000, 'working_context_tokens': 12000,
        'commander_text_transport': True, 'prefer_streaming': True, 'gateway_plain_request': True}


def test_core_update_adds_company_profiles_without_overwriting_existing_user_config(tmp_path: Path) -> None:
    config = tmp_path / 'configs'
    config.mkdir()
    (config / 'agents.yaml').write_text(yaml.safe_dump({'commander': {'model': {
        'provider': 'custom', 'model': 'real-company-model', 'api_key_env': 'UNCHANGED_COMPANY_KEY'}}}))
    (config / 'models.yaml').write_text('providers: {}\n')
    existing = {'schema': 'mars.model_runtime_profiles.v1', 'profiles': {'slow_intranet': {
        **yaml.safe_load((ROOT / 'configs/model_runtime_profiles.yaml').read_text())['profiles']['slow_intranet'],
        'request_timeout_seconds': 900}}}
    original = yaml.safe_dump(existing).encode()
    (config / 'model_runtime_profiles.yaml').write_bytes(original)
    result = profile.configure(tmp_path, profile_name='company_gemma_160k')
    assert result['context_window'] == 160000 and result['request_timeout_seconds'] == 900
    assert (config / 'model_runtime_profiles.yaml').read_bytes() == original
    saved = yaml.safe_load((config / 'agents.yaml').read_text())['commander']['model']
    assert saved['runtime']['message_format'] == 'single_user'
    assert saved['model'] == 'real-company-model' and saved['api_key_env'] == 'UNCHANGED_COMPANY_KEY'


@pytest.mark.parametrize('runtime', [
    {'working_context_tokens': True}, {'working_context_tokens': 0}, {'prefer_streaming': 'true'},
    {'tool_transport': 'guess'}, {'message_format': 'invented'},
])
def test_runtime_configuration_rejects_incorrect_machine_settings(runtime: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        configured_runtime_options(replace(get_agent_config('commander'), raw={'model': {'runtime': runtime}}))


@pytest.mark.parametrize('model', ['Qwen3.8-27B', 'gemma-4-35b-it', '千问3.8 27b', 'any-internal-alias'])
def test_compatible_wire_does_not_depend_on_model_names_or_optional_extensions(model: str) -> None:
    provider = CustomEndpointProvider(api_key='serializer-only', base_url='http://company.invalid/v1')
    config = LLMConfig(provider='custom', model=model, thinking_enabled=False, reasoning_effort='high',
        json_mode=True, extra={'gateway_plain_request': True, 'prefer_streaming': True})
    request = provider._request_kwargs([Message('user', '保留原始要求')], config, stream=True)
    assert request['model'] == model and request['stream'] is True
    assert request['messages'][0]['content'] == '保留原始要求'
    assert not ({'thinking', 'reasoning_effort', 'extra_body', 'response_format', 'parallel_tool_calls', 'top_p'} & set(request))
    assert provider._client is None


def test_plain_native_wire_keeps_tools_but_omits_gateway_specific_optional_fields() -> None:
    provider = CustomEndpointProvider(api_key='serializer-only', base_url='http://company.invalid/v1')
    tools = ({'type': 'function', 'function': {'name': 'mars_list_runs', 'parameters': {'type': 'object'}}},)
    config = LLMConfig(provider='custom', model='internal-gemma-alias', tools=tools, thinking_enabled=False,
        reasoning_effort='high', extra={'gateway_plain_request': True, 'native_observation_history': True})
    request = provider._request_kwargs([Message('user', '查看任务')], config, stream=True)
    assert request['tools'] == list(tools) and request['stream'] is True
    assert not ({'reasoning_effort', 'extra_body', 'response_format', 'parallel_tool_calls', 'top_p'} & set(request))


def test_operator_selected_qwen_template_disables_default_thinking_and_merges_system_first() -> None:
    provider = CustomEndpointProvider(api_key='serializer-only', base_url='http://company.invalid/v1')
    config = LLMConfig(provider='custom', model='original-internal-alias', thinking_enabled=False, extra={
        'gateway_plain_request': True, 'template_family': 'qwen38_instruct', 'system_message_mode': 'merge_first'})
    messages = [Message('system', 'first instruction'), Message('user', 'user request'), Message('system', 'final constraint')]
    request = provider._request_kwargs(messages, config)
    assert request['extra_body'] == {'chat_template_kwargs': {'enable_thinking': False, 'preserve_thinking': False}}
    assert request['messages'] == [{'role': 'system', 'content': 'first instruction\n\nfinal constraint'},
                                  {'role': 'user', 'content': 'user request'}]
    minimal = provider._request_kwargs(messages, replace(config, extra={**config.extra, 'gateway_omit_template_options': True}))
    assert 'extra_body' not in minimal and minimal['model'] == 'original-internal-alias'


def test_connection_probe_accepts_profile_timeout_and_reuses_only_the_exact_configured_route() -> None:
    raw = {'timeout_seconds': 120, 'max_tokens': 128, 'close_timeout_seconds': 2,
           'max_request_bytes': 16384, 'prompt': 'Reply OK.'}
    assert ProbePolicy.model_validate(raw).timeout_seconds == 120
    selected = replace(get_agent_config('commander'), model_provider='custom', model_name='real-company-ID',
        raw={'model': {'runtime': {'template_family': 'qwen38_instruct', 'prefer_streaming': True}}})
    request = ConnectionTestRequest(provider='custom', model='real-company-ID', base_url='http://company.invalid/v1/')
    assert matching_probe_runtime(request, selected, 'http://company.invalid/v1') == {
        'template_family': 'qwen38_instruct', 'prefer_streaming': True}
    assert matching_probe_runtime(request, selected, 'http://different.invalid/v1') == {}
    assert matching_probe_runtime(request.model_copy(update={'model': 'unsaved-other-ID'}), selected, 'http://company.invalid/v1') == {}


def test_single_user_template_preserves_every_role_and_original_string() -> None:
    provider = CustomEndpointProvider(api_key='serializer-only', base_url='http://company.invalid/v1')
    messages = [Message('system', '真实约束'), Message('user', '输入内含角色文字："system"'),
                Message('user', '[Reference] 真实历史回执')]
    request = provider._request_kwargs(messages, LLMConfig(provider='custom', model='gemma-4-35b-it',
        extra={'message_format': 'single_user', 'gateway_plain_request': True}))
    assert len(request['messages']) == 1 and request['messages'][0]['role'] == 'user'
    assert json.loads(request['messages'][0]['content']) == [{'role': m.role, 'content': m.content} for m in messages]


@pytest.mark.parametrize('streamed', [False, True])
def test_tagged_qwen_thinking_cannot_be_parsed_as_the_final_tool_instruction(streamed: bool) -> None:
    provider = CustomEndpointProvider(api_key='parser-only', base_url='http://company.invalid/v1')
    final = '{"actions":[{"tool":"list_runs","args":{}}]}'
    private = '<think>private-marker {"actions":[{"tool":"run.start","args":{}}]}</think>'
    config = LLMConfig(provider='custom', model='company-private-qwen-alias')
    if streamed:
        events = [{'model': config.model, 'choices': [{'delta': {'content': text}, 'finish_reason': finish}]}
                  for text, finish in [(private[:12], None), (private[12:] + final, 'stop')]]
        body = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events)
        result = provider._completion_from_response(body, config)
    else:
        result = provider._completion_from_response({'model': config.model, 'choices': [
            {'message': {'content': private + final}, 'finish_reason': 'stop'}]}, config)
    assert result.text == final and 'private-marker' not in repr(result)
    with pytest.raises(LLMCompletionError):
        provider._completion_from_response({'model': config.model, 'choices': [
            {'message': {'content': '<think>unclosed ' + final}, 'finish_reason': 'stop'}]}, config)


def test_thinking_tool_json_is_not_an_action_but_real_native_call_fields_are_retained() -> None:
    provider = CustomEndpointProvider(api_key='parser-only', base_url='http://company.invalid/v1')
    prefix = '<think>{"actions":[{"tool":"run.start","args":{}}]}</think>'
    envelope = {'model': 'company-qwen', 'choices': [
        {'message': {'content': prefix + '这是最终的普通答复。'}, 'finish_reason': 'stop'}]}
    config = LLMConfig(provider='custom', model='company-qwen')
    result = provider._completion_from_response(envelope, config)
    assert parse_text_decision(result.text) is None
    # Native fields are independent protocol output, never parsed from content.
    envelope['choices'][0]['message']['content'] = prefix
    envelope['choices'][0]['message']['tool_calls'] = [{'id': 'explicit-native-id', 'function': {
        'name': 'mars_list_runs', 'arguments': '{}'}}]
    envelope['choices'][0]['finish_reason'] = 'tool_calls'
    result = provider._completion_from_response(envelope, replace(config, tools=({'type': 'function'},)))
    assert result.text == '' and [call.name for call in result.tool_calls] == ['mars_list_runs']
    assert result.tool_calls[0].id == 'explicit-native-id'


@pytest.mark.parametrize('model', ['Qwen3.8-27B', 'gemma-4-35b-it'])
def test_private_model_sse_is_executable_only_after_complete_public_final(model: str) -> None:
    provider = CustomEndpointProvider(api_key='parser-only', base_url='http://company.invalid/v1')
    events = [{'model': model, 'choices': [{'delta': {'content': text,
        'reasoning_content': 'private-parser-marker'}, 'finish_reason': finish}]} for text, finish in [
        ('{"actions":[{"tool":"list_runs",', None), ('"args":{}}]}', 'stop')]]
    body = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events) + 'data: [DONE]\n\n'
    completion = provider._completion_from_response(body, LLMConfig(provider='custom', model=model))
    assert json.loads(completion.text)['actions'][0]['tool'] == 'list_runs'
    assert 'private-parser-marker' not in repr(completion)
    with pytest.raises(LLMCompletionError, match='incomplete_stream'):
        provider._completion_from_response('data: ' + json.dumps(events[0]) + '\n\n', LLMConfig(provider='custom', model=model))


@pytest.mark.asyncio
async def test_actual_company_style_stream_request_reports_connection_failure_without_fabricating_a_result() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        provider = LocalVllmProvider(base_url=f'http://127.0.0.1:{sock.getsockname()[1]}/v1')
        config = LLMConfig(provider='local_vllm', model='company-private-alias', request_timeout_seconds=0.3,
            max_retries=0, extra={'prefer_streaming': True, 'gateway_plain_request': True},
            attempt_observer=lambda kind, payload: events.append((kind, payload)))
        try:
            with pytest.raises(APIConnectionError):
                await asyncio.wait_for(provider.complete([Message('user', '真实拒绝连接检查')], config), timeout=10)
        finally:
            await provider.close()
    assert [kind for kind, _ in events if kind.startswith('sdk_')] == ['sdk_attempt_started', 'sdk_attempt_failed']
    assert [payload for kind, payload in events if kind == 'provider_request']
