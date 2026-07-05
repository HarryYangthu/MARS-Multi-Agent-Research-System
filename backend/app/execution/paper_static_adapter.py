"""Adapter for Harry's real static PIMC training code.

The external paper code stays outside the MARS repository. This adapter invokes
``train_static.py`` through a configured Python interpreter, writes all outputs
under the MARS run directory, and maps the script's ``summary.json`` into the
standard execution result shape used by reports, diagnostics, and the workbench.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import yaml

from app.execution.mock_simulation import MockResult
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
) -> MockResult:
    """Run the external static PIMC script for one MARS experiment."""
    started = time.monotonic()
    cfg = _paper_static_config()
    run_root = Path(spec.run_root) if spec.run_root is not None else repo_root() / "runs" / spec.run_id
    logs_dir = run_root / "execution" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{_safe_name(spec.experiment_id)}_paper_static.log"

    try:
        repo_path = _resolve_path(str(cfg.get("repo_path", "")), repo_root())
        config_path = _resolve_path(str(cfg.get("config_path", "configs/static.yaml")), repo_path)
        data_path = _resolve_path(
            str(spec.config.get("data_path") or cfg.get("data_path", "")),
            repo_path,
        )
        python = _python_from_config(cfg)
    except ValueError as exc:
        _write_failure_log(log_path, str(exc))
        return _failed_result(spec, started, str(exc))

    dry_run = _bool_value(spec.config.get("dry_run", cfg.get("default_dry_run", False)))
    max_iters = _positive_int(spec.config.get("max_iters", cfg.get("default_max_iters")), max(1, steps))
    timeout = _positive_float(cfg.get("timeout_seconds"), 900.0)
    output_root = run_root / "execution" / "paper_static" / _safe_name(spec.experiment_id)
    output_root.mkdir(parents=True, exist_ok=True)

    validation_error = _validate_inputs(
        python=python,
        repo_path=repo_path,
        config_path=config_path,
        data_path=data_path,
    )
    if validation_error:
        _write_failure_log(log_path, validation_error)
        return _failed_result(spec, started, validation_error)

    tag = _safe_tag(f"mars_{spec.run_id}_{spec.experiment_id}")
    argv = [
        python,
        "train_static.py",
        "--cfg",
        str(config_path),
        "--max-iters",
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

    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(repo_path),
            env=_subprocess_env(run_root=run_root, spec=spec),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def read_stdout() -> None:
            nonlocal done_path
            if process.stdout is None:
                return
            async for raw in process.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                stdout_lines.append(line)
                parsed = _parse_epoch_line(line)
                if parsed is not None:
                    step = len(loss_curve)
                    loss_curve.append(parsed["loss"])
                    pim_db_curve.append(parsed["paper_PIM_db"])
                    res_db_curve.append(parsed["paper_RES_db"])
                    ape_db_curve.append(parsed["paper_APE_db"])
                    if bus_publish is not None:
                        await bus_publish(
                            channel,
                            {
                                "event": "execution.curve_point",
                                "experiment_id": spec.experiment_id,
                                "step": step,
                                "metric": "loss",
                                "value": parsed["loss"],
                                "paper_metrics": parsed,
                            },
                        )
                match = _DONE_RE.search(line)
                if match:
                    done_path = Path(match.group("path").strip())

        async def read_stderr() -> None:
            if process.stderr is None:
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
        try:
            process.kill()
            await process.wait()
        except Exception:
            pass
        stderr_lines.append(f"timed out after {timeout:.1f}s")
    except OSError as exc:
        stderr_lines.append(str(exc))

    summary_path = _summary_path(output_root=output_root, done_path=done_path)
    summary = _read_json(summary_path) if summary_path is not None else {}
    metrics = _metrics_from_summary(summary)
    if not metrics and loss_curve:
        metrics = {"loss": loss_curve[-1], "RES": 10.0 * math.log10(loss_curve[-1])}
    metrics.setdefault("returncode", float(returncode))
    metrics.setdefault("dry_run", 1.0 if dry_run else 0.0)
    metrics.setdefault("max_iters", float(max_iters))
    if summary_path is not None:
        metrics.setdefault("summary_written", 1.0)

    status = "completed" if returncode == 0 else "failed"
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

    fingerprint_hash = "sha256:" + hashlib.sha256(
        json.dumps(
            {
                "project": spec.project,
                "run_id": spec.run_id,
                "experiment_id": spec.experiment_id,
                "config": spec.config,
                "data_path": str(data_path),
                "summary": summary,
                "returncode": returncode,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()[:24]

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

    return MockResult(
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
    raw = yaml.safe_load((repo_root() / "configs" / "execution.yaml").read_text(encoding="utf-8")) or {}
    execution = raw.get("execution", {})
    if not isinstance(execution, dict):
        return {}
    cfg = execution.get("paper_static", {})
    return cfg if isinstance(cfg, dict) else {}


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
    return {
        **os.environ,
        "MARS_RUN_ROOT": str(run_root),
        "MARS_RUN_ID": str(spec.run_id),
        "MARS_EXPERIMENT_ID": str(spec.experiment_id),
        "MARS_PROJECT": str(spec.project),
        "PYTHONUNBUFFERED": "1",
    }


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
        "RES": -ape,
        "loss": 10.0 ** (-ape / 10.0),
    }


def _summary_path(*, output_root: Path, done_path: Path | None) -> Path | None:
    if done_path is not None:
        candidate = done_path / "summary.json"
        if candidate.is_file():
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
        metrics["RES"] = -ape
        metrics["loss"] = 10.0 ** (-ape / 10.0)
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


def _failed_result(spec: Any, started: float, message: str) -> MockResult:
    return MockResult(
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
