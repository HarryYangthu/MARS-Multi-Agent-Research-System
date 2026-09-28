"""Persist the actual outgoing payload, not a separately reconstructed preview."""
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import re
import json
import uuid

from app.harness.agent_loop.trace import atomic_json, digest
from app.harness.context.runtime_pack import store_material
from app.harness.llm.provider_base import Message


def record_manifest(root: Path, *, agent: str, node: str, project: str, messages: list[Message],
                    tools: tuple[dict[str, Any], ...], manifest: dict[str, Any], purpose: str = 'runtime') -> Path:
    safe = re.sub('[^a-zA-Z0-9_-]', '_', agent)
    identifier = f'context_manifest.v2.{safe}.{uuid.uuid4().hex}'
    payload = {'messages': [m.to_wire() for m in messages], 'tools': tools}
    payload_hash = digest(payload)
    payload_ref = store_material(root, Message('user', json.dumps(payload, ensure_ascii=False)))
    native_segments = manifest.get('segments', [])
    segments = [{**item, 'title': item['source'], 'source_ref': item['source'],
                 'priority': 'critical' if item['protected'] else 'medium',
                 'selection_reason': 'protected input' if item['protected'] else 'task material',
                 'compression': 'reference' if item['compression'] else 'none',
                 'tokens_estimated': item['tokens_upper_bound'], 'risk_flags': [],
                 'content_hash': item['id'], 'text_preview': '',
                 'raw_ref': ('materials/' + item['refs'][0] + '.json') if item['refs'] else None}
                for item in native_segments]
    raw = {'schema': 'context_manifest.v2', 'manifest_id': identifier, 'run_id': root.name,
           'agent': agent, 'node_key': node, 'project': project, 'purpose': purpose,
           'created_at': datetime.now(timezone.utc).isoformat(), 'output_schema': '',
           'budget': {'max': manifest['budget'], 'target': manifest['target'],
                      'used': manifest['used'], 'over_budget': False},
           'segments': segments, 'render_order': [s['id'] for s in segments],
           'messages_preview': [{'role': m.role, 'content': m.content[:1200]} for m in messages],
           'raw_refs': [f'materials/{payload_ref}.json'],
           'diagnostics': {'runtime_context': manifest, 'payload_sha256': payload_hash,
                           'payload_ref': payload_ref, 'source': 'actual_pre_call_payload', 'provider_usage': None}}
    path = root / 'context/agents' / safe / 'manifests' / f'{identifier}.json'
    atomic_json(path, raw)
    return path


def record_usage(path: Path, usage: Any) -> None:
    import json
    raw = json.loads(path.read_text())
    raw['diagnostics']['provider_usage'] = usage if isinstance(usage, dict) else None
    atomic_json(path, raw)
