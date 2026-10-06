"""Read-only admission of managed paper-training measurements.

Only files inside the run are read. A receipt proves saved evidence consistency,
never independent reproduction or the scientific validity of a legacy split.
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.execution.paper_static_adapter import _metrics_from_summary, _parse_step_line
from app.harness.agent_loop.trace import digest
from app.harness.schema.experiment_contract import budget

if TYPE_CHECKING:
    from app.bridge.results_service import ResultReader


def metric_unit(name: str) -> str | None:
    if name in {"PIM", "RES", "APE", "paper_PIM_db", "paper_RES_db", "paper_APE_db", "paper_mean_gain_db"}:
        return "dB"
    if name == "optimizer_steps":
        return "optimizer updates"
    if name == "epochs":
        return "epochs"
    if name in {"parameter_counts", "channels"}:
        return "count"
    return None


def collect_paper_jobs(reader: ResultReader, *, existing_jobs: int = 0, existing_metrics: int = 0
                       ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    from app.bridge.results_service import canonical, finite_number, public_text, safe_path, sha256

    experiments: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    curves: list[dict[str, Any]] = []
    directory = safe_path(reader.run.root, "execution/jobs")
    paths = list(itertools.islice(directory.glob("*.json"), reader.policy["max_jobs"] + 1))
    if len(paths) > reader.policy["max_jobs"]:
        reader.limitations.append("作业数量超过读取上限，结果不完整。")
    for path in sorted(paths[:reader.policy["max_jobs"]]):
        experiment: dict[str, Any] = {"experiment_id": "unknown", "job_id": None, "role": "unknown",
            "status": "unknown", "verification": "invalid", "duration_seconds": None, "seed": None, "source_id": None}
        try:
            job, job_data = reader.record(path.relative_to(reader.run.root).as_posix())
            # Local-command journals are already admitted by the local receipt reader.
            evidence = job.get("evidence", [])
            paper = [row for row in evidence if isinstance(row, dict) and
                "/execution/paper_static/" in str(row.get("path", "")) and str(row.get("path", "")).endswith("/execution_receipt.json")]
            if not paper:
                continue
            if len(experiments) + existing_jobs >= reader.policy["max_jobs"]:
                reader.limitations.append("作业数量超过读取上限，结果不完整。")
                break
            if len(paper) != 1:
                raise ValueError("Ambiguous paper receipt")
            result = job.get("result")
            if not isinstance(result, dict):
                raise ValueError("Missing saved measurements")
            name = job.get("experiment_id")
            if not isinstance(name, str) or not name:
                raise ValueError("Missing experiment identity")
            safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
            declared = Path(paper[0]["path"])
            relative = declared.relative_to(reader.run.root.resolve()).as_posix()
            if declared.name != "execution_receipt.json" or declared.parent.parent != directory.parent / "paper_static" / safe_name:
                raise ValueError("Receipt belongs to another experiment")
            receipt, raw = reader.record(relative)
            source_id = reader.source("paper_static_receipt", raw)
            experiment.update(experiment_id=public_text(name), job_id=declared.parent.name,
                status=job.get("status", "unknown"), source_id=source_id)
            fingerprint = "sha256:" + sha256(raw)
            config = receipt.get("config")
            if (not isinstance(config, dict) or config.get("backend") != "paper_static"
                    or receipt.get("schema") != "paper_static_receipt.v1"
                    or job.get("schema") != "execution.job" or job.get("project") != reader.run.project
                    or any(row.get("run_id") != reader.run.run_id or row.get("experiment_id") != name for row in (job, result, receipt))
                    or any(row.get("status") != "completed" for row in (job, result, receipt))
                    or result.get("is_mock") is not False or type(receipt.get("returncode")) is not int or receipt["returncode"] != 0
                    or result.get("fingerprint_hash") != fingerprint or paper[0].get("sha256") != fingerprint):
                raise ValueError("Paper job identity or checksum mismatch")
            unit, count = budget(config)
            seed = config.get("seed")
            if (type(seed) is not int or job.get("inputs") != digest({"run_id": reader.run.run_id,
                    "project": reader.run.project, "name": name, "config": config, "seed": seed, "steps": count, "backend": "paper_static"})
                    or path.stem != digest([name, config.get("attempt", 1), config.get("confirmation_token", "")])):
                raise ValueError("Paper job submission differs from receipt")
            files: dict[str, bytes] = {}
            for key in ("summary", "log"):
                target = Path(str(receipt.get(key + "_path", "")))
                admitted = target.relative_to(reader.run.root.resolve()).as_posix()
                if key == "summary" and not admitted.startswith(declared.parent.relative_to(reader.run.root).as_posix() + "/"):
                    raise ValueError("Summary belongs to another attempt")
                if key == "log" and admitted != f"execution/logs/{safe_name}_paper_static.log":
                    raise ValueError("Log belongs to another experiment")
                data = reader.read(admitted, evidence=True)
                expected = "sha256:" + sha256(data)
                if (receipt.get(key + "_sha256") != sha256(data) or not any(
                        isinstance(row, dict) and row.get("path") == str(target) and row.get("sha256") == expected for row in evidence)):
                    raise ValueError("Measurement evidence checksum mismatch")
                reader.source("paper_" + key + "_not_exported", data)
                files[key] = data
            summary = json.loads(files["summary"])
            if not isinstance(summary, dict):
                raise ValueError("Invalid summary")
            expected_metrics = _metrics_from_summary(summary)
            measurements = result.get("metrics")
            if (not expected_metrics or not isinstance(measurements, dict)
                    or len(metrics) + existing_metrics + len(measurements) > reader.policy["max_metrics"]
                    or set(measurements) != set(expected_metrics) | {"returncode", "dry_run", "max_iters", "summary_written"}
                    or any(finite_number(value) is None for value in measurements.values())
                    or any(measurements.get(key) != value for key, value in expected_metrics.items())
                    or measurements.get("returncode") != 0 or measurements.get("dry_run") != 0
                    or measurements.get("max_iters") != count or measurements.get("summary_written") != 1
                    or unit == "steps" and (type(summary.get("optimizer_steps")) is not int
                        or type(summary.get("seed")) is not int or summary.get("optimizer_steps") != count or summary.get("seed") != seed)):
                raise ValueError("Recorded measurements or budget differ from summary")
            points = result.get("loss_curve", [])
            if not isinstance(points, list) or len(points) > reader.policy["max_curve_points"] or any(finite_number(value) is None for value in points):
                raise ValueError("Invalid saved curve")
            if unit == "steps":
                observations = [row for line in files["log"].decode("utf-8").splitlines() if (row := _parse_step_line(line)) is not None]
                if ([row["optimizer_step"] for row in observations] != list(range(1, count + 1))
                        or points != [row["loss"] for row in observations]):
                    raise ValueError("Curve differs from actual update observations")
            role = config.get("role") if config.get("role") in {"baseline", "candidate", "ablation"} else "unknown"
            experiment.update(verification="verified_local_receipt", duration_seconds=finite_number(result.get("duration_seconds")),
                seed=seed, steps=count if unit == "steps" else None, role=role, execution_backend="paper_static", os_isolated=False,
                configuration_sha256=sha256(canonical(config)), comparison_group=sha256(canonical({"config": {key: value for key, value in config.items()
                    if key not in {"seed", "attempt", "confirmation_token"}}, "config_sha256": receipt.get("config_sha256")})))
            reader.source("paper_job_journal", job_data)
            # Control fields also need their identity/hash chain verified, but are
            # not used for any scientific outcome or loss comparison.
            for key, value in measurements.items():
                metrics.append({"experiment_id": public_text(name), "job_id": experiment["job_id"], "name": key,
                    "value": value, "unit": metric_unit(key), "direction": None, "role": role,
                    "verification": "verified_local_receipt", "source_id": source_id})
            if points:
                curves.append({"experiment_id": public_text(name), "job_id": experiment["job_id"], "metric": "training_loss",
                    "points": points, "source_id": source_id})
            reader.limitations.append("paper_static 为原项目训练协议；APE 是抵消增益 dB，训练损失依赖损失函数，各种损失值不能直接横向比较。")
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            reader.limitations.append("一项 paper_static 作业的身份、预算、收据或测量文件未通过校验，未纳入已验证结果。")
        experiments.append(experiment)
    return experiments, metrics, curves
