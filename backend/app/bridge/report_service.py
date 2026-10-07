"""Product boundary for report conversion, downloads and Writing skill selection."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from app.harness.llm.model_registry import get_agent_config
from app.harness.runtime.project_scope import safe_scope_path
from app.harness.tools.config import load_tool_configs
from app.reporting import generate_report_bundle, read_latest_report_bundle
from app.storage.run_store import RunHandle
from app.storage.report_skill_store import import_report_skill, report_skill_options, select_report_skills


def writing_tools() -> list[str]:
    enabled = load_tool_configs()
    return [name for name in get_agent_config("writing").tools if name in enabled and enabled[name].enabled
            and not enabled[name].bridge_only
            and (not enabled[name].allowed_agents or "writing" in enabled[name].allowed_agents)]


def get_bundle(run: RunHandle) -> dict[str, Any]:
    bundle = read_latest_report_bundle(run) or {"exists": False, "run_id": run.run_id}
    bundle["report_ready"] = (run.subdir("writing") / "research_report.approved.md").is_file()
    return bundle


def export_bundle(run: RunHandle) -> dict[str, Any]:
    if not (run.subdir("writing") / "research_report.approved.md").is_file():
        raise ValueError("请先审核并批准研究报告，再生成导出文件")
    return generate_report_bundle(run, actor="api")


def download_path(run: RunHandle, filename: str, manifest: str | None = None) -> Path:
    from app.harness.schema.frontmatter_parser import parse
    if Path(filename).name != filename:
        raise ValueError("下载文件名无效")
    bundle = get_bundle(run)
    if manifest:
        if not re.fullmatch(r"writing/report_bundle\.v\d+\.md", manifest):
            raise ValueError("报告清单路径无效")
        ref = safe_scope_path(run.root, manifest, must_exist=True)
        bundle = {"metadata": parse(ref.read_text(encoding="utf-8")).metadata}
    for item in bundle.get("metadata", {}).get("deliverables", []):
        if item.get("status") != "completed" or Path(item.get("path", "")).name != filename:
            continue
        path = safe_scope_path(run.root, item["path"], must_exist=True)
        if not item.get("sha256") or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("导出文件完整性校验失败，请重新生成")
        return path
    raise FileNotFoundError("此格式尚未成功生成")


def get_skills(run: RunHandle) -> dict[str, Any]:
    return report_skill_options(run.project, writing_tools())


def import_skill(run: RunHandle, content: str) -> dict[str, Any]:
    imported = import_report_skill(content)
    return {"imported": imported, **get_skills(run)}


def choose_skills(run: RunHandle, ids: list[str]) -> dict[str, Any]:
    return select_report_skills(run.project, ids, writing_tools())
