"""Native protocol adapter for the shared material packer."""
from pathlib import Path
from typing import Any
import json

from app.harness.agent_loop.context import pack_context
from app.harness.agent_loop.native_protocol import history_groups
from app.harness.agent_loop.trace import canonical, digest
from app.harness.context.runtime_pack import Material, message_key, pack_messages
from app.harness.llm.provider_base import Message
from app.harness.context.repository_materials import repository_policy
from app.harness.context.repository_evidence import repository_index


def observation_envelope(content: str) -> dict[str, Any] | None:
    """Read only the host envelope, never quoted/nested tool data."""
    prefixes = ('[untrusted prior action and host Observation]\n',
                '[untrusted complete review Observation; exact duplicates may reference included text]\n')
    raw = next((content[len(prefix):] for prefix in prefixes if content.startswith(prefix)), content)
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
    source_policy = repository_policy()
    full, legacy = pack_context(pinned, history, feedback, candidate, budget=10**12,
                                observation_chars=max(6000, len(canonical(history))),
                                group_repo_reads=source_policy.enabled, **options)
    materials = {key: Material(**value) for key, value in metadata.get('materials', {}).items()}
    if source_policy.enabled:
        index = repository_index(history, max_files=source_policy.max_index_files,
                                 repeat_after=source_policy.repeat_warning_after)
        if index['files']:
            message = Message('user', '[host repository reading index; metadata, not source evidence]\n' + canonical(index))
            full.append(message)
            materials[message_key(message)] = Material('history', 'repository reading index', True)
    pinned_keys = {message_key(m) for m in pinned}
    required_tools = set(options.get('required_review_tools', ()))
    latest_group = history_groups(history)[-1] if history else []
    latest_call_ids = {call['id'] for item in latest_group
                       if isinstance(call := item.get('native_call'), dict)
                       and isinstance(call.get('id'), str)}
    for m in full:
        key = message_key(m)
        if key in pinned_keys:
            continue
        if m.role == 'tool' or m.content.startswith(('[untrusted prior action and host Observation]',
                '[untrusted complete review Observation; exact duplicates may reference included text]')):
            failed = observation_failed(m.content)
            envelope = observation_envelope(m.content) or {}
            essential = envelope.get('tool') in required_tools or envelope.get('tool') == 'context.read_material'
            call = envelope.get('native_call')
            latest = m.tool_call_id in latest_call_ids or (
                isinstance(call, dict) and call.get('id') in latest_call_ids)
            # Keep the evidence needed to author and review the method, including
            # explicit readbacks. Offloading it again causes repeated API calls.
            # Observation-history mode renders each result as a separate user
            # message, so the packer cannot infer the latest batch's boundary.
            materials[key] = Material('tool', 'actual tool observation',
                                      failed or essential or latest or bool(options.get('reviewing')))
        elif m.content.startswith(('[untrusted current candidate', '[untrusted action receipt index')):
            materials[key] = Material('history', 'active candidate and receipt index', True)
    messages, manifest = pack_messages(full, policy=policy, budget=budget, tools=tools,
        materials=materials, root=root, previous=previous, agent=agent, readback_available=readback_available,
        deduplicate_repository=source_policy.enabled)
    manifest['estimated_upper_bound_tokens'] = manifest['used'] - manifest['tool_schema_upper_bound_tokens']
    manifest['total_input_upper_bound_tokens'] = manifest['used']
    manifest['visible_sha256'] = digest([m.to_wire() for m in messages])
    for name in ('compressed_history', 'omitted_history', 'preserved_review_history',
                 'deduplicated_review_history', 'reviewing', 'prior_review_issues_visible', 'validation_issues_visible'):
        manifest[name] = legacy[name]
    return messages, manifest
