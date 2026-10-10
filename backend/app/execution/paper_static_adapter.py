"""Adapter for Harry's real static PIMC training code.

The external paper code stays outside the MARS repository. This adapter invokes
``train_static.py`` through a configured Python interpreter, writes all outputs
under the MARS run directory, and maps the script's ``summary.json`` into the
standard execution result shape used by reports, diagnostics, and the workbench.
"""
from __future__ import annotations

import asyncio
import ast
import hashlib
import json
import math
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from app.execution.results import SimulationResult
from app.execution.paper_static_protocol import static_summary_errors, static_training_protocol
from app.execution.subprocess_env import sanitized_subprocess_environment
from app.harness.tools.process_runtime import start_process, terminate_process_tree
from app.settings import repo_root

_EPOCH_RE = re.compile(
    r"epoch:\s+\S+.*?PIM:\s+([-+]?\d+(?:\.\d+)?)\s+"
    r"RES:\s+([-+]?\d+(?:\.\d+)?)\s+APE:\s+([-+]?\d+(?:\.\d+)?)"
)
_DONE_RE = re.compile(r"done\s+->\s+(?P<path>.+)$")


async def run_paper_static_simulation(
    spec: Any,
    *,
    bus_publish: Any | None = None,
    steps: int = 1,
) -> SimulationResult:
    """Run the external static PIMC script for one MARS experiment."""
    started = time.monotonic()
    cfg = _paper_static_config()
    run_root = Path(spec.run_root) if spec.run_root is not None else repo_root() / "runs" / spec.run_id
    logs_dir = run_root / "execution" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{_safe_name(spec.experiment_id)}_paper_static.log"

    try:
        from app.harness.tools.git_branch import current_git_branch
        branch = current_git_branch(spec.project, spec.run_id)
        # Host binding wins over a global adapter path or model arguments.
        repo_path = branch.root if branch is not None else _resolve_path(str(cfg.get("repo_path", "")), repo_root())
        config_path = approved_config_path(spec.config, cfg, repo_path)
        data_path = _resolve_path(
            str(spec.config.get("data_path") or cfg.get("data_path", "")),
            repo_path,
        )
        python = _python_from_config(cfg)
    except ValueError as exc:
        _write_failure_log(log_path, str(exc))
        return _failed_result(spec, started, str(exc))

    dry_run = _bool_value(spec.config.get("dry_run", cfg.get("default_dry_run", False)))
    from app.harness.schema.experiment_contract import budget
    unit, max_iters = budget(spec.config)
    timeout = _positive_float(cfg.get("timeout_seconds"), 900.0)
    output_root = run_root / "execution" / "paper_static" / _safe_name(spec.experiment_id) / uuid.uuid4().hex
    output_root.mkdir(parents=True, exist_ok=False)

    validation_error = _validate_inputs(
        python=python,
        repo_path=repo_path,
        config_path=config_path,
        data_path=data_path,
    )
    if validation_error:
        _write_failure_log(log_path, validation_error)
        return _failed_result(spec, started, validation_error)

    try:
        protocol = static_training_protocol(config_path, _override_args(spec.config, cfg), unit=unit, count=max_iters)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        _write_failure_log(log_path, str(exc))
        return _failed_result(spec, started, str(exc))

    tag = _safe_tag(f"mars_{spec.run_id}_{spec.experiment_id}")
    argv = [
        python,
        "train_static.py",
        "--cfg",
        str(config_path),
        "--max-steps" if unit == "steps" else "--max-iters",
        str(max_iters),
        "--tag",
        tag,
        "--set",
        f"data.path={data_path}",
        "--set",
        f"output_dir={output_root}",
    ]
    if dry_run:
        argv.append("--dry-run")
    argv.extend(_override_args(spec.config, cfg))

    channel = f"run.{spec.run_id}.experiment.{spec.experiment_id}"
    if bus_publish is not None:
        await bus_publish(
            channel,
            {
                "event": "execution.started",
                "experiment_id": spec.experiment_id,
                "kind": "paper_static",
                "data_path": str(data_path),
                "config_path": str(config_path),
                "max_iters": max_iters,
                "budget_unit": unit,
                "dry_run": dry_run,
            },
        )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    loss_curve: list[float] = []
    pim_db_curve: list[float] = []
    res_db_curve: list[float] = []
    ape_db_curve: list[float] = []
    done_path: Path | None = None
    returncode = -1
    timed_out = False
    process: asyncio.subprocess.Process | None = None

    try:
        process = await start_process(
            argv,
            backend=str(cfg.get("process_backend", "local_process")),
            require_isolation=bool(cfg.get("require_isolation", False)),
            cwd=str(repo_path),
            env=_subprocess_env(run_root=run_root, spec=spec),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def read_stdout() -> None:
            nonlocal done_path
            if process is None or process.stdout is None:
                return
            async for raw in process.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                stdout_lines.append(line)
                progress = _parse_step_line(line)
                parsed = progress if unit == "steps" else _parse_epoch_line(line)
                if parsed is not None:
                    curve_metric = "training_loss" if unit == "steps" else "cancellation_residual_ratio"
                    step = int(parsed.get("optimizer_step", len(loss_curve)))
                    loss_curve.append(parsed["loss"])
                    if "paper_RES_db" in parsed:
                        pim_db_curve.append(parsed["paper_PIM_db"])
                        res_db_curve.append(parsed["paper_RES_db"])
                        ape_db_curve.append(parsed["paper_APE_db"])
                    from app.execution.curve_parser import write_curve
                    write_curve(run_root=run_root, experiment_id=spec.experiment_id,
                                metric_name=curve_metric, values=loss_curve)
                    if bus_publish is not None:
                        await bus_publish(
                            channel,
                            {
                                "event": "execution.curve_point",
                                "experiment_id": spec.experiment_id,
                                "step": step,
                                "metric": curve_metric,
                                "value": parsed["loss"],
                                "paper_metrics": parsed,
                            },
                        )
                match = _DONE_RE.search(line)
                if match:
                    done_path = Path(match.group("path").strip())

        async def read_stderr() -> None:
            if process is None or process.stderr is None:
                return
            async for raw in process.stderr:
                stderr_lines.append(raw.decode("utf-8", errors="replace").rstrip())

        await asyncio.wait_for(
            asyncio.gather(read_stdout(), read_stderr(), process.wait()),
            timeout=timeout,
        )
        returncode = int(process.returncode or 0)
    except asyncio.TimeoutError:
        timed_out = True
        returncode = -1
        stderr_lines.append(f"timed out after {timeout:.1f}s")
    except OSError as exc:
        stderr_lines.append(str(exc))
    finally:
        if process is not None:
            await terminate_process_tree(process)

    summary_path = _summary_path(output_root=output_root, done_path=done_path)
    summary = _read_json(summary_path) if summary_path is not None else {}
    metrics = _metrics_from_summary(summary)
    has_measurements = summary_path is not None and bool(metrics) and all(math.isfinite(value) for value in metrics.values())
    protocol_errors = static_summary_errors(summary, unit=unit, count=max_iters, seed=spec.seed)
    if protocol_errors:
        has_measurements = False
        stderr_lines.extend(protocol_errors)
    metrics.setdefault("returncode", float(returncode))
    metrics.setdefault("dry_run", 1.0 if dry_run else 0.0)
    metrics.setdefault("max_iters", float(max_iters))
    if summary_path is not None:
        metrics.setdefault("summary_written", 1.0)

    status = "completed" if returncode == 0 and has_measurements and not dry_run else "failed"
    if not has_measurements:
        stderr_lines.append("execution produced no finite research measurements")
    if timed_out:
        status = "failed"
    if loss_curve == [] and "loss" in metrics:
        loss_curve = [float(metrics["loss"])]

    paper_metrics_plot_path: Path | None = None
    if pim_db_curve or res_db_curve or ape_db_curve:
        try:
            from app.execution.pim_cancellation import plot_paper_metric_curve

            candidate = output_root / "paper_metrics_curve.png"
            plot_paper_metric_curve(
                pim_db=pim_db_curve,
                res_db=res_db_curve,
                ape_db=ape_db_curve,
                path=candidate,
                title=f"{spec.experiment_id} Training Metrics",
            )
            if candidate.is_file():
                paper_metrics_plot_path = candidate
        except Exception as exc:
            stderr_lines.append(f"paper metrics plot failed: {exc}")

    _write_log(
        log_path=log_path,
        argv=argv,
        cwd=repo_path,
        returncode=returncode,
        stdout_lines=stdout_lines,
        stderr_lines=stderr_lines,
        summary_path=summary_path,
    )
    _write_manifest(
        run_root=run_root,
        experiment_id=spec.experiment_id,
        payload={
            "backend": "paper_static",
            "repo_path": str(repo_path),
            "config_path": str(config_path),
            "data_path": str(data_path),
            "data_source_id": str(spec.config.get("data_source_id") or ""),
            "fs_mhz": spec.config.get("fs_mhz"),
            "python": python,
            "output_root": str(output_root),
            "summary_path": str(summary_path) if summary_path is not None else "",
            "log_path": str(log_path),
            "paper_metrics_plot_path": (
                str(paper_metrics_plot_path) if paper_metrics_plot_path is not None else ""
            ),
            "returncode": returncode,
            "status": status,
            "dry_run": dry_run,
            "max_iters": max_iters,
        },
    )

    from app.harness.persistence import atomic_write_json
    receipt_path = output_root / "execution_receipt.json"
    atomic_write_json(receipt_path, {"schema": "paper_static_receipt.v1", "run_id": spec.run_id,
        "experiment_id": spec.experiment_id, "status": status, "returncode": returncode,
        "config": spec.config, "training_protocol": protocol, "config_path": str(config_path),
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(), "argv": argv,
        "summary_path": str(summary_path) if summary_path is not None else None,
        "summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest() if summary_path is not None else None,
        "log_path": str(log_path), "log_sha256": hashlib.sha256(log_path.read_bytes()).hexdigest()})
    fingerprint_hash = "sha256:" + hashlib.sha256(receipt_path.read_bytes()).hexdigest()

    if bus_publish is not None:
        await bus_publish(
            channel,
            {
                "event": "execution.completed" if status == "completed" else "execution.failed",
                "experiment_id": spec.experiment_id,
                "fingerprint_hash": fingerprint_hash,
                "metrics": metrics,
                "backend": "paper_static",
                "log_file": log_path.name,
            },
        )

    return SimulationResult(
        run_id=spec.run_id,
        experiment_id=spec.experiment_id,
        duration_seconds=time.monotonic() - started,
        status=status,
        metrics=metrics,
        fingerprint_hash=fingerprint_hash,
        is_mock=False,
        loss_curve=loss_curve,
    )


def paper_static_readiness() -> dict[str, Any]:
    """Return filesystem/dependency readiness details for UI status panels."""
    cfg = _paper_static_config()
    repo_path = _resolve_path(str(cfg.get("repo_path", "")), repo_root())
    config_path = _resolve_path(str(cfg.get("config_path", "configs/static.yaml")), repo_path)
    data_path = _resolve_path(str(cfg.get("data_path", "")), repo_path)
    python = _python_from_config(cfg)
    python_exists = _python_exists(python)
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "python": python,
        "python_exists": python_exists,
        "repo_path": str(repo_path),
        "repo_exists": repo_path.is_dir(),
        "config_path": str(config_path),
        "config_exists": config_path.is_file(),
        "data_path": str(data_path),
        "data_exists": data_path.is_file(),
        "default_max_iters": _positive_int(cfg.get("default_max_iters"), 1),
        "default_dry_run": _bool_value(cfg.get("default_dry_run", False)),
    }


