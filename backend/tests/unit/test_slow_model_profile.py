"""Pure timeout policy plus real local configuration backups; no model doubles."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import json
from typing import Any

import pytest
import yaml

from app.bridge.commander_native import commander_call_deadline_seconds, native_commander_config
from app.bridge.commander_semantics import semantic_review_config
from app.harness.llm.provider_base import LLMConfig

ROOT = Path(__file__).resolve().parents[3]
spec = spec_from_file_location('slow_profile', ROOT / 'deploy/windows-native/configure_slow_model.py')
assert spec and spec.loader
profile = module_from_spec(spec)
spec.loader.exec_module(profile)


@pytest.mark.parametrize('seconds', [20, 120, 600, 900])
def test_selected_timeout_is_not_shortened_by_hidden_commander_or_review_caps(seconds: int) -> None:
    config = LLMConfig(provider='zhipu', model='GLM-V5.2', request_timeout_seconds=seconds, max_retries=3)
    deadline = commander_call_deadline_seconds(config)
    assert seconds <= deadline <= seconds + 5
    assert native_commander_config(config, deadline).request_timeout_seconds == seconds
    assert semantic_review_config(config, deadline).request_timeout_seconds == seconds
    assert native_commander_config(config, deadline).max_retries == 0


@pytest.mark.parametrize('seconds', [float('inf'), float('nan'), 0, -1])
def test_invalid_timeout_cannot_start_an_unbounded_request(seconds: float) -> None:
    with pytest.raises(ValueError):
        commander_call_deadline_seconds(LLMConfig(provider='zhipu', model='GLM-V5.2', request_timeout_seconds=seconds))


def create_settings(root: Path) -> dict[str, bytes]:
    (root / 'configs').mkdir()
    agents = {'commander': {'enabled': True, 'model': {'provider': 'zhipu', 'model': 'GLM-V5.2',
        'base_url': 'http://company.invalid/v1', 'api_key_env': 'COMPANY_KEY', 'thinking': {'enabled': True},
        'reasoning_effort': 'low', 'max_tokens': 8192, 'request_timeout_seconds': 120, 'retry': {'max_retries': 2}}},
        'idea': {'model': {'provider': 'zhipu', 'model': 'GLM-V5.2', 'thinking': {'enabled': True}, 'max_tokens': 8192}}}
    (root / 'configs/agents.yaml').write_text(yaml.safe_dump(agents,sort_keys=False))
    (root / 'configs/models.yaml').write_text('providers:\n  zhipu:\n    api_key_env: COMPANY_KEY\nconnection_test:\n  timeout_seconds: 30\n  max_tokens: 512\n')
    (root / 'configs/model_runtime_profiles.yaml').write_bytes((ROOT / 'configs/model_runtime_profiles.yaml').read_bytes())
    return {name:(root/name).read_bytes() for name in profile.FILES}


def test_profile_is_idempotent_preserves_routes_and_restores_exact_original_bytes(tmp_path: Path) -> None:
    original = create_settings(tmp_path)
    result = profile.configure(tmp_path)
    assert result['status'] == 'applied'
    agents = yaml.safe_load((tmp_path/'configs/agents.yaml').read_text())
    model = agents['commander']['model']
    assert (model['model'],model['base_url'],model['api_key_env']) == ('GLM-V5.2','http://company.invalid/v1','COMPANY_KEY')
    assert model['request_timeout_seconds'] == 600 and model['max_tokens'] == 2048
    assert model['thinking']['enabled'] is False and 'reasoning_effort' not in model
    assert agents['idea']['model']['thinking']['enabled'] is True
    assert profile.configure(tmp_path)['status'] == 'already_applied'
    assert profile.configure(tmp_path,restore=True)['status'] == 'restored'
    assert all((tmp_path/name).read_bytes()==content for name,content in original.items())


def test_restoring_profile_never_overwrites_later_user_edits(tmp_path: Path) -> None:
    create_settings(tmp_path)
    profile.configure(tmp_path)
    edited = tmp_path/'configs/agents.yaml'
    edited.write_text(edited.read_text()+'\n# later user edit\n')
    before = edited.read_bytes()
    with pytest.raises(ValueError,match='Settings changed'):
        profile.configure(tmp_path,restore=True)
    assert edited.read_bytes() == before


def test_required_thinking_and_longer_timeouts_are_preserved() -> None:
    policy=yaml.safe_load((ROOT/'configs/model_runtime_profiles.yaml').read_text())['profiles']['slow_intranet']
    agents: dict[str, Any] = {'commander':{'model':{'provider':'zhipu','model':'glm-5.3','thinking':{'enabled':True},
             'request_timeout_seconds':900,'max_tokens':1024,'retry':{'max_retries':0}}}}
    changed,_=profile.slow_settings(agents,{},policy)
    model=changed['commander']['model']
    assert model['thinking']['enabled'] is True and model['request_timeout_seconds']==900
    assert model['max_tokens']==1024 and model['retry']['max_retries']==0
    assert agents['commander']['model']['thinking']['enabled'] is True
