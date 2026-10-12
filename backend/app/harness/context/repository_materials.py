"""Verified per-run source snapshots, shared by readers without bypassing scope checks."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import hashlib
import json
import os

import yaml

from app.harness.agent_loop.trace import atomic_json, digest
from app.harness.context.runtime_pack import message_key, read_material, store_material
from app.harness.llm.provider_base import Message
from app.harness.persistence import path_lock
from app.harness.runtime.project_scope import safe_scope_path
from app.settings import repo_root


@dataclass(frozen=True)
class RepositoryPolicy:
    enabled: bool
    max_cached_file_bytes: int
    max_index_files: int
    repeat_warning_after: int


def repository_policy() -> RepositoryPolicy:
    raw = yaml.safe_load((repo_root() / 'configs/context.yaml').read_text(encoding='utf-8'))
    values = raw['repository_materials']
    if type(values.get('enabled')) is not bool:
        raise ValueError('repository_materials.enabled must be boolean')
    for key in ('max_cached_file_bytes', 'max_index_files', 'repeat_warning_after'):
        if type(values.get(key)) is not int or values[key] <= 0:
            raise ValueError(f'invalid repository_materials.{key}')
    return RepositoryPolicy(**values)


def source_window(target: Path, repository: Path, relative: str, *, offset: int, limit: int,
                  run_root: Path | None, policy: RepositoryPolicy) -> dict[str, Any]:
    # Revalidate actual bytes on every read, including uncommitted edits with
    # unchanged size/mtime. The cache saves archival duplication and context;
    # it does not claim to eliminate filesystem reads.
    raw = target.read_bytes()
    text = raw.decode('utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
    if type(offset) is not int or offset < 0 or (text and offset >= len(text)):
        raise ValueError('char_offset must identify a character inside the file')
    end = min(len(text), offset + limit)
    byte_sha = hashlib.sha256(raw).hexdigest()
    result: dict[str, Any] = {
        'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
        'source_bytes_sha256': byte_sha, 'repo_root': str(repository), 'path': relative,
        'truncated': end < len(text), 'char_start': offset, 'char_end': end,
        'total_chars': len(text), 'next_offset': end if end < len(text) else None,
        'content': text[offset:end],
    }
    if not policy.enabled or run_root is None or len(raw) > policy.max_cached_file_bytes:
        return result
    run_root.mkdir(parents=True, exist_ok=True)
    identity = {'repo_root': os.path.normcase(str(repository.resolve())),
                'path': relative, 'source_bytes_sha256': byte_sha}
    key = digest(identity)
    path = safe_scope_path(run_root, f'context/repository/{key}.json')
    lock = safe_scope_path(run_root, f'context/repository/{key}.lock')
    source = Message('user', text)
    ref = message_key(source)
    record = {**identity, 'material_ref': ref, 'sha256': result['sha256'], 'total_chars': len(text)}
    with path_lock(lock):
        # A reference is never considered a hit unless the original is present
        # and its hash is verified. Corrupt evidence fails explicitly.
        hit = path.is_file() and safe_scope_path(run_root, f'context/materials/{ref}.json').is_file()
        if path.is_file() and json.loads(path.read_text(encoding='utf-8')) != record:
            raise ValueError('repository cache identity mismatch')
        store_material(run_root, source)
        cached = read_material(run_root, ref, offset, limit)
        if cached['content'] != text[offset:end]:
            raise ValueError('repository cache content mismatch')
        if not hit:
            atomic_json(path, record)
    result['content'] = cached['content']
    result['cache'] = {'hit': hit, 'key': key, 'material_ref': ref,
                       'source_verified': True, 'scope': 'this run; shared across agents',
                       'note': 'material_ref is a historical source snapshot, not a live file check'}
    return result
