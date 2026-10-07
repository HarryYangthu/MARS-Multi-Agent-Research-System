"""Build the normalized data pack consumed by report deliverable generators."""
from __future__ import annotations

import json
import hashlib
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.storage.run_store import RunHandle
from app.harness.schema.frontmatter_parser import parse
from app.harness.runtime.project_scope import safe_scope_path


def collect_report_data_pack(run: RunHandle) -> dict[str, Any]:
    """Collect metrics, curves, logs, evaluation, and writing sources.

    The function is deliberately tolerant: reports must still generate a
    degraded bundle when simulations partially fail or no plots are available.
    """
    source_refs: list[str] = []
    metrics_raw = _read_json(run.subdir("execution") / "metrics.json")
    metrics_rows = _normalize_metrics(metrics_raw)
    if metrics_raw is not None:
        source_refs.append("execution/metrics.json")

    curves = _collect_curves(run, source_refs)
    plots = _collect_plots(run, source_refs)
    evaluation = _read_json(run.subdir("events") / "evaluation_scorecard.json")
    if evaluation is not None:
        source_refs.append("events/evaluation_scorecard.json")

    writing_source = _latest_existing(
        [
            run.subdir("writing") / "research_report.approved.md",
            *reversed(sorted(run.subdir("writing").glob("research_report.v*.md"), key=lambda p: int(p.stem.rsplit("v", 1)[1]))),
        ]
    )
    report_markdown = ""
    report_bytes = b""
    if writing_source is not None:
        report_bytes = writing_source.read_bytes()
        report_markdown = parse(report_bytes.decode("utf-8")).body
        source_refs.append(_relative(run, writing_source))

    diagnostics = [
        {
            "path": _relative(run, path),
            "excerpt": _excerpt(path.read_text(encoding="utf-8")),
        }
        for path in _sorted_paths(run.subdir("diagnosis").glob("*.md"))
    ]
    source_refs.extend(item["path"] for item in diagnostics)

    run_logs = _collect_run_logs(run, source_refs)
    summary = _summarize_metrics(metrics_rows)
    degraded_reasons = _degraded_reasons(
        metrics_rows=metrics_rows,
        curves=curves,
        plots=plots,
    )
    source_hashes = {ref: hashlib.sha256(safe_scope_path(run.root, ref, must_exist=True).read_bytes()).hexdigest()
                     for ref in sorted(set(source_refs))}

    return {
        "schema": "report_data_pack.v1",
        "run_id": run.run_id,
        "project": run.project,
        "task": run.task,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "summary": summary,
        "metrics": metrics_rows,
        "curves": curves,
        "plots": plots,
        "evaluation": evaluation if isinstance(evaluation, dict) else {},
        "diagnostics": diagnostics,
        "run_logs": run_logs,
        "report_markdown_excerpt": _excerpt(report_markdown, limit=2200),
        "report_markdown": report_markdown,
        "run_root": str(run.root.resolve()),
        "report_source": _relative(run, writing_source) if writing_source else None,
        "report_source_sha256": hashlib.sha256(report_bytes).hexdigest() if writing_source else None,
        "report_approved": bool(writing_source and writing_source.name.endswith(".approved.md")),
        "source_refs": sorted(set(source_refs)),
        "source_hashes": source_hashes,
        "degraded": bool(degraded_reasons),
        "degraded_reasons": degraded_reasons,
    }


def _normalize_metrics(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        metrics_raw = item.get("metrics", {})
        metrics = metrics_raw if isinstance(metrics_raw, dict) else {}
        row: dict[str, Any] = {
            "index": index,
            "experiment_id": str(item.get("experiment_id") or item.get("run_id") or f"exp_{index + 1}"),
            "status": str(item.get("status") or "completed"),
            "fingerprint_hash": str(item.get("fingerprint_hash") or ""),
            "duration_seconds": _as_float(item.get("duration_seconds")),
        }
        for key, value in metrics.items():
            if isinstance(key, str):
                row[key] = _jsonable(value)
        rows.append(row)
    return rows


def _summarize_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    numeric_keys: list[str] = []
    for row in rows:
        for key, value in row.items():
            if key in {"index", "experiment_id", "status", "fingerprint_hash"}:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if key not in numeric_keys:
                    numeric_keys.append(key)

    aggregates: dict[str, dict[str, float]] = {}
    for key in numeric_keys:
        values = [
            float(row[key])
            for row in rows
            if isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool)
        ]
        if not values:
            continue
        aggregates[key] = {
            "min": min(values),
            "max": max(values),
            "mean": round(sum(values) / len(values), 6),
        }

    best_key = "RES" if "RES" in numeric_keys else ("loss" if "loss" in numeric_keys else "")
    best_row: dict[str, Any] = {}
    if best_key:
        candidates = [
            row
            for row in rows
            if isinstance(row.get(best_key), (int, float)) and not isinstance(row.get(best_key), bool)
        ]
        if candidates:
            best_row = min(candidates, key=lambda item: float(item[best_key]))

    return {
        "experiment_count": len(rows),
        "numeric_metrics": numeric_keys,
        "aggregates": aggregates,
        "primary_metric": best_key,
        "best_experiment": best_row,
    }


