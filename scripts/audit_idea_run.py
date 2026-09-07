"""Audit an actual Idea run from its recorded events, source files and final artifact.

No model call or simulated execution is performed. Derived reports never rewrite
the raw trace. A passing report covers method-proposal material, not experiments.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from loguru import logger

from app.agents.idea.research import evidence_inventory, material_errors
from app.harness.agent_loop.trace import atomic_json, audit_trace, digest
from app.harness.schema.frontmatter_parser import parse
from app.harness.schema.validator import validate_document


def audit_run(root: Path) -> dict[str, Any]:
    root = root.resolve()
    request = json.loads((root / "input" / "request.json").read_text())
    summary = json.loads((root / "summary.json").read_text())
    paths = list((root / "agent_traces" / "idea").glob("*/checkpoint.json"))
    if len(paths) != 1:
        raise ValueError("expected exactly one invocation checkpoint; select its run explicitly")
    trace_root = paths[0].parent
    state = json.loads(paths[0].read_text())
    audit = audit_trace(trace_root)
    events = [json.loads(line) for line in (trace_root / "events.jsonl").read_text().splitlines()]
    observations = state["history"]
    inventory = evidence_inventory(observations)
    proposal = root / "idea" / "idea_proposal.v1.md"
    errors: list[str] = []
    schema_valid = False
    material_valid = False
    metadata: dict[str, Any] = {}
    if proposal.is_file():
        text = proposal.read_text()
        validation = validate_document(text, expected_schema="proposal.v1")
        schema_valid = validation.valid
        errors.extend(f"{e.path}: {e.message}" for e in validation.errors)
        if schema_valid:
            metadata = parse(text).metadata
            requirements = request["scenario"]["requirements"]
            errors.extend(material_errors(metadata, observations,
                                          min_sources=int(requirements["min_sources"]),
                                          min_pdfs=int(requirements["min_pdfs"]),
                                          require_budget=bool(requirements["require_parameter_budget"]),
                                          max_ratio=float(requirements["max_parameter_ratio"])))
            material_valid = not errors
        if text != state["candidate"] or digest(text) != summary.get("proposal_sha256"):
            errors.append("final artifact differs from checkpoint or recorded summary hash")
    else:
        errors.append("no final proposal file")
    if state["status"] != "passed" or state["pending"] is not None:
        errors.append("loop has not reached passed with no pending operation")
    if not audit["consistent"]:
        errors.append("trace audit is inconsistent")
    reflections = [e for e in events if e["kind"] == "reflection"]
    if request["loop_policy"]["mode"] == "reflection":
        if not reflections or reflections[-1].get("accept") is not True or not state["reflection_accepted"]:
            errors.append("Reflection mode lacks a final accepting review")
    if summary.get("status") != "passed_method_proposal":
        errors.append("runner has not recorded a successful method proposal")
    if request.get("source_dirty"):
        errors.append("evaluation started with a dirty source tree")
    tool_rows = []
    for index, obs in enumerate(observations, 1):
        output = obs.get("output") or {}
        sources = output.get("sources", []) if isinstance(output, dict) else []
        tool_rows.append({"step": index, "tool": obs["tool"], "args": obs["args"],
                          "reason": obs["reason"], "ok": obs["ok"], "error": obs.get("error"),
                          "returned_hits": len(output.get("hits", [])) if isinstance(output, dict) else 0,
                          "source_results": [{k: s.get(k) for k in
                                              ("title", "url", "ok", "error", "reused", "sha256",
                                               "network_download_performed", "download_path")}
                                             for s in sources]})
    downloads = [{k: row.get(k) for k in ("title", "url", "sha256", "bytes", "pdf_pages", "download_path")}
                 for row in inventory["downloads"]]
    windows = [{"title": row.get("title"), "url": row.get("url"), "reason": row["selection_reason"],
                "pages": [{k: page.get(k) for k in ("page", "truncated", "full_page_text_chars")}
                          for page in row.get("visible_pages", [])],
                "read_receipt": row.get("read_receipt"), "full_document_read": row.get("full_document_read", False)}
               for row in inventory["reads"]]
    return {
        "run_id": summary["run_id"], "source_commit": request["source_commit"], "source_tree": request["source_tree"],
        "recorded_status": summary["status"], "loop_status": state["status"], "pending": state["pending"],
        "audit_passed": not errors, "errors": errors, "trace_consistent": audit["consistent"],
        "schema_valid": schema_valid, "material_valid": material_valid,
        "reflection_accepted": state["reflection_accepted"],
        "scientific_validated": False, "project_ready": False, "simulation_executed": False,
        "duration_seconds": summary.get("duration_seconds"), "counts": state["counts"],
        "sdk_attempt_failures": sum(e["kind"] == "sdk_attempt_failed" for e in events),
        "usage": state["usage"], "recorded_usage_complete": state["usage_complete"],
        "usage_complete": bool(state["usage_complete"] and state["pending"] != "model"
                               and not any(e["kind"] == "sdk_attempt_failed" for e in events)),
        "tool_counts": dict(Counter(o["tool"] for o in observations)), "tools": tool_rows,
        "evidence_counts": inventory["counts"],
        "search_sources": [{k: row.get(k) for k in ("title", "url", "identity", "selection_reason")}
                           for row in inventory["papers"]],
        "downloaded_sources": downloads, "read_windows": windows,
        "related_literature": metadata.get("related_literature", []),
        "parameter_budget": metadata.get("parameter_budget"),
        "validation_events": [{"valid": e["valid"], "errors": e.get("visible")} for e in events if e["kind"] == "validation"],
        "reflection_events": [e.get("visible", {"accept": e.get("accept")}) for e in reflections],
        "context_compressions": [{k: e.get(k) for k in ("compressed_history", "omitted_history", "estimated_upper_bound_tokens")}
                                 for e in events if e["kind"] == "context_packed" and
                                 (e.get("compressed_history") or e.get("omitted_history"))],
        "limits": ["Search results and PDF excerpts do not prove full reading or understanding.",
                   "Reflection is the model's self-review, not independent scientific validation.",
                   "The 1.2 parameter ratio is an evaluation assumption, not a confirmed user target.",
                   "No production baseline, real dataset or GPU experiment is included."],
    }


def render_report(report: dict[str, Any]) -> str:
    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    counts = report["counts"]
    lines = ["# Idea Agent 真实运行审计", "", f"运行：`{report['run_id']}`", "",
             f"源码：`{report['source_commit']}`；文件树：`{report['source_tree']}`", "",
             f"可复核的方法方案验收：**{report['audit_passed']}**。记录状态：{report['recorded_status']}。", "",
             f"Schema：{report['schema_valid']}；材料：{report['material_valid']}；"
             f"Reflection 接受：{report['reflection_accepted']}；trace 一致：{report['trace_consistent']}。", "",
             f"模型请求 {counts['model_requests']} 次，返回 {counts['model_responses']} 次；"
             f"SDK 尝试 {counts['sdk_attempts']} 次；工具 {counts['tool_dispatches']} 次，Observation {counts['observations']} 次。", "",
             f"协议修复 {counts['protocol_repairs']} 次，材料/Schema 修复 {counts['validation_repairs']} 次，"
             f"Reflection {counts['reflections']} 次。", "",
             f"Token 用量：{report['usage']}；记录完整：{report['usage_complete']}（不完整时仅为下界）。", "",
             "## 工具操作与选择理由", "", "| 步骤 | 工具 | 成功 | Agent 提供的行动理由 |",
             "| --- | --- | --- | --- |"]
    for row in report["tools"]:
        lines.append(f"| {row['step']} | {row['tool']} | {row['ok']} | {cell(row['reason'])} |")
    lines.extend(["", "## 实际下载与阅读范围", "", f"证据计数：{report['evidence_counts']}", ""])
    for row in report["downloaded_sources"]:
        lines.extend([f"- [{row['title']}]({row['url']})；{row['bytes']} bytes；SHA-256 `{row['sha256']}`。"])
    for row in report["read_windows"]:
        pages = ", ".join(str(p["page"]) + ("（节选截断）" if p.get("truncated") else "") for p in row["pages"])
        lines.append(f"- {row['title']}：可见页 {pages}；理由：{row['reason']}。")
    lines.extend(["", "## 阻塞或限制", ""])
    lines.extend(f"- {error}" for error in report["errors"])
    lines.extend(f"- {limit}" for limit in report["limits"])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    args = parser.parse_args()
    report = audit_run(args.run_root)
    atomic_json(args.run_root / "review" / "audit.json", report)
    target = args.run_root / "review" / "report.md"
    target.write_text(render_report(report), encoding="utf-8")
    logger.info("Audit passed={} report={}", report["audit_passed"], target)
    return 0 if report["audit_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
