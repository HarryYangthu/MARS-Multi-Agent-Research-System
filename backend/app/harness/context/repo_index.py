"""Bounded metadata-only repository map; file reads remain tool-mediated."""
from __future__ import annotations

import os
from typing import Any

from app.harness.tools.project_repo import load_project_repo, resolve_allowed_path, TEXT_SUFFIXES


def repository_index(project: str, limit: int) -> dict[str, Any]:
    repo = load_project_repo(project)
    files: list[str] = []
    if repo.scope is not None:
        candidates = iter(repo.scope.readable_files)
    else:
        def walk() -> Any:
            visited = 0
            for directory, names, entries in os.walk(repo.root, followlinks=False):
                names[:] = sorted(n for n in names if not n.startswith('.') and n not in
                                  {'node_modules', '__pycache__', 'runs', 'checkpoints', 'venv', 'data'})
                for name in sorted(entries):
                    visited += 1
                    if visited > limit * 20:
                        return
                    yield (os.path.relpath(directory, repo.root) + '/' + name).removeprefix('./')
        candidates = walk()
    for name in candidates:
        try:
            path = resolve_allowed_path(repo, name, require_exists=True, require_text=True)
            if path.is_file() and path.suffix in TEXT_SUFFIXES:
                files.append(name)
        except ValueError:
            continue
        if len(files) >= limit:
            break
    return {'files': files, 'limit': limit, 'complete': False, 'contents_loaded': False,
            'read_only': repo.read_only, 'note': 'Bounded filename index only; absence does not prove no matching file.'}
