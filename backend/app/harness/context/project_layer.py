"""Project layer of the 3-layer context.

Reads ``projects/<name>/{AGENTS.md, project.yaml, repo_link.yaml}`` plus
``projects/<name>/context/*.md`` and returns a structured slice for context
loading + Manifest writes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.settings import repo_root


@dataclass
class ProjectLayer:
    project: str
    agents_md: str
    project_yaml: dict[str, Any] = field(default_factory=dict)
    repo_link: dict[str, Any] = field(default_factory=dict)
    context_docs: tuple[tuple[str, str], ...] = ()
    code_summary: str = ""
    history_summary: str = ""

    def render(self) -> str:
        parts = [f"## Project: {self.project}"]
        if self.project_yaml.get("description"):
            parts.append(self.project_yaml["description"])
        if self.agents_md:
            parts.append("### Project AGENTS.md (excerpt)")
            parts.append(self.agents_md[:4000])
        for name, content in self.context_docs:
            parts.append(f"### Project context: {name}")
            parts.append(content[:6000])
        if self.code_summary:
            parts.append("### Code summary")
            parts.append(self.code_summary)
        if self.history_summary:
            parts.append("### History summary")
            parts.append(self.history_summary)
        return "\n\n".join(parts)


def project_root(project: str) -> Path:
    return repo_root() / "projects" / project


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def build_project_layer(*, project: str) -> ProjectLayer:
    root = project_root(project)
    agents_md_path = root / "AGENTS.md"
    project_yaml = _read_yaml(root / "project.yaml")
    repo_link = _read_yaml(root / "repo_link.yaml")
    agents_md = agents_md_path.read_text(encoding="utf-8") if agents_md_path.exists() else ""
    return ProjectLayer(
        project=project,
        agents_md=agents_md,
        project_yaml=project_yaml,
        repo_link=repo_link,
        context_docs=_read_context_docs(root / "context"),
    )


def _read_context_docs(context_dir: Path) -> tuple[tuple[str, str], ...]:
    if not context_dir.exists():
        return ()
    docs: list[tuple[str, str]] = []
    for path in sorted(context_dir.glob("*.md")):
        if not path.is_file():
            continue
        docs.append((path.name, path.read_text(encoding="utf-8")))
    return tuple(docs)
