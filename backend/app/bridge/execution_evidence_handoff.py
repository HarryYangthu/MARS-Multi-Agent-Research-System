"""Bounded, read-only handoff of this run's actual job files to reporting."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from app.harness.runtime.project_scope import safe_scope_path


def _read(root: Path, name: str) -> tuple[Path, bytes]:
    path = safe_scope_path(root, name, must_exist=True)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Execution evidence exceeds the bounded handoff size")
    return path, path.read_bytes()


def execution_job_evidence(root: Path, run_id: str, project: str) -> str:
    """Keep file identity and measured steps, never infer scientific acceptance."""
    jobs = safe_scope_path(root, "execution/jobs/.handoff_anchor").parent
    lines = ["# Actual job file evidence", "Paths below belong to this run, relative to its root.",
             "Configured output_dir is a plan default; these are the actual host-owned job directories.",
             "Use complete filenames, including .json and .jsonl. These files are outside the source repository.",
             "Min/max loss does not establish monotonic convergence or statistical significance."]
    paths = sorted(jobs.glob("*.json")) if jobs.exists() else []
    for job_path in paths[:64]:
        name = job_path.relative_to(root).as_posix()
        try:
            _, raw = _read(root, name)
            job = json.loads(raw)
            if job.get("run_id") != run_id or job.get("project") != project:
                raise ValueError("Job identity differs from the current run/project")
            record: dict[str, Any] = {"source": name, "sha256": hashlib.sha256(raw).hexdigest(),
                "experiment_id": job.get("experiment_id"), "attempt": job.get("attempt"),
                "status": job.get("status"), "duration_seconds": job.get("result", {}).get("duration_seconds"),
                "files": []}
            if job.get("status") != "completed":
                lines.append(json.dumps(record, ensure_ascii=False, allow_nan=False))
                continue
            summaries: list[Path] = []
            for evidence in job.get("evidence", []):
                target = Path(str(evidence.get("path", "")))
                relative = target.relative_to(root).as_posix() if target.is_absolute() else target.as_posix()
                if not relative.startswith("execution/"):
                    raise ValueError("Job evidence is outside execution scope")
                path, content = _read(root, relative)
                sha = hashlib.sha256(content).hexdigest()
                if evidence.get("sha256") != "sha256:" + sha:
                    raise ValueError("Job evidence no longer matches its recorded digest")
                record["files"].append({"source": relative, "sha256": sha})
                if path.name == "summary.json":
                    summaries.append(path)
            for summary in summaries:
                record["actual_output_dir"] = summary.parent.relative_to(root).as_posix()
                for filename in ("config.yaml", "steps.jsonl", "progress.json"):
                    relative = (summary.parent / filename).relative_to(root).as_posix()
                    if not (summary.parent / filename).exists():
                        record["files"].append({"source": relative, "available": False})
                        continue
                    _, content = _read(root, relative)
                    item: dict[str, Any] = {"source": relative, "sha256": hashlib.sha256(content).hexdigest()}
                    if filename == "steps.jsonl":
                        rows = [json.loads(line) for line in content.decode().splitlines() if line.strip()]
                        values = {key: [row[key] for row in rows if isinstance(row, dict)
                            and type(row.get(key)) in (int, float) and math.isfinite(row[key])]
                            for key in ("lr", "optimizer_step", "training_loss")}
                        item.update({"rows": len(rows), "lr_values": sorted(set(values["lr"])),
                            "lr_rows": len(values["lr"]), "last_optimizer_step": values["optimizer_step"][-1]
                            if values["optimizer_step"] else None,
                            "training_loss_min": min(values["training_loss"]) if values["training_loss"] else None,
                            "training_loss_max": max(values["training_loss"]) if values["training_loss"] else None})
                    record["files"].append(item)
            lines.append(json.dumps(record, ensure_ascii=False, allow_nan=False))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            lines.append(json.dumps({"source": name, "evidence_error": str(exc)}, ensure_ascii=False))
    if len(paths) > 64:
        lines.append(f"Additional job records omitted: {len(paths) - 64}")
    return "\n".join(lines)