def _collect_curves(run: RunHandle, source_refs: list[str]) -> list[dict[str, Any]]:
    curves_dir = run.subdir("execution") / "curves"
    out: list[dict[str, Any]] = []
    for path in _sorted_paths(curves_dir.glob("*.json")):
        data = _read_json(path)
        if data is None:
            continue
        source_refs.append(_relative(run, path))
        points = data if isinstance(data, list) else data.get("points") if isinstance(data, dict) else []
        if not isinstance(points, list) and isinstance(data, dict) and isinstance(data.get("values"), list):
            points = [{"sample_index": index + 1, str(data.get("metric", "value")): value}
                      for index, value in enumerate(data["values"])]
        if isinstance(points, list) and points and all(isinstance(value, (int, float)) for value in points):
            points = [{"sample_index": index + 1, "value": value} for index, value in enumerate(points)]
        points_count = len(points) if isinstance(points, list) else 0
        out.append(
            {
                "path": _relative(run, path),
                "experiment_id": str(data.get("experiment_id", path.stem)) if isinstance(data, dict) else path.stem,
                "points_count": points_count,
                "preview": points[:5] if isinstance(points, list) else [],
                "points": points if isinstance(points, list) else [],
            }
        )
    # Current runners write steps beside the receipt-bound summary, not curves/.
    latest: dict[str, dict[str, Any]] = {}
    for record in run.subdir("execution").joinpath("jobs").glob("*.json"):
        job = _read_json(record)
        if not isinstance(job, dict) or job.get("run_id") != run.run_id or job.get("project") != run.project:
            continue
        group = str(job.get("experiment_id", ""))
        if group and int(job.get("attempt", 0)) >= int(latest.get(group, {}).get("attempt", -1)):
            latest[group] = job
    for group, job in latest.items():
        if job.get("status") != "completed":
            continue
        for evidence in job.get("evidence", []):
            try:
                candidate = Path(str(evidence["path"]))
                relative = candidate.relative_to(run.root.resolve()) if candidate.is_absolute() else candidate
                if not str(relative).startswith("execution/"):
                    raise ValueError("curve source outside execution")
                summary = safe_scope_path(run.root, str(relative), must_exist=True)
                if summary.name != "summary.json":
                    continue
                if evidence.get("sha256") != "sha256:" + hashlib.sha256(summary.read_bytes()).hexdigest():
                    raise ValueError("summary digest differs from job receipt")
                path = safe_scope_path(run.root, str(relative.parent / "steps.jsonl"), must_exist=True)
                if path.stat().st_size > 16 * 1024 * 1024:
                    raise ValueError("step file exceeds export limit")
                points = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
                if any(not isinstance(row, dict) or any(isinstance(v, float) and not math.isfinite(v) for v in row.values()) for row in points):
                    raise ValueError("invalid step values")
                source_refs.append(_relative(run, path))
                out.append({"path": _relative(run, path), "experiment_id": group,
                            "points_count": len(points), "points": points})
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return out


def _collect_plots(run: RunHandle, source_refs: list[str]) -> list[dict[str, Any]]:
    plots_dir = run.subdir("execution") / "plots"
    out: list[dict[str, Any]] = []
    candidates = list(plots_dir.iterdir()) if plots_dir.exists() else []
    candidates.extend(run.subdir("execution").glob("*.png"))
    for path in _sorted_paths(candidates):
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(run.root.resolve()):
            continue
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".svg", ".pdf"}:
            continue
        source_refs.append(_relative(run, path))
        out.append(
            {
                "path": _relative(run, path),
                "kind": path.suffix.lower().lstrip("."),
                "bytes": path.stat().st_size,
            }
        )
    return out


def _collect_run_logs(run: RunHandle, source_refs: list[str]) -> list[dict[str, str]]:
    candidates: list[Path] = []
    candidates.extend(_sorted_paths(run.subdir("execution").glob("*.log")))
    candidates.extend(_sorted_paths(run.subdir("execution").glob("*.txt")))
    candidates.extend(_sorted_paths(run.subdir("execution").glob("run_log*.md")))
    out: list[dict[str, str]] = []
    for path in candidates[:12]:
        source_refs.append(_relative(run, path))
        out.append({"path": _relative(run, path), "excerpt": _excerpt(path.read_text(encoding="utf-8"))})
    return out


def _degraded_reasons(
    *,
    metrics_rows: list[dict[str, Any]],
    curves: list[dict[str, Any]],
    plots: list[dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    if not metrics_rows:
        reasons.append("metrics.json missing or empty")
    if not curves:
        reasons.append("no curve JSON files found")
    if not plots:
        reasons.append("no plots found")
    failed = [
        str(row.get("experiment_id"))
        for row in metrics_rows
        if str(row.get("status", "completed")).lower() not in {"completed", "ok", "success"}
    ]
    if failed:
        reasons.append("partial experiment failures: " + ", ".join(failed[:5]))
    return reasons


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _latest_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.is_file() and path.resolve().is_relative_to(path.parent.parent.resolve()):
            return path
    return None


def _sorted_paths(paths: Any) -> list[Path]:
    return sorted((path for path in paths if isinstance(path, Path)), key=lambda p: p.name)


def _relative(run: RunHandle, path: Path) -> str:
    try:
        return path.resolve().relative_to(run.root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _excerpt(text: str, *, limit: int = 1200) -> str:
    compact = "\n".join(line.rstrip() for line in text.strip().splitlines() if line.strip())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return str(value)
