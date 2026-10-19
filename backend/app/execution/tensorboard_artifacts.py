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
import yaml

from app.execution.tensorboard_writer import ScalarWriter
from app.harness.persistence import atomic_write_json


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _regular_inside(path: Path, root: Path) -> bool:
    if not path.is_file() or not _inside(path, root):
        return False
    for parent in (path, *path.parents):
        if parent == root:
            return True
        if parent.is_symlink():
            return False
    return False


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            # Native observers append while the experiment is running.
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _is_baseline(request: dict[str, Any], configuration: dict[str, Any]) -> bool:
    job = request.get("config", {})
    role = job.get("role") if isinstance(job, dict) else None
    if isinstance(role, str) and role in {"baseline", "candidate", "ablation"}:
        return role == "baseline"
    return bool(configuration.get("model") == "baseline")


def _native_models(root: Path, attempt: Path, group: Path, request: dict[str, Any],
                   identity: str, record_index: int, record_count: int,
                   registered: set[str]) -> list[dict[str, Any]]:
    """Index real native observations without creating or rewriting any events."""
    if (request.get("schema") != "local_command_request.v1"
            or request.get("invocation_id") != attempt.name
            or request.get("experiment_id") != attempt.parent.name
            or not isinstance(request.get("project"), str) or not request["project"]):
        return []
    models = []
    for steps_path in sorted(attempt.rglob("steps.jsonl")):
        method_root = steps_path.parent
        key = method_root.relative_to(attempt).as_posix()
        scalar_dir = method_root / "tensorboard"
        if (key in registered or not _regular_inside(steps_path, attempt)
                or not any(_regular_inside(path, attempt)
                           for path in scalar_dir.glob("events.out.tfevents.*"))):
            continue
        updates = [row["optimizer_step"] for row in _jsonl_rows(steps_path)
                   if type(row.get("optimizer_step")) is int and row["optimizer_step"] > 0]
        if not updates:
            continue
        training_steps = max(updates)
        configuration: dict[str, Any] = {}
        for name in ("config.yaml", "config.json"):
            path = method_root / name
            if _regular_inside(path, attempt):
                try:
                    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) if name.endswith("yaml") else _read(path)
                except (OSError, ValueError, yaml.YAMLError):
                    continue  # A missing display label does not hide actual native observations.
                if isinstance(loaded, dict):
                    configuration = loaded
                    break
        epochs_path = method_root / "epochs.jsonl"
        epochs = {row["epoch"] for row in _jsonl_rows(epochs_path)
                  if type(row.get("epoch")) is int and row["epoch"] > 0
                  and type(row.get("optimizer_steps")) is int
                  and 0 < row["optimizer_steps"] <= training_steps} if _regular_inside(epochs_path, attempt) else set()
        raw_model = configuration.get("model_name", configuration.get("model"))
        model_name = raw_model if isinstance(raw_model, str) and raw_model else method_root.name
        snapshot_dir = method_root / "tensorboard_snapshots"
        histogram_dir = snapshot_dir if snapshot_dir.is_dir() and not snapshot_dir.is_symlink() else scalar_dir
        models.append({"id": identity + "/" + key, "name": key,
            "label": model_name + " · " + (f"记录 {record_index} · " if record_count > 1 else "")
                     + str(request.get("experiment_id", "")),
            "is_baseline": _is_baseline(request, configuration), "native": True,
            "scalar_run": scalar_dir.relative_to(root).as_posix(),
            "histogram_run": histogram_dir.relative_to(root).as_posix(),
            "image_run": (group / "figures").relative_to(root).as_posix(),
            "image_tags": [], "comparison_tags": [], "attempt": identity,
            "training_steps": training_steps, "validation_points": len(epochs)})
    return models


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
    request_paths = [path for path in (root / "execution/local_commands").glob("*/*/job.json") if path.is_file()]
    for record_index, request_path in enumerate(sorted(request_paths, key=lambda path: path.stat().st_mtime), 1):
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
                config_path = method_root / "config.json"
                config = _read(config_path) if config_path.is_file() and _inside(config_path, attempt) else {}
                raw_model = config.get("model")
                model_name = raw_model if isinstance(raw_model, str) else method_root.name
                scalar_dir = method_root / "tensorboard" if native else method_dir
                snapshot_dir = method_root / "tensorboard_snapshots"
                histogram_dir = snapshot_dir if snapshot_dir.is_dir() else scalar_dir
                report["models"].append({"id": identity + "/" + key, "name": key,
                    "label": model_name + " · " + (f"记录 {record_index} · " if len(request_paths) > 1 else "") + str(request.get("experiment_id", "")),
                    "is_baseline": _is_baseline(request, config), "native": native,
                    "scalar_run": scalar_dir.relative_to(root).as_posix(),
                    "histogram_run": histogram_dir.relative_to(root).as_posix(),
                    "image_run": (group / "figures").relative_to(root).as_posix(),
                    "image_tags": [], "comparison_tags": [], "attempt": identity,
                    "training_steps": previous["steps"], "validation_points": len(history)})
            registered = {model["name"] for model in report["models"] if model["attempt"] == identity}
            native_models = _native_models(root, attempt, group, request, identity, record_index,
                                            len(request_paths), registered)
            report["models"].extend(native_models)
            for model in native_models:
                state["models"][model["name"]] = {"steps": model["training_steps"], "history": -1}
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
                    for model_record in report["models"]:
                        if model_record["attempt"] != identity:
                            continue
                        method_name = Path(model_record["name"]).name
                        if key.startswith(model_record["name"] + "/") or key.startswith("visualizations/" + method_name + "/"):
                            model_record["image_tags"].append("figures/" + key)
                        if path.name.startswith("comparison_"):
                            model_record["comparison_tags"].append("figures/" + key)
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
