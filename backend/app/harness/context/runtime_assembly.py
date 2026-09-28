"""Source-aware message assembly shared by runtime and preview."""
from dataclasses import asdict
from typing import Any
import json

from app.harness.context.runtime_pack import Material, message_key, reference_message, split_handoff
from app.harness.llm.provider_base import Message


def assemble_materials(base: list[Message], upstream: dict[str, str], metadata: dict[str, Any]) -> list[Message]:
    messages = list(base)
    materials: dict[str, dict[str, Any]] = {}
    for m in messages:
        materials[message_key(m)] = asdict(Material('rules_task', 'host/task'))
    for item in metadata.get('references', []):
        m = reference_message('background', item['source'], item['text'])
        messages.append(m)
        materials[message_key(m)] = asdict(Material('background', item['source'], False))
    original = metadata.get('original_user_request')
    if original and not any(m.content == original for m in messages):
        messages.append(Message('user', '[original user request; preserve scope]\n' + original))
    contract = metadata.get('task_contract')
    if contract:
        messages.append(Message('user', '[host-bound task contract; parent assignment cannot expand permissions]\n'
                                + json.dumps(contract, ensure_ascii=False)))
    if metadata.get('code_profile'):
        messages.append(Message('system', str(metadata['code_profile'].get('code_focus', ''))))
    if metadata.get('repository_index'):
        m = reference_message('code', 'repository index', json.dumps(metadata['repository_index'], ensure_ascii=False))
        messages.append(m)
        materials[message_key(m)] = asdict(Material('code', 'repository index', False))
    for label, text in upstream.items():
        for m, descriptor in split_handoff(label, text, metadata['runtime_policy'].get('optional_sections', [])):
            messages.append(m)
            materials[message_key(m)] = asdict(descriptor)
    metadata['materials'] = materials
    return messages
