"""Classify bounded source observations separately from expendable tool logs."""
from __future__ import annotations

from app.harness.context.runtime_pack import Material, message_key
from app.harness.llm.provider_base import Message


def commander_materials(messages: list[Message]) -> dict[str, Material]:
    materials: dict[str, Material] = {}
    for message in messages:
        content = message.content
        if content.startswith('[observation tool='):
            code_observation = content.startswith('[observation tool=code.repo_')
            source_read = content.startswith('[observation tool=code.repo_read_lines]')
            materials[message_key(message)] = Material(
                'code' if code_observation else 'tool',
                ('repository source' if source_read else 'repository index')
                if code_observation else 'commander observation',
                '"ok": false' in content,
            )
        elif message.role == 'assistant':
            materials[message_key(message)] = Material('history', 'commander completed reply', False)
        elif content.startswith('[reference background:'):
            materials[message_key(message)] = Material('background', 'project background', False)
    return materials