def _paper_static_config() -> dict[str, Any]:
    from app.harness.tools.config import load_execution_config
    raw = load_execution_config()
    execution = raw.get("execution", {})
    if not isinstance(execution, dict):
        return {}
    cfg = execution.get("paper_static", {})
    return cfg if isinstance(cfg, dict) else {}


def approved_config_path(config: dict[str, Any], policy: dict[str, Any], root: Path) -> Path:
    """Use this job's approved file, never silently substitute a global template."""
    raw = config.get("config_path")
    if not isinstance(raw, str) or not raw:
        raise ValueError("编码交付缺少本组实验 config_path；请绑定已批准配置文件")
    path = _resolve_path(raw, root)
    if not path.is_relative_to(root.resolve()) or any(part.startswith(".") for part in Path(raw).parts):
        raise ValueError("实验配置文件必须位于绑定代码目录中")
    if config.get("entrypoint") != "train_static.py":
        raise ValueError("paper_static 的 entrypoint 只能填写 train_static.py（纯脚本路径，不含 python 或命令参数）；"
                         "配置路径放在 config_path，训练预算放在 budget_steps 或 budget_unit/max_iters")
    from app.harness.schema.experiment_contract import budget
    unit, _ = budget(config)
    if unit == "steps":
        tree = ast.parse((root / "train_static.py").read_text())
        supported = any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument" and any(isinstance(arg, ast.Constant)
            and arg.value == "--max-steps" for arg in node.args) for node in ast.walk(tree))
        if not supported:
            raise ValueError("交付入口尚不支持 --max-steps，不能将训练步数换成轮数")
    return path


