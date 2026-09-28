"""Planning preview uses the runtime assembler/packer; no provider is called.

A preview cannot predict future tool observations or role-specific runtime schema
extensions. Actual outgoing manifests remain the authoritative execution record.
"""
from typing import Any
import json

from app.harness.context.folder_context import load_folder_context, is_context_template
from app.harness.context.runtime_assembly import assemble_materials
from app.harness.context.runtime_manifest import manifest_segments
from app.harness.context.runtime_pack import Material, pack_messages
from app.harness.context.runtime_policy import load_policy, role_profile, input_budget
from app.harness.llm.model_registry import get_agent_config, configured_context_window
from app.harness.llm.provider_base import Message
from app.harness.tools.registry import get_registry
from app.settings import repo_root


def preview_runtime_context(agent: str, project: str, task: str, upstream: dict[str, str]) -> dict[str, Any]:
    cfg = get_agent_config(agent)
    policy = load_policy()
    folder = load_folder_context(project)
    references = []
    rules = ''
    if folder:
        rules = '\n'.join(f['content'] for f in folder['files'] if f['role'] == 'instructions' and not is_context_template(f))
        references = [{'source': f['path'], 'text': f['content']} for f in folder['files']
                      if f['role'] == 'reference' and not is_context_template(f)]
    elif (repo_root() / 'projects' / project / 'AGENTS.md').is_file():
        from app.harness.project_workspace import project_root
        rules = (project_root(project) / 'AGENTS.md').read_text()
    metadata: dict[str, Any] = {'runtime_policy': policy, 'references': references,
        'original_user_request': task, 'code_profile': role_profile(policy, agent)}
    system = f'MARS {agent}. Planning preview; runtime role instructions may add requirements.'
    base = [Message('system', system), Message('system', rules), Message('user', task)]
    if cfg.output_schema:
        schema_path = repo_root() / 'backend/app/harness/schema/schemas' / f'{cfg.output_schema}.json'
        base.insert(2, Message('system', schema_path.read_text()))
    messages = assemble_materials(base, upstream, metadata)
    tools = tuple({'type': 'function', 'function': {'name': name.replace('.', '_'),
                   'description': spec.description, 'parameters': spec.input_schema}}
                  for name in dict.fromkeys((*cfg.tools, 'context.read_material'))
                  if (spec := get_registry().spec(name)) is not None)
    # No originals are persisted by a preview, so it never pretends references
    # can be expanded. Capacity errors are explicit instead of hidden clipping.
    packed, manifest = pack_messages(messages, policy=policy,
        budget=input_budget(policy, int(cfg.raw.get('loop', {}).get('input_token_budget', policy['input_budget'])),
                            output_reserve=cfg.max_tokens, model_window=configured_context_window(cfg)), tools=tools,
        materials={k: Material(**v) for k,v in metadata['materials'].items()},
        agent=agent, readback_available=False)
    segments = manifest_segments(manifest)
    return {'schema': 'context_manifest.v2', 'manifest_id': 'planning-preview', 'run_id': 'preview',
        'agent': agent, 'node_key': agent, 'project': project, 'output_schema': cfg.output_schema,
        'purpose': 'planning_preview', 'created_at': '',
        'budget': {'max': manifest['budget'], 'target': manifest['target'], 'used': manifest['used'], 'over_budget': False},
        'segments': segments, 'render_order': [s['id'] for s in segments], 'raw_refs': [],
        'messages_preview': [{'role': m.role, 'content': m.content[:1200]} for m in packed],
        'diagnostics': {'runtime_context': manifest, 'source': 'planning_preview_not_sent',
                        'provider_usage': None, 'warnings': ['runtime_role_and_observations_not_yet_available']}}
