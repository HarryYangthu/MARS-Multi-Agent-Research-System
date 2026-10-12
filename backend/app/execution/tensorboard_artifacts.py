"""Project-run visualizations from actual saved observations, never inferred curves.

Native events remain in place. Legacy JSON observations and all raster figures in
the current local-command attempt are mirrored with provenance and stable cursors.
The importer never loads checkpoints or executes code from a research repository.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from filelock import FileLock

from app.execution.tensorboard_writer import ScalarWriter
from app.harness.persistence import atomic_write_json


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _scalar_rows(writer: ScalarWriter, rows: list[Any], cursor: int,
                 *, history: bool) -> int:
    for row in rows:
        if not isinstance(row, dict):
            continue
        step = row.get("optimizer_steps" if history else "optimizer_step")
        if isinstance(step, bool) or not isinstance(step, int) or step <= cursor:
            continue
        if history:
            fields = {"validation_RES_db": "validation/RES_db", "epoch": "runtime/epoch",
                      "training_loss": "train/epoch_mean_loss"}
        else:
            fields = {"training_loss": "train/loss", "lr": "train/learning_rate",
                      "gradient_norm": "train/gradient_norm_before_clip",
                      "update_seconds": "runtime/update_seconds", "elapsed_seconds": "runtime/elapsed_seconds"}
        for key, tag in fields.items():
            writer.scalar(tag, row.get(key), step)
        cursor = step
    return cursor


def sync_run_visualizations(run_root: Path) -> dict[str, Any]:
    """Synchronize only this run's local-command outputs, independently of training success."""
    root = run_root.resolve()
    destination = root / "execution/tensorboard_artifacts"
    destination.mkdir(parents=True, exist_ok=True)
    with FileLock(str(destination / ".lock"), timeout=30):
        return _sync(root, destination)


def _sync(root: Path, destination: Path) -> dict[str, Any]:
    report: dict[str, Any] = {"schema": "execution.visualizations.v1", "models": [],
                              "images": 0, "warnings": [], "source": "saved_run_artifacts"}
    for request_path in sorted((root / "execution/local_commands").glob("*/*/job.json")):
        attempt = request_path.parent
        if not _inside(request_path, root) or not _inside(attempt, root):
            report["warnings"].append("跳过运行目录之外的日志链接")
            continue
        try:
            request = _read(request_path)
            if request.get("run_id") != root.name:
                report["warnings"].append("跳过不属于当前研究的记录")
                continue
            identity = hashlib.sha256(str(attempt.relative_to(root)).encode()).hexdigest()[:12]
            group = destination / identity
            group.mkdir(exist_ok=True)
            state_path = group / "import_state.json"
            state: dict[str, Any] = _read(state_path) if state_path.is_file() else {"models": {}, "images": {}}
            # A method owns its own history/checkpoint selection and TensorBoard run.
            for history_path in sorted(attempt.rglob("history.json")):
                if not _inside(history_path, attempt):
                    continue
                method_root = history_path.parent
                steps_path = method_root / "steps.jsonl"
                if not steps_path.is_file() or not _inside(steps_path, attempt):
                    continue
                key = method_root.relative_to(attempt).as_posix()
                previous = state["models"].get(key, {"steps": -1, "history": -1})
                native = any((method_root / "tensorboard").glob("events.out.tfevents.*"))
                history = _read(history_path)
                if not isinstance(history, list):
                    continue
                method_dir = group / key
                with ScalarWriter(method_dir) as writer:
                    if not native:
                        rows = []
                        for line in steps_path.read_text().splitlines():
                            try:
                                rows.append(json.loads(line))
                            except json.JSONDecodeError:
                                # A writer may still be flushing its final line.
                                continue
                        previous["steps"] = _scalar_rows(writer, rows, previous["steps"], history=False)
                        previous["history"] = _scalar_rows(writer, history, previous["history"], history=True)
                    summary_path = method_root / "summary.json"
                    if summary_path.is_file() and _inside(summary_path, attempt):
                        signature = hashlib.sha256(summary_path.read_bytes()).hexdigest()
                        if not native and previous.get("summary") != signature:
                            summary = _read(summary_path)
                            step = int(summary.get("selected_optimizer_steps", summary.get("optimizer_steps", 0)))
                            writer.scalar("model/real_parameters", summary.get("real_parameters"), 0)
                            for split, source_key in (("test", "test"), ("test_original", "initial_PIM")):
                                values = summary.get(source_key, {})
                                for metric in ("RES_db", "channel_mean_db", "worst_channel_db"):
                                    writer.scalar(f"{split}/{metric}", values.get(metric), step)
                                for index, value in enumerate(values.get("per_channel_db", []), 1):
                                    writer.scalar(f"{split}/channels/ch{index:02d}_RES_db", value, step)
                            previous["summary"] = signature
                    if not previous.get("provenance"):
                        writer.text("run/provenance", json.dumps({"source": key, "job": request,
                            "mode": "native" if native else "reconstructed_from_saved_observations",
                            "notice": "仅导入真实已保存记录；没有重训或插值补造；未保存的逐轮权重不可恢复。"}, ensure_ascii=False))
                        previous["provenance"] = True
                state["models"][key] = previous
                report["models"].append({"name": key, "native": native,
                    "training_steps": previous["steps"], "validation_points": len(history)})
            # Include every image generated by this invocation, including coefficient
            # dashboards; never scan other experiments in the repository.
            with ScalarWriter(group / "figures") as writer:
                for path in sorted(attempt.rglob("*")):
                    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"} or not path.is_file():
                        continue
                    if not _inside(path, attempt):
                        report["warnings"].append("跳过运行目录之外的图像链接")
                        continue
                    key = path.relative_to(attempt).as_posix()
                    signature = hashlib.sha256(path.read_bytes()).hexdigest()
                    if state["images"].get(key) != signature:
                        try:
                            writer.image("figures/" + key, path)
                            state["images"][key] = signature
                        except (OSError, ValueError) as exc:
                            report["warnings"].append(f"图像未接入：{key}：{exc}")
                    report["images"] += 1
            if not state["models"]:
                native_files = list(attempt.rglob("events.out.tfevents.*"))
                if not native_files and (attempt / "result.json").is_file():
                    report["warnings"].append(f"{request.get('experiment_id')} 只有最终结果，缺少可读取的训练过程记录")
            atomic_write_json(state_path, state)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            report["warnings"].append(f"可视化日志读取失败：{attempt.name}：{exc}")
    atomic_write_json(destination / "coverage.json", report)
    return report
