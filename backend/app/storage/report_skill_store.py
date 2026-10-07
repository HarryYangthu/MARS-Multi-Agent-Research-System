"""Import immutable instruction skills and explicitly select them per project."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml
from app.harness.persistence import atomic_write_text, path_lock
from app.harness.schema.frontmatter_parser import parse
from app.harness.skills.registry import load_selected_skills
from app.settings import repo_root


def registry_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "configs/skills.yaml"


def selected_report_skills(project: str, *, root: Path | None = None) -> list[str]:
    path = (root or repo_root()) / "configs/report_skill_selection.yaml"
    raw = _mapping(path)
    projects = raw.get("projects", {})
    if not isinstance(projects, dict):
        raise ValueError("报告 skill 选择记录无效")
    value = projects.get(project, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("报告 skill 选择记录无效")
    return value


def import_report_skill(content: str, *, root: Path | None = None) -> dict[str, Any]:
    if not content.strip() or len(content.encode()) > 100_000:
        raise ValueError("SKILL.md 必须为非空文本，且不超过 100 KB")
    try:
        parsed = parse(content)
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError("SKILL.md 顶部 YAML 格式不正确") from exc
    name, description = parsed.metadata.get("name"), parsed.metadata.get("description")
    if not isinstance(name, str) or not name.strip() or len(name) > 120 or not isinstance(description, str) or not description.strip():
        raise ValueError("SKILL.md 顶部须包含 name、description，后面填写报告写作指令")
    if not parsed.body.strip():
        raise ValueError("SKILL.md 缺少写作指令正文")
    required = parsed.metadata.get("required_tools", [])
    if not isinstance(required, list) or any(not isinstance(tool, str) for tool in required):
        raise ValueError("required_tools 必须为工具名称列表")
    digest = hashlib.sha256(content.encode()).hexdigest()
    slug = re.sub(r"[^a-z0-9_-]+", "_", name.lower()).strip("_") or "custom"
    identity = f"report_{slug[:40]}_{digest[:12]}"
    path = registry_path(root)
    relative = f"skills/imported/{digest}/SKILL.md"
    with path_lock(path.with_suffix(".lock")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {"version": 1, "skills": {}}
        if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("skills"), dict):
            raise ValueError("skill 注册表损坏，未覆盖现有配置")
        target = (path.parent / relative).resolve()
        if not target.is_relative_to(path.parent.resolve()):
            raise ValueError("skill 存储路径越界")
        if target.exists() and target.read_text(encoding="utf-8") != content:
            raise ValueError("skill 已有内容与校验值不一致")
        atomic_write_text(target, content)
        raw["skills"][identity] = {"version": digest[:12], "name": name, "description": description,
            "instructions": relative, "required_tools": required, "projects": [],
            "acceptance": {"output_schemas": ["report.v1"], "required_tool_successes": []}}
        atomic_write_text(path, yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    return {"id": identity, "version": digest[:12], "name": name, "description": description}


def report_skill_options(project: str, tools: list[str], *, root: Path | None = None) -> dict[str, Any]:
    path = registry_path(root)
    raw = _mapping(path)
    skills = raw.get("skills", {})
    if not isinstance(skills, dict):
        raise ValueError("skill 注册表格式不正确")
    options: list[dict[str, Any]] = []
    for identity, entry in skills.items():
        if not isinstance(identity, str) or not isinstance(entry, dict) or not isinstance(entry.get("acceptance", {}), dict):
            raise ValueError("skill 注册表格式不正确")
        schemas = entry.get("acceptance", {}).get("output_schemas", [])
        if schemas and "report.v1" not in schemas:
            continue
        try:
            selection = load_selected_skills([identity], granted_tools=tools, project=project, registry_path=path)
            reason, available = "", True
            version = selection.manifest["skills"][0]["version"]
        except (ValueError, OSError) as exc:
            reason, available, version = str(exc), False, entry.get("version", "")
        options.append({"id": identity, "version": version, "name": entry.get("name", identity),
            "description": entry.get("description", "报告写作方法"), "available": available, "reason": reason,
            "required_tools": entry.get("required_tools", [])})
    return {"options": options, "selected": selected_report_skills(project, root=root), "project": project}


def select_report_skills(project: str, ids: list[str], tools: list[str], *, root: Path | None = None) -> dict[str, Any]:
    path = registry_path(root)
    selection = load_selected_skills(ids, granted_tools=tools, project=project, registry_path=path)
    for entry in selection.manifest["skills"]:
        schemas = entry["acceptance"]["output_schemas"]
        if schemas and "report.v1" not in schemas:
            raise ValueError("此 skill 不适用于报告写作")
    frozen = [f"{entry['id']}@{entry['version']}" for entry in selection.manifest["skills"]]
    preferences = path.parent / "report_skill_selection.yaml"
    with path_lock(preferences.with_suffix(".lock")):
        raw = _mapping(preferences)
        projects = raw.setdefault("projects", {})
        if not isinstance(projects, dict):
            raise ValueError("报告 skill 选择记录无效，未覆盖现有配置")
        projects[project] = frozen
        atomic_write_text(preferences, yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    return report_skill_options(project, tools, root=root)


def _mapping(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    except yaml.YAMLError as exc:
        raise ValueError("报告 skill 配置 YAML 格式不正确") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("报告 skill 配置必须为映射")
    return raw