def _python_from_config(cfg: dict[str, Any]) -> str:
    return str(
        os.environ.get("MARS_PAPER_STATIC_PYTHON")
        or cfg.get("python")
        or "python"
    )


def _resolve_path(raw: str, base: Path) -> Path:
    if not raw:
        raise ValueError("paper_static path is empty")
    expanded = Path(raw).expanduser()
    return expanded.resolve() if expanded.is_absolute() else (base / expanded).resolve()


def _validate_inputs(*, python: str, repo_path: Path, config_path: Path, data_path: Path) -> str:
    if not _python_exists(python):
        return f"paper_static python is not executable or not on PATH: {python}"
    if not repo_path.is_dir():
        return f"paper_static repo_path does not exist: {repo_path}"
    if not (repo_path / "train_static.py").is_file():
        return f"train_static.py not found under paper_static repo_path: {repo_path}"
    if not config_path.is_file():
        return f"paper_static config_path does not exist: {config_path}"
    if not data_path.is_file():
        return f"paper_static data_path does not exist: {data_path}"
    return ""


def _python_exists(python: str) -> bool:
    candidate = Path(python).expanduser()
    if candidate.is_absolute():
        return candidate.is_file() and os.access(candidate, os.X_OK)
    return shutil.which(python) is not None


def _subprocess_env(*, run_root: Path, spec: Any) -> dict[str, str]:
    return sanitized_subprocess_environment(
        overrides={
            "MARS_RUN_ROOT": str(run_root),
            "MARS_RUN_ID": str(spec.run_id),
            "MARS_EXPERIMENT_ID": str(spec.experiment_id),
            "MARS_PROJECT": str(spec.project),
            "PYTHONUNBUFFERED": "1",
        }
    )


