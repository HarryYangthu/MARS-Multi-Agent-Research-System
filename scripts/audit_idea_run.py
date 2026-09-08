"""Audit an actual Idea run from its recorded events, source files and final artifact.

No model call or simulated execution is performed. Derived reports never rewrite
the raw trace. A passing report covers method-proposal material, not experiments.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from loguru import logger

from app.agents.idea.acceptance import validation_record_delivery_errors
from app.agents.idea.research import evidence_inventory, material_errors
from app.agents.idea.research_assessment import assessment_errors
from app.agents.idea.research_brief import render_research_brief
from app.agents.idea.research_delegate import load_delegated_research
from app.agents.idea.research_links import research_link_errors
from app.harness.agent_loop.trace import atomic_json, audit_trace, digest
from app.harness.schema.validator import validate_document
from scripts.idea_live_resume import audit_resumptions


def recorded_proposal(root: Path, summary: dict[str, Any]) -> Path:
    """Audit the recorded version, never substitute an earlier successful draft."""
    raw = summary.get("proposal_path")
    if raw is None:
        return root / "idea" / "idea_proposal.v1.md"
    if not isinstance(raw, str) or not raw:
        raise ValueError("proposal_path must name a recorded proposal file")
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if (path.parent != (root / "idea").resolve()
            or not re.fullmatch(r"idea_proposal\.v[1-9][0-9]*\.md", path.name)):
        raise ValueError("recorded proposal must stay in this run's versioned idea directory")
    return path


def recorded_delivery(root: Path, summary: dict[str, Any], *, invocation: str) -> Path | None:
    """Resolve the recorded export without substituting a neighbouring revision."""
    raw = summary.get("delivery_root")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw:
        raise ValueError("delivery_root must name a recorded delivery directory")
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    base = (root / "idea" / "deliveries").resolve()
    if (not path.is_relative_to(root.resolve()) or path.parent.parent != base
            or path.parent.name != invocation):
        raise ValueError("recorded delivery must stay in this run's invocation/export directory")
    return path


def audit_delivery(
    root: Path, summary: dict[str, Any], text: str, *, invocation: str, scope: str,
    model_review_passed: bool,
) -> dict[str, Any]:
    """Recheck exact-candidate receipts and export bytes without running an Agent."""
    errors: list[str] = []
    validation = validate_document(text, expected_schema="proposal.v1")
    metadata = validation.metadata
    candidate_sha = digest(text)
    receipts: list[tuple[Path, dict[str, Any]]] = []
    has_versioned_receipt = False
    for path in sorted((root / "idea" / "validation").glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            errors.append(f"unreadable validation receipt: {path.name}")
            continue
        if not isinstance(record, dict):
            errors.append(f"invalid validation receipt: {path.name}")
            continue
        has_versioned_receipt = has_versioned_receipt or record.get("delivery_contract_version") is not None
        if record.get("candidate_sha256") == candidate_sha:
            receipts.append((path, record))
    versioned = [(path, record) for path, record in receipts if record.get("delivery_contract_version") is not None]
    research_receipts = [(path, record) for path, record in receipts
                         if record.get("research_assessment_required") is True]
    contract_errors: list[str] = []
    for path, record in receipts:
        if "research_assessment_required" in record and type(record["research_assessment_required"]) is not bool:
            contract_errors.append(f"{path.name}: research_assessment_required must be a recorded boolean")
    if not validation.valid:
        contract_errors.extend(f"{error.path}: {error.message}" for error in validation.errors)
    elif versioned:
        for path, record in versioned:
            if record.get("scope", "method_proposal") != scope:
                contract_errors.append(f"{path.name}: delivery receipt scope differs from the recorded request")
            contract_errors.extend(validation_record_delivery_errors(metadata, record, body=validation.body))
    elif not receipts and (has_versioned_receipt or summary.get("delivery_root") is not None):
        contract_errors.append("no exact-candidate validation receipt for the recorded delivery")
    research_errors: list[str] = []
    research_reports: list[dict[str, Any]] | None = None
    expected_brief: str | None = None
    if research_receipts:
        checkpoint_path = root.resolve() / "agent_traces" / "idea" / invocation / "checkpoint.json"
        try:
            if (checkpoint_path.resolve() != checkpoint_path
                    or not checkpoint_path.is_relative_to(root.resolve())):
                raise ValueError("checkpoint resolves outside the recorded invocation")
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if not isinstance(checkpoint, dict) or checkpoint.get("candidate") != text:
                raise ValueError("checkpoint candidate differs from the audited candidate")
            observations = checkpoint.get("history")
            if not isinstance(observations, list) or any(not isinstance(item, dict) for item in observations):
                raise ValueError("checkpoint history must contain actual observation objects")
            research_reports, _ = load_delegated_research(root, observations)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            research_errors.append("research evidence verification failed: " + str(exc))
        if not model_review_passed:
            research_errors.append("research assessment requires an accepted model review of this candidate")
        if research_reports is not None and validation.valid:
            research_errors.extend(assessment_errors(metadata, research_reports, required=True))
            for path, record in research_receipts:
                try:
                    minimum = record.get("requirements", {}).get("min_sources", 1)
                    if type(minimum) is not int or minimum < 0:
                        raise ValueError("min_sources must be a nonnegative recorded integer")
                    research_errors.extend(research_link_errors(metadata, research_reports,
                        min_sources=minimum, require_linked_sources=True))
                except (ValueError, TypeError, AttributeError) as exc:
                    research_errors.append(f"{path.name}: invalid recorded research requirements: {exc}")
            try:
                expected_brief = render_research_brief(metadata, research_reports, reviewed=model_review_passed)
            except (ValueError, TypeError, KeyError) as exc:
                research_errors.append("research_brief.md cannot be reproduced from verified evidence: " + str(exc))
        contract_errors.extend(research_errors)
    errors.extend(contract_errors)
    contract_valid: bool | None = not contract_errors if versioned else None
    bundle_errors: list[str] = []
    delivery_root: Path | None = None
    try:
        delivery_root = recorded_delivery(root, summary, invocation=invocation)
    except ValueError as exc:
        bundle_errors.append(str(exc))
    if versioned and delivery_root is None and not bundle_errors:
        bundle_errors.append("versioned candidate has no recorded delivery_root")
    if delivery_root is not None:
        files: dict[str, str] = {}
        filenames = ["proposal.md", "proposal.json", "summary.txt", "acceptance.json"]
        if research_receipts:
            filenames.extend(["research_brief.md", "research_evidence.json"])
        for name in filenames:
            path = delivery_root / name
            try:
                if path.resolve().parent != delivery_root:
                    raise ValueError("file resolves outside the recorded delivery directory")
                files[name] = (path.read_bytes().decode("utf-8") if name == "research_brief.md"
                               else path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                bundle_errors.append(f"{name}: {type(exc).__name__} reading the recorded delivery file")
        if "proposal.md" in files and files["proposal.md"] != text:
            bundle_errors.append("delivery proposal.md differs from the accepted candidate")
        if "proposal.json" in files:
            try:
                if json.loads(files["proposal.json"]) != metadata:
                    bundle_errors.append("delivery proposal.json differs from the accepted candidate metadata")
            except ValueError:
                bundle_errors.append("delivery proposal.json is not valid JSON")
        human_summary = metadata.get("human_summary")
        if "summary.txt" in files and files["summary.txt"] != str(human_summary) + "\n":
            bundle_errors.append("delivery summary.txt differs from the accepted human_summary")
        if summary.get("human_summary") != human_summary:
            bundle_errors.append("runner summary human_summary differs from the accepted candidate")
        if summary.get("handoff") != metadata.get("handoff"):
            bundle_errors.append("runner summary handoff differs from the accepted candidate")
        if research_receipts:
            if "research_evidence.json" in files:
                try:
                    sidecar = json.loads(files["research_evidence.json"])
                except ValueError:
                    sidecar = None
                if not isinstance(sidecar, dict):
                    bundle_errors.append("delivery research_evidence.json must be a JSON object")
                else:
                    if set(sidecar) != {"schema", "proposal_sha256", "reports", "scientific_validated"}:
                        bundle_errors.append("delivery research_evidence.json has missing or unexpected fields")
                    if sidecar.get("schema") != "idea.research_evidence.v1":
                        bundle_errors.append("delivery research_evidence.json has an unsupported schema")
                    if sidecar.get("proposal_sha256") != candidate_sha:
                        bundle_errors.append("delivery research_evidence.json is bound to a different candidate hash")
                    if sidecar.get("scientific_validated") is not False:
                        bundle_errors.append("delivery research_evidence.json cannot claim scientific validation")
                    if research_reports is None or digest(sidecar.get("reports")) != digest(research_reports):
                        bundle_errors.append("delivery research_evidence.json reports differ from verified checkpoint evidence")
            if "research_brief.md" in files:
                if expected_brief is None or files["research_brief.md"] != expected_brief:
                    bundle_errors.append("delivery research_brief.md differs from exact rendering of verified evidence")
        if "acceptance.json" in files:
            try:
                acceptance = json.loads(files["acceptance.json"])
            except ValueError:
                acceptance = None
            if not isinstance(acceptance, dict):
                bundle_errors.append("delivery acceptance.json must be a JSON object")
            else:
                handoff = metadata.get("handoff", {})
                prerequisites = handoff.get("required_context", []) if isinstance(handoff, dict) else []
                expected_flags = {"schema_valid": validation.valid,
                                  "delivery_contract_valid": not contract_errors,
                                  "model_review_passed": model_review_passed,
                                  "scientific_validated": False, "simulation_executed": False,
                                  "execution_requires_context": any(isinstance(item, dict) and item.get("blocks_execution") is True
                                                                    for item in prerequisites)}
                if research_receipts:
                    expected_flags["research_decisions_checked"] = validation.valid and not research_errors
                for key, expected in expected_flags.items():
                    if acceptance.get(key) is not expected:
                        bundle_errors.append(f"delivery acceptance.json/{key} differs from audited facts")
                if acceptance.get("proposal_sha256") != candidate_sha:
                    bundle_errors.append("delivery acceptance.json is bound to a different candidate hash")
                if acceptance.get("scope") != scope:
                    bundle_errors.append("delivery acceptance.json scope differs from the recorded request")
    errors.extend(bundle_errors)
    bundle_checked = bool(versioned) or summary.get("delivery_root") is not None
    return {"delivery_contract_valid": contract_valid,
            "delivery_bundle_valid": not bundle_errors if bundle_checked else None,
            "research_decisions_checked": validation.valid and not research_errors if research_receipts else None,
            "delivery_root": str(delivery_root) if delivery_root is not None else None,
            "validation_receipts": [path.relative_to(root).as_posix() for path, _ in receipts],
            "delivery_contract_versions": list(dict.fromkeys(str(record["delivery_contract_version"]) for _, record in versioned)),
            "errors": errors}


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
    proposal = recorded_proposal(root, summary)
    resumptions, errors = audit_resumptions(root, trace_root)
    schema_valid = False
    material_valid = False
    delivery: dict[str, Any] = {"delivery_contract_valid": None, "delivery_bundle_valid": None,
                                "delivery_root": None, "validation_receipts": [], "delivery_contract_versions": []}
    metadata: dict[str, Any] = {}
    reflections = [e for e in events if e["kind"] == "reflection"]
    model_review_passed = bool(state.get("reflection_accepted") and reflections and reflections[-1].get("accept") is True)
    if proposal.is_file():
        text = proposal.read_text()
        validation = validate_document(text, expected_schema="proposal.v1")
        schema_valid = validation.valid
        errors.extend(f"{e.path}: {e.message}" for e in validation.errors)
        if schema_valid:
            metadata = validation.metadata
            requirements = request["scenario"]["requirements"]
            material_failures = material_errors(metadata, observations,
                                          min_sources=int(requirements["min_sources"]),
                                          min_pdfs=int(requirements["min_pdfs"]),
                                          require_budget=bool(requirements["require_parameter_budget"]),
                                          max_ratio=float(requirements["max_parameter_ratio"]))
            errors.extend(material_failures)
            material_valid = not material_failures
            delivery = audit_delivery(root, summary, text, invocation=trace_root.name,
                                      scope=str(request.get("scope", request["scenario"].get("scope", "method_proposal"))),
                                      model_review_passed=model_review_passed)
            errors.extend(delivery.pop("errors"))
        if text != state["candidate"] or digest(text) != summary.get("proposal_sha256"):
            errors.append("final artifact differs from checkpoint or recorded summary hash")
    else:
        errors.append("no final proposal file")
    if state["status"] != "passed" or state["pending"] is not None:
        errors.append("loop has not reached passed with no pending operation")
    if not audit["consistent"]:
        errors.append("trace audit is inconsistent")
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
        "audited_proposal": str(proposal),
        "source_resumptions": resumptions,
        "recorded_status": summary["status"], "loop_status": state["status"], "pending": state["pending"],
        "audit_passed": not errors, "errors": errors, "trace_consistent": audit["consistent"],
        "schema_valid": schema_valid, "material_valid": material_valid,
        **delivery, "human_summary": metadata.get("human_summary"), "handoff": metadata.get("handoff"),
        "reflection_accepted": state["reflection_accepted"],
        "model_review_passed": model_review_passed,
        "scientific_validated": False, "project_ready": False, "simulation_executed": False,
        "duration_seconds": summary.get("duration_seconds"), "counts": state["counts"],
        "sdk_attempt_failures": sum(e["kind"] == "sdk_attempt_failed" for e in events),
        "provider_error": audit.get("provider_error"),
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
             f"运行、材料与交付审计通过：**{report['audit_passed']}**。记录状态：{report['recorded_status']}。", "",
             f"Schema：{report['schema_valid']}；材料：{report['material_valid']}；"
             f"交付契约：{report.get('delivery_contract_valid')}；交付文件一致：{report.get('delivery_bundle_valid')}；"
             f"trace 一致：{report['trace_consistent']}。", "",
             f"模型审查通过：{report.get('model_review_passed', report['reflection_accepted'])}；"
             f"实验执行：{report.get('simulation_executed', False)}；科学验证：{report.get('scientific_validated', False)}。"
             "模型审查不等于实验或独立科学验证；交付检查为 None 表示历史记录未声明新版合同。", "",
             f"模型请求 {counts['model_requests']} 次，返回 {counts['model_responses']} 次；"
             f"SDK 尝试 {counts['sdk_attempts']} 次；工具 {counts['tool_dispatches']} 次，Observation {counts['observations']} 次。", "",
             f"协议修复 {counts['protocol_repairs']} 次，材料/Schema 修复 {counts['validation_repairs']} 次，"
             f"Reflection {counts['reflections']} 次。", "",
             f"Token 用量：{report['usage']}；记录完整：{report['usage_complete']}（不完整时仅为下界）。", "",
             "## 方案概括", "", str(report.get("human_summary") or "本轮没有可审计的方案概括。"), "",
             "## 下游交接", "", "```json", json.dumps(report.get("handoff"), ensure_ascii=False, indent=2), "```", "",
             "## 工具操作与选择理由", "", "| 步骤 | 工具 | 成功 | Agent 提供的行动理由 |",
             "| --- | --- | --- | --- |"]
    for row in report["tools"]:
        lines.append(f"| {row['step']} | {row['tool']} | {row['ok']} | {cell(row['reason'])} |")
    if report.get("source_resumptions"):
        lines.extend(["", "## 断点续跑源码", ""])
        for row in report["source_resumptions"]:
            lines.append(f"- `{row['source_commit']}`；文件树 `{row['source_tree']}`；沿用原调用计数和预算。")
    lines.extend(["", "## 实际下载与阅读范围", "", f"证据计数：{report['evidence_counts']}", ""])
    for row in report["downloaded_sources"]:
        lines.extend([f"- [{row['title']}]({row['url']})；{row['bytes']} bytes；SHA-256 `{row['sha256']}`。"])
    for row in report["read_windows"]:
        pages = ", ".join(str(p["page"]) + ("（节选截断）" if p.get("truncated") else "") for p in row["pages"])
        lines.append(f"- {row['title']}：可见页 {pages}；理由：{row['reason']}。")
    lines.extend(["", "## 阻塞或限制", ""])
    if report.get("provider_error"):
        lines.append(f"- 最后一次 SDK 尝试的错误：`{report['provider_error']}`。")
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
