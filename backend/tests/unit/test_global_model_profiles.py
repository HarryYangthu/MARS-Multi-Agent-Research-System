"""Real local settings/session storage; no service, model or tool doubles."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

from app.api.global_model_profiles import SaveModelProfile
from app.bridge.commander_session import CommanderSessionStore
from app.bridge.global_model_profiles import GlobalModelProfiles
from app.harness.llm.model_registry import get_agent_config, select_provider


def payload(**changes: object) -> dict[str, object]:
    return {'name': 'Company Qwen', 'provider': 'custom', 'model': 'company/Qwen3.8-27B',
            'base_url': 'http://127.0.0.1:1/v1', 'context_window': 200000,
            'compatibility': 'qwen_200k', **changes}


def test_global_model_profiles_store_keys_separately_and_never_return_them(tmp_path: Path) -> None:
    profiles = GlobalModelProfiles(tmp_path)
    item = profiles.save(payload(), api_key='private-contract-marker')
    assert item['configured'] and item['api_key_configured']
    assert 'private-contract-marker' not in json.dumps(item)
    assert 'private-contract-marker' not in profiles.path.read_text()
    assert 'private-contract-marker' not in json.dumps(profiles.public())
    assert profiles._secrets()[item['id']] == 'private-contract-marker'
    if os.name != 'nt':
        assert profiles.secrets_path.stat().st_mode & 0o077 == 0
    profiles.set_default(item['id'])
    assert GlobalModelProfiles(tmp_path).public()['default_id'] == item['id']
    updated = profiles.save(payload(id=item['id'], name='Renamed'), api_key='')
    assert updated['api_key_configured'] and len(profiles.public()['profiles']) == 1
    assert profiles._secrets()[item['id']] == 'private-contract-marker'


def test_two_profiles_and_session_selection_are_shared_and_survive_reload(tmp_path: Path) -> None:
    profiles = GlobalModelProfiles(tmp_path)
    qwen = profiles.save(payload(), api_key='first-private-marker')
    gemma = profiles.save(payload(name='Company Gemma', model='gemma-4-35b-it', compatibility='gemma_160k', context_window=160000), api_key='second-private-marker')
    profiles.set_default(gemma['id'])
    sessions = CommanderSessionStore(tmp_path / 'sessions')
    a, b = sessions.create(project='first-project'), sessions.create(project='second-project')
    a.selected_model_id, b.selected_model_id = qwen['id'], profiles.public()['default_id']
    sessions.persist(a)
    sessions.persist(b)
    restored = CommanderSessionStore(tmp_path / 'sessions')
    assert restored.get(a.conv_id).selected_model_id == qwen['id']
    assert restored.get(b.conv_id).selected_model_id == gemma['id']
    assert len(GlobalModelProfiles(tmp_path).public()['profiles']) == 2


@pytest.mark.parametrize('provider,compatibility,capacity', [
    ('custom', 'qwen_200k', 200000), ('openai', 'gemma_160k', 160000), ('local_vllm', 'generic', 128000),
])
def test_resolve_preserves_exact_private_route_and_leaves_agent_defaults_unchanged(
    tmp_path: Path, provider: str, compatibility: str, capacity: int,
) -> None:
    profiles = GlobalModelProfiles(tmp_path)
    item = profiles.save(payload(provider=provider, model='original-private-ID', compatibility=compatibility, context_window=capacity), api_key='private-key-marker')
    base = get_agent_config('commander')
    original = repr(base)
    resolved = profiles.resolve(item['id'], base)
    provider_adapter, runtime = select_provider(resolved)
    try:
        assert provider_adapter.base_url == 'http://127.0.0.1:1/v1'
        assert resolved.model_name == 'original-private-ID' and resolved.model_provider == provider
        assert runtime.extra['context_window'] == capacity and runtime.extra['working_context_tokens'] == 12000
        assert runtime.extra['commander_text_transport'] is True
        assert runtime.extra.get('template_family') == ('qwen38_instruct' if compatibility == 'qwen_200k' else None)
        assert runtime.max_tokens == 2048 and runtime.max_retries == 0
        assert repr(base) == original and 'private-key-marker' not in repr(resolved)
    finally:
        os.environ.pop(resolved.api_key_env, None)
        os.environ.pop(resolved.base_url_env, None)


def test_profile_input_secrets_are_not_in_the_metadata_payload() -> None:
    model = SaveModelProfile(**payload(), api_key='private-input-marker')
    assert 'private-input-marker' not in repr(model)
    assert 'api_key' not in model.model_dump(exclude={'api_key'})


@pytest.mark.parametrize('changes', [
    {'base_url': 'http://user:private@example.invalid/v1'}, {'base_url': 'http://company.invalid/v1?key=private'},
    {'model': ''}, {'context_window': True}, {'compatibility': 'unknown'}, {'provider': 'mock'},
])
def test_invalid_profile_does_not_create_a_half_configuration(tmp_path: Path, changes: dict[str, object]) -> None:
    profiles = GlobalModelProfiles(tmp_path)
    with pytest.raises(ValueError):
        profiles.save(payload(**changes), api_key='private-key-marker')
    assert not profiles.path.exists() and not profiles.secrets_path.exists()


def test_default_profile_must_exist_and_unselected_old_sessions_use_existing_defaults(tmp_path: Path) -> None:
    profiles = GlobalModelProfiles(tmp_path)
    with pytest.raises(ValueError):
        profiles.set_default('absent')
    base = get_agent_config('commander')
    assert profiles.resolve('', base) is base
    with pytest.raises(ValueError):
        profiles.resolve('absent', base)
