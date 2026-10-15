"""Real input files and context packing; no model or execution stand-ins."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest
import yaml

from app.bridge.commander_context import commander_materials
from app.harness.context.runtime_pack import message_key, pack_messages
from app.harness.context.runtime_policy import freeze_policy
from app.harness.llm.provider_base import Message
from app.harness.tools.execution.handoff import execution_delivery_context
from app.harness.tools.execution.local_command import local_command_protocol, _RESULT_SCHEMA
from app.settings import reset_settings_cache


def test_current_source_survives_navigation_and_background_pressure(tmp_path: Path) -> None:
    configuration = Message(role='user', content='[observation tool=code.repo_read_lines] '
        + json.dumps({'ok': True, 'output': {'path': 'configs/train.yaml', 'content': 'Epoch: 1\nlr: 0.001\n'}}))
    implementation = Message(role='user', content='[observation tool=code.repo_read_lines] '
        + json.dumps({'ok': True, 'output': {'path': 'model.py', 'content': 'x = 1\n' * 400}}))
    navigation = Message(role='user', content='[observation tool=code.repo_list] '
        + json.dumps({'ok': True, 'output': {'entries': ['train.yaml']}}))
    messages = [Message(role='system', content='Keep current evidence separate from historical results.'),
        Message(role='user', content='[reference background: prior trials]\n' + 'Historical training used 200 epochs.\n' * 250),
        Message(role='user', content='Read the actual current configuration.'), configuration, implementation, navigation]
    materials = commander_materials(messages)
    assert materials[message_key(configuration)].kind == 'code'
    assert materials[message_key(navigation)].source == 'repository index'
    policy = freeze_policy(tmp_path)
    packed, manifest = pack_messages(messages, policy=policy, budget=5000,
        materials=materials, root=tmp_path, agent='commander')
    assert manifest['decisions']
    assert configuration in packed and implementation in packed
    assert any('incomplete reference background' in item.content for item in packed)


def test_coding_receives_the_actual_registered_command_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / 'execution.yaml'
    registered = {'id': 'measured-training', 'argv': [sys.executable, '{code_root}/train.py'],
                  'required_metrics': ['loss'], 'label': 'Human-configured command'}
    config.write_text(yaml.safe_dump({'execution': {'local_commands': [registered]}}))
    monkeypatch.setenv('MARS_EXECUTION_CONFIG_PATH', str(config))
    monkeypatch.setenv('MARS_EXECUTION_BACKEND', 'local_command')
    reset_settings_cache()
    try:
        context = execution_delivery_context(project='pimc')
        assert context['backend'] == 'local_command'
        assert context['local_commands'] == [{key: registered[key] for key in ('id', 'label', 'argv', 'required_metrics')}]
        assert 'command_id' in context['instruction'] and 'MARS_RESULT_PATH' in context['instruction']
        protocol = context['protocol']
        assert protocol['result_schema'] == _RESULT_SCHEMA
        assert '不是 JSON 文本' in protocol['request']['delivery']
        assert 'request["config"]' in protocol['request']['config_location']
        protocol['result_schema']['required'].clear()
        assert local_command_protocol()['result_schema']['required'] == _RESULT_SCHEMA['required']
        config.write_text(yaml.safe_dump({'execution': {'local_commands': []}}))
        assert execution_delivery_context(project='pimc')['local_commands'] == []
    finally:
        reset_settings_cache()
