"""Human calibration utilities for model-based evaluators.

The V2 contract is intentionally data-first: export samples for human review,
then compare human labels with evaluator decisions. This keeps calibration
usable without external LLM access and gives future model judges a stable
drift-report format.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.harness.evaluation.artifacts import read_all_reports

_RANK = {"pass": 0, "warn": 1, "revise": 2, "block": 3, "fail": 4}


def export_human_calibration_samples(
    *,
    run_roots: list[Path],
    output_path: Path,
    limit: int = 100,
) -> Path:
    samples: list[dict[str, Any]] = []
    for run_root in run_roots:
        for report in read_all_reports(run_root=run_root):
            metadata = report.get("metadata", {})
            if not isinstance(metadata, dict):
                continue
            findings = metadata.get("findings", [])
            decision = str(metadata.get("decision", ""))
            if decision == "pass" and not findings:
                continue
            samples.append(
                {
                    "schema": "evaluation_human_calibration_sample.v1",
                    "sample_id": f"{run_root.name}:{report.get('path')}",
                    "run_id": run_root.name,
                    "report_path": report.get("path"),
                    "target_ref": metadata.get("target_ref"),
                    "evaluator": metadata.get("evaluator"),
                    "evaluator_version": metadata.get("evaluator_version"),
                    "evaluator_decision": decision,
                    "overall_score": metadata.get("overall_score"),
                    "findings": findings if isinstance(findings, list) else [],
                    "human_decision": None,
                    "human_notes": "",
                }
            )
            if len(samples) >= limit:
                break
        if len(samples) >= limit:
            break
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        for sample in samples:
            fh.write(json.dumps(sample, ensure_ascii=False) + "\n")
    return output_path


def write_calibration_report(*, labels_path: Path, output_path: Path) -> Path:
    samples = _read_jsonl(labels_path)
    comparable = [
        sample
        for sample in samples
        if sample.get("human_decision") in _RANK
        and sample.get("evaluator_decision") in _RANK
    ]
    disagreements: list[dict[str, Any]] = []
    for sample in comparable:
        evaluator_decision = str(sample["evaluator_decision"])
        human_decision = str(sample["human_decision"])
        delta = abs(_RANK[evaluator_decision] - _RANK[human_decision])
        if delta:
            disagreements.append(
                {
                    "sample_id": sample.get("sample_id"),
                    "run_id": sample.get("run_id"),
                    "report_path": sample.get("report_path"),
                    "evaluator": sample.get("evaluator"),
                    "evaluator_decision": evaluator_decision,
                    "human_decision": human_decision,
                    "rank_delta": delta,
                    "human_notes": sample.get("human_notes", ""),
                }
            )
    total = len(comparable)
    agreement_rate = 1.0 - (len(disagreements) / total) if total else None
    report = {
        "schema": "evaluation_calibration_report.v1",
        "created": datetime.now(tz=timezone.utc).isoformat(),
        "labels_path": labels_path.as_posix(),
        "sample_count": len(samples),
        "comparable_count": total,
        "agreement_rate": round(agreement_rate, 6) if agreement_rate is not None else None,
        "needs_judge_review": bool(agreement_rate is not None and agreement_rate < 0.85),
        "evaluator_decision_counts": dict(Counter(str(s.get("evaluator_decision")) for s in comparable)),
        "human_decision_counts": dict(Counter(str(s.get("human_decision")) for s in comparable)),
        "disagreements": disagreements[:50],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not path.exists():
        return items
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            items.append(raw)
    return items
