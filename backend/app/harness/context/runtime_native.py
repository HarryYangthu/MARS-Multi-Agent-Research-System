"""Native protocol adapter for the shared material packer."""
from pathlib import Path
from typing import Any
import json

from app.harness.agent_loop.context import pack_context
from app.harness.agent_loop.trace import canonical, digest
from app.harness.context.runtime_pack import Material, message_key, pack_messages
from app.harness.llm.provider_base import Message


def observation_envelope(content: str) -> dict[str, Any] | None:
    """Read only the host envelope, never quoted/nested tool data."""
    prefix = '[untrusted prior action and host Observation]\n'
    raw = content[len(prefix):] if content.startswith(prefix) else content
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def observation_failed(content: str) -> bool:
    value = observation_envelope(content)
    return value is None or value.get('ok') is not True


def pack_native(*, pinned: list[Message], history: list[dict[str, Any]], feedback: str, candidate: str,
                budget: int, tools: tuple[dict[str, Any], ...], policy: dict[str, Any],
                metadata: dict[str, Any], root: Path | None, previous: dict[str, Any] | None,
                agent: str, readback_available: bool, **options: Any) -> tuple[list[Message], dict[str, Any]]:
    # Reuse native pairing and required review evidence logic without pre-trimming.
    full, legacy = pack_context(pinned, history, feedback, candidate, budget=10**12,
                                observation_chars=max(6000, len(canonical(history))), **options)
    materials = {key: Material(**value) for key, value in metadata.get('materials', {}).items()}
    pinned_keys = {message_key(m) for m in pinned}
    required_tools = set(options.get('required_review_tools', ()))
    for m in full:
        key = message_key(m)
        if key in pinned_keys:
            continue
        if m.role == 'tool' or m.content.startswith(('[untrusted prior action and host Observation]',)):
            failed = observation_failed(m.content)
            envelope = observation_envelope(m.content) or {}
            essential = envelope.get('tool') in required_tools or envelope.get('tool') == 'context.read_material'
            # Keep the evidence needed to author and review the method, including
            # explicit readbacks. Offloading it again causes repeated API calls.
            materials[key] = Material('tool', 'actual tool observation',
                                      failed or essential or bool(options.get('reviewing')))
        elif m.content.startswith(('[untrusted current candidate', '[untrusted action receipt index')):
            materials[key] = Material('history', 'active candidate and receipt index', True)
    messages, manifest = pack_messages(full, policy=policy, budget=budget, tools=tools,
        materials=materials, root=root, previous=previous, agent=agent, readback_available=readback_available)
    manifest['estimated_upper_bound_tokens'] = manifest['used'] - manifest['tool_schema_upper_bound_tokens']
    manifest['total_input_upper_bound_tokens'] = manifest['used']
    manifest['visible_sha256'] = digest([m.to_wire() for m in messages])
    for name in ('compressed_history', 'omitted_history', 'preserved_review_history',
                 'deduplicated_review_history', 'reviewing', 'prior_review_issues_visible', 'validation_issues_visible'):
        manifest[name] = legacy[name]
    return messages, manifest
