"""Versioned context policy. Role names are configuration data, not harness imports."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json

import yaml

from app.harness.agent_loop.trace import atomic_json, digest
from app.harness.persistence import path_lock
from app.settings import repo_root


def load_policy(path: Path | None = None) -> dict[str, Any]:
    raw = yaml.safe_load((path or repo_root() / 'configs/context.yaml').read_text())
    value = raw.get('runtime_context', {}) if isinstance(raw, dict) else {}
    validate_policy(value)
    return dict(value)


def validate_policy(value: dict[str, Any]) -> None:
    if value.get('version') != 3:
        raise ValueError('runtime context policy requires version 3')
    trigger, target = value.get('trigger_percent'), value.get('target_percent')
    if type(trigger) is not int or type(target) is not int or not 0 < target < trigger < 100:
        raise ValueError('context target must be below trigger, both between 0 and 100')
    for field in ('safety_margin', 'input_budget', 'excerpt_chars', 'read_chars', 'index_files'):
        if type(value.get(field)) is not int or value[field] <= 0:
            raise ValueError(f'invalid context policy {field}')
    for profile in value.get('profiles', {}).values():
        shares = profile.get('shares', [])
        if len(shares) != 4 or any(type(x) is not int or x < 0 for x in shares) or sum(shares) != 100:
            raise ValueError('context profile shares must sum to 100')


def freeze_policy(root: Path, *, legacy_resume: bool = False) -> dict[str, Any]:
    path = root / 'context/runtime_policy.v3.json'
    with path_lock(path.with_suffix('.lock')):
        if path.exists():
            record = json.loads(path.read_text())
            policy = record['policy']
            if record.get('sha256') != digest(policy):
                raise ValueError('context policy snapshot hash mismatch')
            if policy.get('version') == 3:
                validate_policy(policy)
            return dict(policy)
        # Never reinterpret an old native checkpoint under new packing rules.
        policy = {'version': 2} if legacy_resume or any(root.rglob('checkpoint.json')) else load_policy()
        atomic_json(path, {'policy': policy, 'sha256': digest(policy)})
        return policy


def input_budget(policy: dict[str, Any], configured: int, *, output_reserve: int,
                 model_window: int | None = None) -> int:
    ceiling = min(configured, int(policy['input_budget']))
    if model_window is not None:
        if type(model_window) is not int or model_window <= 0:
            raise ValueError('model context window must be a positive integer')
        ceiling = min(ceiling, model_window - output_reserve - int(policy['safety_margin']))
    if ceiling <= 0:
        raise ValueError('no input space after reserving output and safety margin')
    return ceiling


def role_profile(policy: dict[str, Any], agent: str) -> dict[str, Any]:
    profiles = policy.get('profiles', {})
    return dict(profiles.get(agent, profiles.get('default', {})))
