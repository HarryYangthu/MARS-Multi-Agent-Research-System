"""Real files and pure packing contracts; no provider or tool substitutes."""
from pathlib import Path
from dataclasses import replace
import json

import pytest

from app.harness.context.runtime_pack import Material, message_key, pack_messages, read_material, split_handoff
from app.harness.context.runtime_policy import load_policy, freeze_policy, input_budget
from app.harness.context.runtime_native import pack_native
from app.harness.context.runtime_manifest import record_manifest, record_usage
from app.harness.agent_loop.context import token_upper_bound
from app.harness.agent_loop.trace import digest
from app.harness.llm.provider_base import Message, ToolCall


def test_threshold_exact_and_recovery(tmp_path: Path) -> None:
    policy = load_policy()
    assert input_budget(policy, 128000, output_reserve=8192, model_window=128000) == 117760
    messages = [Message('system', 'Never modify baseline'), Message('user', 'logs ' * 5000), Message('user', 'Current goal')]
    materials = {message_key(messages[1]): Material('history', 'completed steps', False)}
    # Use background so this is not the newest unprocessed tool exchange.
    materials[message_key(messages[1])] = Material('background', 'old notes', False)
    size = token_upper_bound(messages)
    _, below = pack_messages(messages, policy=policy, budget=size * 100 // 79 + 1,
        materials=materials, root=tmp_path)
    assert not below['triggered'] and below['used'] == size
    packed, at = pack_messages(messages, policy=policy, budget=size * 100 // 80,
        materials=materials, root=tmp_path)
    assert at['triggered'] and at['used'] <= at['target']
    assert at['trigger_percent'] == 80 and at['target_percent'] == 65
    assert at['compression_method'] == 'reversible_excerpt_and_offload'
    assert packed[0] == messages[0] and packed[-1] == messages[-1]
    restored, again = pack_messages(messages, policy=policy, budget=size * 100 // 80,
        materials=materials, root=tmp_path, previous=at['state'])
    assert restored == packed and not again['triggered']
    assert again['state']['levels'] == at['state']['levels']


def test_protected_over_target_is_retained_and_over_max_blocks(tmp_path: Path) -> None:
    messages = [Message('system', 'hard constraint ' * 350)]
    size = token_upper_bound(messages)
    _, manifest = pack_messages(messages, policy=load_policy(), budget=size + 10, root=tmp_path)
    assert manifest['triggered'] and not manifest['target_reached']
    with pytest.raises(ValueError, match='no request sent'):
        pack_messages(messages, policy=load_policy(), budget=size - 1, root=tmp_path)


def test_latest_tool_pair_and_failed_attempt_survive(tmp_path: Path) -> None:
    history = [{'tool': 'code.repo_reader', 'ok': True, 'output': {'content': 'old ' * 7000},
                'native_call': {'id': 'a', 'name': 'code_repo_reader', 'arguments': '{"path":"a.py"}'}},
               {'tool': 'code.repo_reader', 'ok': False, 'error': 'specific failure',
                'native_call': {'id': 'b', 'name': 'code_repo_reader', 'arguments': '{"path":"b.py"}'}},
               {'tool': 'code.repo_reader', 'ok': True, 'output': {'content': 'needed function'},
                'native_call': {'id': 'c', 'name': 'code_repo_reader', 'arguments': '{"path":"c.py"}'}}]
    packed, manifest = pack_native(pinned=[Message('user', 'task')], history=history,
        feedback='unresolved constraint', candidate='', budget=12000, tools=(), policy=load_policy(),
        metadata={}, root=tmp_path, previous=None, agent='coding', readback_available=True, native=True)
    assert manifest['triggered'] and manifest['decisions']
    wire = [m.to_wire() for m in packed]
    ids = [c['id'] for m in wire for c in m.get('tool_calls', [])]
    assert ids == [m['tool_call_id'] for m in wire if m['role'] == 'tool'] == ['a', 'b', 'c']
    assert 'specific failure' in str(wire) and 'needed function' in str(wire)
    assert 'unresolved constraint' in str(wire)


def test_review_evidence_never_replaced_with_reference(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match='protected context'):
        pack_native(pinned=[Message('user', 'review')], history=[{'tool':'code.repo_reader',
            'ok': True, 'output': {'content': 'required evidence ' * 2000}}], feedback='', candidate='',
            budget=10000, tools=(), policy=load_policy(), metadata={}, root=tmp_path, previous=None,
            agent='idea', readback_available=False, reviewing=True, required_review_tools=('code.repo_reader',))


def test_original_hash_paging_and_tamper(tmp_path: Path) -> None:
    from app.harness.context.runtime_pack import store_material
    source = Message('user', 'αβ' * 100)
    ref = store_material(tmp_path, source)
    first = read_material(tmp_path, ref, 0, 17)
    rest = read_material(tmp_path, ref, first['next_offset'], 500)
    assert first['content'] + rest['content'] == source.content
    assert rest['next_offset'] is None
    path = tmp_path / 'context/materials' / f'{ref}.json'
    path.write_text('{}')
    with pytest.raises(ValueError, match='hash mismatch'):
        read_material(tmp_path, ref, 0, 10)
    with pytest.raises(ValueError):
        read_material(tmp_path, '../other', 0, 10)


def test_tool_schemas_count_once_and_manifest_matches_wire(tmp_path: Path) -> None:
    messages = [Message('system', 'rules'), Message('user', 'goal')]
    tools = ({'type': 'function', 'function': {'name': 'read', 'parameters': {'type':'object'}}},)
    packed, manifest = pack_messages(messages, policy=load_policy(), budget=4000, tools=tools, root=tmp_path)
    assert manifest['used'] == token_upper_bound(messages) + manifest['tool_schema_upper_bound_tokens']
    assert sum(manifest['components'].values()) == manifest['used']
    path = record_manifest(tmp_path, agent='idea', node='idea', project='p', messages=packed, tools=tools, manifest=manifest)
    raw = json.loads(path.read_text())
    assert raw['diagnostics']['payload_sha256'] == digest({'messages':[m.to_wire() for m in packed], 'tools':tools})
    record_usage(path, {'prompt_tokens': 17})
    assert json.loads(path.read_text())['diagnostics']['provider_usage']['prompt_tokens'] == 17


