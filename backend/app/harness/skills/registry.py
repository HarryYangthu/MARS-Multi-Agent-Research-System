"""Load selected local skills and freeze their versions, permissions and checks."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.settings import repo_root


@dataclass(frozen=True)
class SkillSelection:
    context: str
    manifest: dict[str, Any]


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _strings(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError(f"{label} must be a list of non-empty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{label} contains duplicates")
    return value


def load_selected_skills(
    skill_ids: Sequence[str], *, granted_tools: Sequence[str], project: str,
    registry_path: Path | None = None,
) -> SkillSelection:
    """Resolve explicit IDs (or ID@version) without enabling additional tools.

    Paths are relative to the registry's directory, must remain beneath it, and
    may not escape through symlinks. The manifest is suitable for run snapshots;
    callers must bind it to their invocation before invoking a model or resuming.
    """
    if isinstance(skill_ids, (str, bytes)):
        raise ValueError("skills must be an explicit list of IDs")
    selected = _strings(list(skill_ids), "skills")
    path = registry_path or repo_root() / "configs" / "skills.yaml"
    if not selected:
        return SkillSelection("", {"schema": "skills.selection.v1", "skills": [], "sha256": _digest([])})
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("skills"), dict):
        raise ValueError("skill registry must declare version 1 and a skills mapping")
    entries: list[dict[str, Any]] = []
    sections: list[str] = []
    seen: set[str] = set()
    for selection in selected:
        skill_id, separator, requested_version = selection.partition("@")
        if skill_id in seen:
            raise ValueError(f"skill selected more than once: {skill_id}")
        seen.add(skill_id)
        definition = raw["skills"].get(skill_id)
        if not isinstance(definition, dict):
            raise ValueError(f"unknown skill: {skill_id}")
        version = definition.get("version")
        if not isinstance(version, str) or not version.strip():
            raise ValueError(f"skill {skill_id} requires a string version")
        if separator and requested_version != version:
            raise ValueError(f"skill version unavailable: {selection}")
        allowed_projects = _strings(definition.get("projects", []), "skill projects")
        if allowed_projects and project not in allowed_projects:
            raise ValueError(f"skill {skill_id} is not available for project {project}")
        required = _strings(definition.get("required_tools", []), "skill required_tools")
        missing = set(required) - set(granted_tools)
        if missing:
            raise ValueError(f"skill {skill_id} requires ungranted tools: {sorted(missing)}")
        source = definition.get("instructions")
        if not isinstance(source, str) or not source:
            raise ValueError(f"skill {skill_id} must name an instructions file")
        relative = Path(source)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("skill instructions must remain beneath the registry directory")
        target = (path.parent / relative).resolve()
        if not target.is_relative_to(path.parent.resolve()):
            raise ValueError("skill instruction symlink escapes registry directory")
        content = target.read_text(encoding="utf-8")
        if not content.strip() or len(content.encode()) > 100_000:
            raise ValueError(f"skill {skill_id} instructions must contain at most 100KB of nonempty text")
        checks = definition.get("acceptance", {})
        if not isinstance(checks, dict) or set(checks) - {"output_schemas", "required_tool_successes"}:
            raise ValueError(f"skill {skill_id} has unsupported acceptance checks")
        schemas = _strings(checks.get("output_schemas", []), "skill output_schemas")
        successful_tools = _strings(checks.get("required_tool_successes", []), "skill required_tool_successes")
        if set(successful_tools) - set(required):
            raise ValueError("skill acceptance tools must be declared in required_tools")
        entry = {"id": skill_id, "version": version, "instructions_ref": source,
                 "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                 "required_tools": required, "projects": allowed_projects,
                 "acceptance": {"output_schemas": schemas, "required_tool_successes": successful_tools},
                 "definition_sha256": _digest(definition)}
        entries.append(entry)
        sections.append(f"[Selected skill {skill_id}@{version}; procedural reference]\n{content}")
    manifest = {"schema": "skills.selection.v1", "skills": entries, "sha256": _digest(entries)}
    return SkillSelection("\n\n".join(sections), manifest)


def skill_acceptance_errors(
    selection: SkillSelection, *, output_schema: str,
    observations: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Check selected skill obligations using host observations, not model claims."""
    errors: list[str] = []
    successful = {o.get("tool") for o in observations if o.get("ok") is True}
    for entry in selection.manifest["skills"]:
        checks = entry["acceptance"]
        if checks["output_schemas"] and output_schema not in checks["output_schemas"]:
            errors.append(f"skill {entry['id']}: output schema {output_schema} is not allowed")
        for name in checks["required_tool_successes"]:
            if name not in successful:
                errors.append(f"skill {entry['id']}: successful tool observation required: {name}")
    return errors
