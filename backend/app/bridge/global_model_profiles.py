"""Machine-wide conversation model connections, separate from project state."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from app.harness.llm.model_registry import AgentConfig
from app.harness.llm.model_capabilities import setup_reasoning
from app.harness.persistence import atomic_write_json, path_lock
from app.settings import repo_root, set_runtime_env


class GlobalModelProfiles:
    def __init__(self, root: Path | None = None) -> None:
        self.directory = (root or repo_root()) / 'local'
        self.path = self.directory / 'model_profiles.json'
        self.secrets_path = self.directory / 'model_profile_secrets.json'

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {'schema': 'mars.global_model_profiles.v1', 'default_id': '', 'profiles': []}
        record = json.loads(self.path.read_text(encoding='utf-8'))
        if record.get('schema') != 'mars.global_model_profiles.v1' or not isinstance(record.get('profiles'), list):
            raise ValueError('全局模型配置无法读取，请保留文件并检查 local/model_profiles.json。')
        return dict(record)

    def _secrets(self) -> dict[str, str]:
        if not self.secrets_path.exists():
            return {}
        return dict(json.loads(self.secrets_path.read_text(encoding='utf-8')))

    def public(self) -> dict[str, Any]:
        with path_lock(self.path.with_suffix('.lock')):
            record, secrets = self._read(), self._secrets()
            return {'default_id': record.get('default_id', ''), 'profiles': [
                {**item, 'api_key_configured': bool(secrets.get(item['id'])),
                 'configured': item['provider'] == 'local_vllm' or bool(secrets.get(item['id']))}
                for item in record['profiles']]}

    def save(self, payload: dict[str, Any], *, api_key: str = '') -> dict[str, Any]:
        data = dict(payload)
        identifier = str(data.get('id') or uuid4().hex)
        if not re.fullmatch(r'[a-f0-9]{32}', identifier):
            raise ValueError('模型配置标识无效。')
        provider = str(data.get('provider', 'custom'))
        if provider not in {'custom', 'local_vllm', 'openai', 'qwen', 'zhipu', 'deepseek'}:
            raise ValueError('请选择支持 OpenAI 兼容接口的服务类型。')
        endpoint = str(data.get('base_url', '')).strip().rstrip('/')
        parsed = urlsplit(endpoint)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment or any(c.isspace() for c in endpoint)):
            raise ValueError('接口地址须为完整 http/https 地址，不能包含密钥或查询参数。')
        name, model = str(data.get('name', '')).strip(), str(data.get('model', '')).strip()
        if not name or len(name) > 80 or not model or len(model) > 200:
            raise ValueError('请填写显示名称与公司要求的真实模型 ID。')
        capacity = data.get('context_window', 128000)
        if type(capacity) is not int or not 4096 <= capacity <= 2000000:
            raise ValueError('模型上下文容量须为 4096 到 2000000 之间的整数。')
        compatibility = str(data.get('compatibility', 'generic'))
        if compatibility not in {'generic', 'qwen_200k', 'gemma_160k'}:
            raise ValueError('模型兼容配置无效。')
        row = {'id': identifier, 'name': name, 'provider': provider, 'model': model,
               'base_url': endpoint, 'context_window': capacity, 'compatibility': compatibility}
        with path_lock(self.path.with_suffix('.lock')):
            record, secrets = self._read(), self._secrets()
            if data.get('id') and not any(item['id'] == identifier for item in record['profiles']):
                raise ValueError('找不到要更新的模型配置，请重新读取。')
            if api_key:
                if any(c in api_key for c in '\r\n\x00'):
                    raise ValueError('API Key 不能包含换行。')
                secrets[identifier] = api_key
                atomic_write_json(self.secrets_path, secrets)
                os.chmod(self.secrets_path, 0o600)
            record['profiles'] = [row if item['id'] == identifier else item for item in record['profiles']]
            if not any(item['id'] == identifier for item in record['profiles']):
                record['profiles'].append(row)
            atomic_write_json(self.path, record)
        return {**row, 'api_key_configured': bool(secrets.get(identifier)),
                'configured': provider == 'local_vllm' or bool(secrets.get(identifier))}

    def set_default(self, identifier: str) -> None:
        with path_lock(self.path.with_suffix('.lock')):
            record = self._read()
            if identifier and not any(item['id'] == identifier for item in record['profiles']):
                raise ValueError('所选模型配置不存在。')
            record['default_id'] = identifier
            atomic_write_json(self.path, record)

    def resolve(self, identifier: str, base: AgentConfig) -> AgentConfig:
        if not identifier:
            return base
        with path_lock(self.path.with_suffix('.lock')):
            record = self._read()
            item = next((p for p in record['profiles'] if p['id'] == identifier), None)
            if item is None:
                raise ValueError('当前对话选择的模型配置不存在，请在输入区重新选择模型。')
            secret = self._secrets().get(identifier, '')
        key_env = 'MARS_MODEL_PROFILE_' + identifier.upper() + '_KEY'
        url_env = 'MARS_MODEL_PROFILE_' + identifier.upper() + '_BASE_URL'
        set_runtime_env({key_env: secret, url_env: item['base_url']})
        runtime: dict[str, Any] = {'tool_transport': 'text', 'prefer_streaming': True,
            'gateway_plain_request': True, 'working_context_tokens': 12000, 'system_message_mode': 'merge_first'}
        if item['compatibility'] == 'qwen_200k':
            runtime.update(template_family='qwen38_instruct', message_format='single_user')
        elif item['compatibility'] == 'gemma_160k':
            runtime['message_format'] = 'single_user'
        raw = deepcopy(dict(base.raw))
        thinking, effort = setup_reasoning(item['provider'], item['model'])
        model = {**raw.get('model', {}), 'provider': item['provider'], 'model': item['model'],
            'api_key_env': key_env, 'base_url_env': url_env, 'base_url': item['base_url'],
            'context_window': item['context_window'], 'runtime': runtime, 'thinking': {'enabled': thinking},
            'max_tokens': 2048, 'request_timeout_seconds': 600, 'retry': {'max_retries': 0}}
        model.pop('reasoning_effort', None)
        if effort is not None:
            model['reasoning_effort'] = effort
        raw['model'] = model
        return replace(base, raw=raw, model_provider=item['provider'], model_name=item['model'],
            api_key_env=key_env, base_url=item['base_url'], base_url_env=url_env, max_tokens=2048,
            thinking_enabled=thinking, reasoning_effort=effort, request_timeout_seconds=600, max_retries=0)


def resolve_selected_agent_config(session: Any, base: AgentConfig) -> AgentConfig:
    return GlobalModelProfiles().resolve(str(session.selected_model_id or ''), base)