def test_unknown_upstream_and_metadata_are_protected() -> None:
    source = '---\nseed: 42\n---\n# Acceptance\nmetric > 30\n# Background\nprior history\n'
    blocks = split_handoff('plan.v2', source, ['Background'])
    assert ''.join(m.content.split('\n',1)[1] for m,_ in blocks) == source
    assert [d.protected for _,d in blocks] == [True,True,False]
    assert split_handoff('unknown', 'never lose this parameter', ['Background'])[0][1].protected


def test_old_checkpoint_keeps_legacy_and_new_policy_freezes(tmp_path: Path) -> None:
    old = tmp_path / 'old';old.mkdir();(old/'checkpoint.json').write_text('{}')
    assert freeze_policy(old)['version'] == 2
    new = tmp_path/'new'
    policy = freeze_policy(new)
    assert policy['version'] == 3 and freeze_policy(new, legacy_resume=True) == policy
    assert input_budget(policy, 48000, output_reserve=8192, model_window=32000) == 32000-8192-policy['safety_margin']


def test_no_offload_when_reader_unavailable(tmp_path: Path) -> None:
    m = Message('user', 'unavailable source ' * 1000)
    with pytest.raises(ValueError, match='protected context'):
        pack_messages([m], policy=load_policy(), budget=4000, root=tmp_path,
                      materials={message_key(m):Material('background','doc',False)}, readback_available=False)


def test_incomplete_tool_pair_rejected() -> None:
    with pytest.raises(ValueError, match='incomplete tool'):
        pack_messages([Message('assistant','', (ToolCall('id','read','{}'),))], policy=load_policy(), budget=4000)


@pytest.mark.asyncio
async def test_real_material_dispatch_requires_host_read_scope(tmp_path: Path) -> None:
    from app.harness.context.runtime_pack import store_material
    from app.harness.tools.registry import ToolContext, get_registry
    freeze_policy(tmp_path)
    ref = store_material(tmp_path, Message('user', 'real archived background'))
    registry = get_registry()
    ctx = ToolContext('context-dispatch', 'synthetic_regression', 'idea', extra={'run_root': str(tmp_path)})
    denied = await registry.dispatch('context.read_material', {'ref': ref}, ctx)
    assert not denied.ok and denied.status == 'not_allowed'
    ctx.supplemental_read_scope = registry.scope_for_read_tools('idea', ('context.read_material',))
    result = await registry.dispatch('context.read_material', {'ref': ref}, ctx)
    assert result.ok and result.output['content'] == 'real archived background'
    with pytest.raises(ValueError, match='cannot authorize'):
        registry.scope_for_read_tools('idea', ('code.apply_patch',))


def test_explicit_model_capacity_is_validated() -> None:
    from app.harness.llm.model_registry import configured_context_window, get_agent_config
    config = get_agent_config('idea')
    assert configured_context_window(replace(config, raw={'model': {'context_window': 32000}})) == 32000
    assert configured_context_window(replace(config, raw={'model': {}})) is None
    for invalid in (True, 0, '32000'):
        with pytest.raises(ValueError, match='positive integer'):
            configured_context_window(replace(config, raw={'model': {'context_window': invalid}}))


def test_receipt_index_cannot_displace_latest_observation_protection(tmp_path: Path) -> None:
    # A receipt index follows the actual observation. It is not a replacement
    # for the unread source, even though both are classified as runtime history.
    with pytest.raises(ValueError, match='protected context'):
        pack_native(pinned=[Message('user', 'read this source')],
            history=[{'tool': 'code.repo_reader', 'ok': True, 'output': {'content': 'important ' * 2000}}],
            feedback='', candidate='', budget=10000, tools=(), policy=load_policy(),
            metadata={}, root=tmp_path, previous=None, agent='coding', readback_available=True)


def test_repository_index_can_offload_while_latest_source_is_retained(tmp_path: Path) -> None:
    index = Message('user', 'directory listing\n' * 4000)
    code = Message('user', 'def forward(x): return x')
    messages = [Message('system', 'Keep baseline unchanged'), index, code, Message('user', 'Improve residual')]
    materials = {message_key(index): Material('code', 'repository index', False),
                 message_key(code): Material('code', 'model.py', False)}
    packed, manifest = pack_messages(messages, policy=load_policy(), budget=10000,
                                     materials=materials, root=tmp_path)
    assert manifest['used'] < 10000
    assert packed[2] == code and packed[0] == messages[0] and packed[-1] == messages[-1]
    assert read_material(tmp_path, manifest['segments'][1]['refs'][0], 0, 100000)['content'] == index.content
    # Also applies to old manifests containing only an index and no source yet.
    packed, manifest = pack_messages([messages[0], index, messages[-1]], policy=load_policy(),
                                     budget=10000, materials=materials, root=tmp_path)
    assert manifest['used'] < 10000 and not manifest['segments'][1]['protected']


def test_observation_status_comes_from_outer_envelope() -> None:
    from app.harness.context.runtime_native import observation_failed
    assert not observation_failed(json.dumps({'ok': True, 'output': {'ok': False}}))
    assert observation_failed(json.dumps({'ok': False, 'error': 'missing baseline'}))
    assert observation_failed('invalid host envelope')
    assert observation_failed(json.dumps({'output': 'unknown status'}))
