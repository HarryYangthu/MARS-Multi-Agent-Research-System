"""Apply/restore a bounded slow-model profile without changing model routing."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

import yaml

from app.harness.llm.model_capabilities import requires_glm_thinking

FILES = ('configs/agents.yaml', 'configs/models.yaml')


def slow_settings(agents: dict[str, Any], models: dict[str, Any], policy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    agents, models = deepcopy(agents), deepcopy(models)
    for name, body in agents.items():
        if not isinstance(body, dict) or not isinstance(body.get('model'), dict):
            continue
        model = body['model']
        model['request_timeout_seconds'] = max(float(model.get('request_timeout_seconds', 120)), policy['request_timeout_seconds'])
        retry = model.setdefault('retry', {})
        retry['max_retries'] = min(int(retry.get('max_retries', 0)), policy['max_retries'])
        if name == 'commander':
            model['max_tokens'] = min(int(model.get('max_tokens', 4096)), policy['commander_max_tokens'])
            if (policy['commander_disable_optional_thinking'] and not requires_glm_thinking(
                    str(model.get('provider', '')), str(model.get('model', '')))):
                model['thinking'] = {**model.get('thinking', {}), 'enabled': False}
                model.pop('reasoning_effort', None)
    probe = models.setdefault('connection_test', {})
    probe['timeout_seconds'] = max(float(probe.get('timeout_seconds', 30)), policy['connection_test_timeout_seconds'])
    probe['max_tokens'] = min(int(probe.get('max_tokens', 512)), policy['connection_test_max_tokens'])
    return agents, models


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, data: bytes) -> None:
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def configure(root: Path, *, restore: bool = False) -> dict[str, Any]:
    """Real local configuration transaction; backups never enter a release."""
    root = root.resolve()
    state = root / 'local/model-profiles'
    receipt_path = state / 'slow-intranet.json'
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        unchanged = all(digest((root / name).read_bytes()) == receipt['applied'][name] for name in FILES)
        if restore:
            if not unchanged:
                raise ValueError('Settings changed after applying this profile. Keep current settings; use the saved backup for a manual merge.')
            originals = {name: (state / receipt['snapshot'] / name).read_bytes() for name in FILES}
            if any(digest(originals[name]) != receipt['original'][name] for name in FILES):
                raise ValueError('Profile backup checksum mismatch; current settings were preserved')
            for name in FILES:
                _write(root / name, originals[name])
            receipt_path.unlink()
            return {'status': 'restored', 'backup': str(state / receipt['snapshot'])}
        if unchanged:
            return {'status': 'already_applied', 'backup': str(state / receipt['snapshot'])}
    elif restore:
        raise ValueError('No slow-model profile backup is available')
    raw = yaml.safe_load((root / 'configs/model_runtime_profiles.yaml').read_text())
    if raw.get('schema') != 'mars.model_runtime_profiles.v1':
        raise ValueError('Invalid model runtime profile')
    policy = raw['profiles']['slow_intranet']
    for key in ('request_timeout_seconds', 'max_retries', 'commander_max_tokens',
                'connection_test_timeout_seconds', 'connection_test_max_tokens'):
        if type(policy.get(key)) is not int or policy[key] < (0 if key == 'max_retries' else 1):
            raise ValueError('Invalid model runtime profile field: ' + key)
    original = {name: (root / name).read_bytes() for name in FILES}
    updated = slow_settings(yaml.safe_load(original[FILES[0]]), yaml.safe_load(original[FILES[1]]), policy)
    contents = {name: yaml.safe_dump(value, allow_unicode=True, sort_keys=False).encode() for name, value in zip(FILES, updated)}
    snapshot = uuid4().hex
    backup = state / snapshot
    for name in FILES:
        target = backup / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original[name])
    receipt = {'snapshot': snapshot, 'original': {name: digest(original[name]) for name in FILES},
               'applied': {name: digest(contents[name]) for name in FILES}}
    changed = []
    try:
        for name in FILES:
            _write(root / name, contents[name])
            changed.append(name)
        _write(receipt_path, (json.dumps(receipt, indent=2) + '\n').encode())
    except BaseException:
        for name in changed:
            _write(root / name, original[name])
        shutil.rmtree(backup)
        raise
    return {'status': 'applied', 'backup': str(backup), 'request_timeout_seconds': policy['request_timeout_seconds']}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    result = configure(args.root, restore=args.restore)
    print(json.dumps(result, ensure_ascii=False))
    print('Restart MARS to use the updated model settings. Model IDs, endpoints, secrets and project history were preserved.')


if __name__ == '__main__':
    main()
