"""Persist mock-simulation results into a run's ``execution/`` subdir."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.execution.mock_simulation import MockResult
from app.harness.schema.frontmatter_parser import dumps as fm_dumps


def write_run_log(*, run_root: Path, result: MockResult, project: str) -> Path:
    """Write a `run_log.v1` artifact derived from MockResult."""
    metadata: dict[str, Any] = {
        "schema": "run_log.v1",
        "project": project,
        "agent": "execution",
        "upstream_artifact": "code_spec.approved.md",
        "run_id": f"{result.run_id}_{result.experiment_id}",
        "batch_size": 512,
        "gpu_used": [],
        "duration_seconds": float(result.duration_seconds),
        "status": result.status,
        "metrics": dict(result.metrics),
        "fingerprint_hash": result.fingerprint_hash,
        "is_mock": result.is_mock,
    }
    body = (
        f"# Run log — {result.experiment_id}\n\n"
        f"Mock simulation completed at "
        f"{datetime.now(tz=timezone.utc).isoformat()}.\n"
    )
    text = fm_dumps(metadata, body)
    target_dir = run_root / "execution"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"run_log_{result.experiment_id}.v1.md"
    target.write_text(text, encoding="utf-8")
    return target


def write_metrics_json(*, run_root: Path, results: list[MockResult]) -> Path:
    target = run_root / "execution" / "metrics.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "run_id": f"{r.run_id}_{r.experiment_id}",
            "metrics": r.metrics,
            "fingerprint_hash": r.fingerprint_hash,
            "duration_seconds": r.duration_seconds,
        }
        for r in results
    ]
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return target