def _override_args(config: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
    allowed = cfg.get("allowed_overrides", [])
    if not isinstance(allowed, list):
        allowed = []
    pairs: list[tuple[str, Any]] = []
    for key in allowed:
        key_s = str(key)
        if key_s in config:
            pairs.append((key_s, config[key_s]))
    for source, target in {
        "learning_rate": "lr_init",
        "lr": "lr_init",
        "lut_n_spline": "model.lut_n_spline",
        "lut_rmax": "model.lut_rmax",
        "lut_init": "model.lut_init",
    }.items():
        if source in config:
            pairs.append((target, config[source]))
    args: list[str] = []
    for key, value in pairs:
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, int | float | str):
            rendered = str(value)
        else:
            continue
        args.extend(["--set", f"{key}={rendered}"])
    return args


def _parse_step_line(line: str) -> dict[str, float] | None:
    """Only accept actual finite optimizer-update observations."""
    if not line.startswith("mars.progress "):
        return None
    try:
        row = json.loads(line.removeprefix("mars.progress "))
        step, loss = row["optimizer_step"], row["training_loss"]
        if type(step) is not int or step < 1 or type(loss) not in (int, float) or not math.isfinite(loss):
            return None
        values = {"optimizer_step": float(step), "loss": float(loss)}
        for source, target in (("PIM", "paper_PIM_db"), ("RES", "paper_RES_db"), ("APE", "paper_APE_db")):
            if source in row and type(row[source]) in (int, float) and math.isfinite(row[source]):
                values[target] = float(row[source])
        return values
    except (ValueError, KeyError, TypeError):
        return None


