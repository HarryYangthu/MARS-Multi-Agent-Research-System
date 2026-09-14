"""Load a project's maintained knowledge once per run, without truncation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from app.harness.agent_loop.trace import atomic_json, digest


def load_project_knowledge(project_root: Path, run_root: Path | None) -> tuple[str, dict[str, Any]]:
    config_path = project_root / "project.yaml"
    config = yaml.safe_load(config_path.read_text()) if config_path.is_file() else {}
    name = (config or {}).get("knowledge_file")
    if not name:
        return "", {}
    source = (project_root / str(name)).resolve()
    if not source.is_relative_to(project_root.resolve()):
        raise ValueError("project knowledge must be inside its project")
    snapshot = run_root / "input/project_knowledge.v1.json" if run_root else None
    if snapshot and snapshot.exists():
        record = json.loads(snapshot.read_text())
        if (not isinstance(record, dict) or record.get("schema") != "project.knowledge.v1" or record.get("project") != project_root.name
                or record.get("source") != str(name) or not isinstance(record.get("content"), str)
                or record.get("sha256") != digest(record["content"])):
            raise ValueError("project knowledge snapshot is invalid")
        return record["content"], record
    content = source.read_text(encoding="utf-8")
    if not content.strip():
        raise ValueError("required project knowledge is empty")
    record = {"schema": "project.knowledge.v1", "project": project_root.name,
              "source": str(name), "sha256": digest(content), "content": content}
    if snapshot:
        atomic_json(snapshot, record)
    return content, record
