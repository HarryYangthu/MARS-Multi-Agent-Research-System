"""A read-only index of actual run artifacts and application exports, with bounded text previews."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from mars_app import artifacts

# Names of the records the core writes (stems), as the researcher knows them.
LABELS = {"contract": "研究约定", "report": "内核报告", "analysis": "分析", "analysis_kb_context": "知识库上下文",
          "research_log": "调研记录", "search_log": "检索记录", "sources": "文献来源", "cards": "证据卡",
          "proposals": "提案", "critiques": "质询", "debate": "辩论", "judgements": "评审", "judged_proposals": "评审后的提案",
          "ranking": "排序", "handoff": "交接包", "user_proposal": "用户方案", "routes_final": "路线终态",
          "record": "本轮记录", "diff": "代码差异", "codingagent_summary": "编码过程摘要", "notes": "留言", "followups": "补充实验"}
STAGES = {"baseline_": "基线 · ", "screen": "筛选", "verify": "完整验收", "quick": "快速训练"}
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".md", ".txt", ".json", ".yaml", ".yml", ".docx", ".pptx", ".xlsx", ".csv", ".pdf"}


def index(runs: Path, reports: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    def add(path: Path, root: Path, run_id: str, prefix: str) -> None:
        if path.suffix.lower() not in EXTENSIONS or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            return
        relative = path.relative_to(root).as_posix()
        # Large raw agent/model transcripts are outside the public artifact view contract.
        if any(p in {"codingagent", "zcode", "checkpoints", ".git"} for p in path.relative_to(root).parts):
            return
        suffix = path.suffix.lower()
        kind = "fig" if suffix in {".png", ".jpg", ".jpeg", ".webp"} else "report" if suffix in {".md", ".docx", ".pptx", ".xlsx", ".pdf"} else "record"
        match = re.search(r"step2/([^/]+)/i(\d+)/", relative)
        route, iteration = (match[1], int(match[2])) if match else (None, None)
        source = f"{run_id} · {route} · {iteration}" if route else run_id
        label = LABELS.get(path.stem, path.stem.replace("_", " "))
        if path.stem.startswith(("screen", "verify", "baseline_")):  # screen_<dataset>_<file> → 筛选 · <dataset> · <file>
            stem = path.stem
            prefix = "基线 · " if stem.startswith("baseline_") else ""
            stem = stem.removeprefix("baseline_")
            stage, _, rest = stem.partition("_")
            label = prefix + " · ".join([STAGES.get(stage, stage), *[x for x in rest.split("_", 1) if x]])
        step1 = re.search(r"step1/(r\d+)/", relative)
        title = f"{route} · 第 {iteration} 轮 · {label}" if route else f"{step1[1]} · {label}" if step1 else label
        stat = path.stat()
        items.append({"id": hashlib.sha256(f"{prefix}:{relative}".encode()).hexdigest()[:24], "kind": kind,
                      "title": title, "run_id": run_id, "route": route, "iteration": iteration, "source": source,
                      "step1_round": step1[1] if step1 else None,
                      "path": str(path), "relative_path": f"{prefix}/{relative}", "size": stat.st_size,
                      "modified": stat.st_mtime, "extension": suffix, "virtual": False})
    if runs.is_dir():
        for run in sorted(runs.iterdir(), reverse=True):
            if not artifacts.is_run_dir(run) or not run.resolve().is_relative_to(runs.resolve()):
                continue
            for directory, folders, files in os.walk(run, followlinks=False):
                folders[:] = [name for name in folders if name not in {"codingagent", "zcode", "checkpoints", ".git", "__pycache__"}]
                for name in files:
                    add(Path(directory) / name, runs, run.name, "runs")
            for path in sorted((run / "step2").glob("*/i*/record.json")):
                if not path.resolve().is_relative_to(runs.resolve()):
                    continue
                row = artifacts._json(path, {}) or {}
                if not row.get("commit") or not row.get("diff_files"):
                    continue
                route, iteration = path.parent.parent.name, row.get("iteration")
                items.append({"id": f"diff:{run.name}:{route}:{iteration}", "kind": "code",
                              "title": f"{route} · 第 {iteration} 轮 · 代码改动", "run_id": run.name,
                              "route": route, "iteration": iteration, "source": f"{run.name} · {route} · {iteration}",
                              "path": ", ".join(row["diff_files"]), "relative_path": path.relative_to(runs).as_posix(),
                              "size": None, "modified": path.stat().st_mtime, "extension": ".patch", "virtual": True,
                              "commit": row["commit"]})
    if reports.is_dir():
        for path in reports.rglob("*"):
            relative = path.relative_to(reports)
            add(path, reports, relative.parts[0] if len(relative.parts) > 1 else "", "reports")
    return sorted(items, key=lambda item: item["modified"], reverse=True)


def find(items: list[dict[str, Any]], aid: str) -> dict[str, Any]:
    item = next((x for x in items if x["id"] == aid), None)
    if item is None:
        raise ValueError("产物不存在或已被移动")
    return item


def preview(item: dict[str, Any]) -> dict[str, Any]:
    path = Path(item["path"])
    if item["kind"] == "fig":
        return {"format": "image"}
    if item["extension"] in {".pdf", ".docx", ".pptx", ".xlsx"}:
        return {"format": "download", "text": "请下载或用系统应用打开此文件。"}
    limit = 256 * 1024
    raw = path.read_bytes()[:limit + 1]
    text = raw[:limit].decode("utf-8", errors="replace")
    if item["extension"] == ".json" and len(raw) <= limit:
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except ValueError:
            pass
    return {"format": "markdown" if item["extension"] == ".md" else "text", "text": text, "truncated": len(raw) > limit}


def overview(runs: Path) -> dict[str, Any]:
    stats = {"runs": 0, "routes": 0, "iterations": 0, "sources": 0, "passed": 0}
    latest: dict[str, Any] = {}
    if not runs.is_dir():
        return {"stats": stats, "contract": latest, "datasets": []}
    datasets: set[str] = set()
    dirs = sorted((p for p in runs.iterdir() if artifacts.is_run_dir(p)), key=lambda p: (p / "contract.yaml").stat().st_mtime, reverse=True)
    for run in dirs:
        spec = artifacts.contract(run)
        if not latest:
            latest = spec
        stats["runs"] += 1
        stats["routes"] += len([p for p in (run / "step2").glob("[A-Z]*") if p.is_dir()])
        for file in (run / "step1").glob("r*/sources.json"):
            sources = artifacts._json(file, [])
            stats["sources"] += len(sources) if isinstance(sources, list) else 0
        for file in (run / "step2").glob("*/i*/record.json"):
            stats["iterations"] += 1
            evidence = (artifacts._json(file, {}) or {}).get("evidence") or {}
            verified = evidence.get("verify") or {}
            stats["passed"] += int(verified.get("passed") is True)
            datasets.update(k for k in verified if k.isdigit())
            datasets.update(k for k in (evidence.get("screen") or {}) if k.isdigit())
    return {"stats": stats, "contract": latest, "datasets": sorted(datasets, key=int)}


def compute_summary(runs: Path) -> dict[str, Any]:
    usage = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "known_calls": 0, "unknown_calls": 0}
    recent = []
    if not runs.is_dir():
        return {"usage": usage, "recent": recent}
    for run in runs.iterdir():
        if not artifacts.is_run_dir(run):
            continue
        calls = run / "llm_calls.jsonl"
        if calls.is_file():
            for line in calls.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    call = json.loads(line)
                except ValueError:
                    continue
                known = isinstance(call.get("input_tokens"), int) and isinstance(call.get("output_tokens"), int)
                usage["known_calls" if known else "unknown_calls"] += 1
                if known:
                    for key in ("input_tokens", "output_tokens", "cached_tokens"):
                        value = call.get(key)
                        if isinstance(value, int):
                            usage[key] += value
        for path in (run / "step2").glob("*/i*/record.json"):
            row = artifacts._json(path, {}) or {}
            evidence = row.get("evidence") or {}
            for stage in ("verify", "screen", "prequick"):
                passed = (evidence.get("verify") or {}).get("passed") if stage == "verify" else evidence.get("screen_passed") if stage == "screen" else (evidence.get("prequick") or {}).get("passed")
                if isinstance(passed, bool):
                    recent.append({"run_id": run.name, "route": path.parent.parent.name, "iteration": row.get("iteration"),
                                   "stage": stage, "passed": passed, "modified": path.stat().st_mtime})
    return {"usage": usage, "recent": sorted(recent, key=lambda row: row["modified"], reverse=True)[:12]}
