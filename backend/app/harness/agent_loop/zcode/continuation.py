"""Explicit host-authored continuation of a completed ZCode turn.

Interrupted invocations still use exact fingerprint recovery. A new, linked turn
may consume execution feedback, but cannot change runtime, model or tool rights.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def safe_trace(root: Path, reference: str) -> Path:
    parts = Path(reference).parts
    path = root / reference
    base = root / 'agent_traces/coding'
    if (len(parts) != 3 or parts[:2] != ('agent_traces', 'coding') or parts[-1] in {'.', '..'}
            or base.resolve() != root.resolve() / 'agent_traces/coding'
            or path.resolve() != base.resolve() / parts[-1]
            or Path(reference).is_absolute() or not path.resolve().is_relative_to(base.resolve())
            or path.parent.resolve() != base.resolve() or path.is_symlink()):
        raise ValueError('ZCode continuation must reference this run\'s coding trace')
    return path


def continue_state(root: Path, link: dict[str, Any], compatibility: str) -> dict[str, Any]:
    source = safe_trace(root, str(link['trace_ref']))
    checkpoint = source / 'checkpoint.json'
    payload = checkpoint.read_bytes()
    if hashlib.sha256(payload).hexdigest() != link['checkpoint_sha256']:
        raise ValueError('ZCode continuation source changed')
    saved = json.loads(payload)
    if (saved.get('backend') != 'zcode' or saved.get('status') != 'passed'
            or saved.get('pending') is not None or not saved.get('session_id', '').startswith('sess_')
            or saved.get('continuation_compatibility') != compatibility):
        raise ValueError('ZCode continuation requires a completed compatible session')
    from app.harness.agent_loop.trace import audit_trace
    if not audit_trace(source)['consistent']:
        raise ValueError('ZCode continuation trace is inconsistent')
    owner = safe_trace(root, str(saved.get('session_owner_ref') or link['trace_ref']))
    runtime = owner / 'zcode_runtime'
    if not runtime.is_dir() or runtime.resolve() != owner.resolve() / 'zcode_runtime':
        raise ValueError('ZCode session storage is unavailable; cannot pretend to resume its context')
    return {'session_id': saved['session_id'], 'session_owner_ref': owner.relative_to(root).as_posix(),
            'continuation_source': dict(link)}