def _parse_epoch_line(line: str) -> dict[str, float] | None:
    match = _EPOCH_RE.search(line)
    if not match:
        return None
    pim = float(match.group(1))
    res = float(match.group(2))
    ape = float(match.group(3))
    return {
        "paper_PIM_db": pim,
        "paper_RES_db": res,
        "paper_APE_db": ape,
        "PIM": pim,
        "APE": ape,
        "RES": res,
        "loss": 10.0 ** (-ape / 10.0),
        "cancellation_residual_ratio": 10.0 ** (-ape / 10.0),
    }


def _summary_path(*, output_root: Path, done_path: Path | None) -> Path | None:
    if done_path is not None:
        candidate = done_path / "summary.json"
        if candidate.is_file() and candidate.resolve().is_relative_to(output_root.resolve()):
            return candidate
    summaries = sorted(output_root.glob("*/summary.json"), key=lambda p: p.stat().st_mtime)
    return summaries[-1] if summaries else None


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _metrics_from_summary(summary: dict[str, Any]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for raw_key, out_key in {
        "epochs": "epochs",
        "optimizer_steps": "optimizer_steps",
        "parameter_counts": "parameter_counts",
        "seed": "seed",
        "loss": "loss",
        "loss_max": "loss_max",
        "PIM": "paper_PIM_db",
        "RES": "paper_RES_db",
        "APE": "paper_APE_db",
        "mean_gain": "paper_mean_gain_db",
        "channels": "channels",
    }.items():
        value = summary.get(raw_key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            metrics[out_key] = float(value)
    if "paper_PIM_db" in metrics:
        metrics["PIM"] = metrics["paper_PIM_db"]
    if "paper_APE_db" in metrics:
        ape = metrics["paper_APE_db"]
        metrics["APE"] = ape
        metrics["cancellation_residual_ratio"] = 10.0 ** (-ape / 10.0)
        metrics.setdefault("loss", metrics["cancellation_residual_ratio"])
    if "paper_RES_db" in metrics:
        metrics["RES"] = metrics["paper_RES_db"]
    return metrics


def _write_log(
    *,
    log_path: Path,
    argv: list[str],
    cwd: Path,
    returncode: int,
    stdout_lines: list[str],
    stderr_lines: list[str],
    summary_path: Path | None,
) -> None:
    log_path.write_text(
        "\n".join(
            [
                "backend=paper_static",
                "cwd=" + str(cwd),
                "argv=" + json.dumps(argv, ensure_ascii=False),
                f"returncode={returncode}",
                "summary_path=" + (str(summary_path) if summary_path is not None else ""),
                "--- stdout ---",
                *stdout_lines,
                "--- stderr ---",
                *stderr_lines,
            ]
        ),
        encoding="utf-8",
    )


def _write_failure_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(["backend=paper_static", "returncode=-1", "--- stderr ---", message]),
        encoding="utf-8",
    )


def _write_manifest(*, run_root: Path, experiment_id: str, payload: dict[str, Any]) -> None:
    target_dir = run_root / "execution" / "paper_static"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{_safe_name(experiment_id)}_manifest.json"
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _failed_result(spec: Any, started: float, message: str) -> SimulationResult:
    return SimulationResult(
        run_id=spec.run_id,
        experiment_id=spec.experiment_id,
        duration_seconds=time.monotonic() - started,
        status="failed",
        metrics={"error": 1.0},
        fingerprint_hash="sha256:" + hashlib.sha256(message.encode("utf-8")).hexdigest()[:24],
        is_mock=False,
        loss_curve=[],
    )


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value) or "experiment"


def _safe_tag(value: str) -> str:
    return _safe_name(value)[:80]


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)
