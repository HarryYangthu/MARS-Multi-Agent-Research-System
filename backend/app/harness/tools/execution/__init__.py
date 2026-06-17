"""Execution tools registered through the generic tool registry.

Retrieval-style tools (``metrics_collector`` / ``log_streamer``) read artifacts
of an already-executed run so an Agent can reason over results during its
gather loop. Action-style tools (``simulation_runner`` / ``batch_runner``) are
safe dispatch façades: the bridge injects callbacks through ``ToolContext`` so
``harness/`` stays dependency-clean and still gets Gate/audit coverage.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.tools.config import load_execution_config, tool_config
from app.harness.tools.registry import ToolContext, ToolResult
from app.settings import repo_root


def _exec_dir(run_id: str) -> Path:
    return repo_root() / "runs" / run_id / "execution"


def _run_root(ctx: ToolContext, run_id: str) -> Path:
    raw = ctx.extra.get("run_root") if ctx.extra else None
    if raw:
        return Path(str(raw))
    return repo_root() / "runs" / run_id


def _exec_dir_for(ctx: ToolContext, run_id: str) -> Path:
    return _run_root(ctx, run_id) / "execution"


def _steps_from_args(args: dict[str, Any]) -> int:
    cfg = load_execution_config()["execution"]
    raw = args.get("steps", cfg.get("batch_steps", 120))
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 120


def _backend_from_args(args: dict[str, Any]) -> str:
    cfg = load_execution_config()["execution"]
    return str(args.get("backend") or cfg.get("backend") or "mock")


def _float_arg(args: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(args.get(key, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class _ExecutionSpec:
    run_id: str
    experiment_id: str
    project: str
    config: dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 5.0
    seed: int | None = None
    template: str = "exponential_decay"
    run_root: Path | None = None
    plot_every_steps: int = 5


@dataclass(frozen=True)
class _ExecutionResult:
    run_id: str
    experiment_id: str
    duration_seconds: float
    status: str
    metrics: dict[str, float]
    fingerprint_hash: str
    is_mock: bool
    loss_curve: list[float]


@dataclass(frozen=True)
class _LocalCommandSpec:
    id: str
    label: str
    argv: tuple[str, ...]


def _job_spec_from_args(
    args: dict[str, Any],
    ctx: ToolContext,
    *,
    default_id: str = "exp1",
) -> _ExecutionSpec:
    run_id = str(args.get("run_id") or ctx.run_id or "")
    project = str(args.get("project") or ctx.project or "")
    experiment_id = str(args.get("experiment_id") or args.get("id") or default_id)
    config = args.get("config", {})
    if not isinstance(config, dict):
        config = {}
    seed_raw = args.get("seed")
    seed = int(seed_raw) if isinstance(seed_raw, int | str) and str(seed_raw).isdigit() else None
    return _ExecutionSpec(
        run_id=run_id,
        experiment_id=experiment_id,
        project=project,
        config=config,
        duration_seconds=_float_arg(args, "duration_seconds", 5.0),
        seed=seed,
        template=str(args.get("template") or "exponential_decay"),
        run_root=_run_root(ctx, run_id),
    )


def _persist_results(
    *,
    run_root: Path,
    project: str,
    results: list[_ExecutionResult],
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    logs_dir = run_root / "execution" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = _write_metrics_json(run_root=run_root, results=results)
    artifacts.append({"kind": "metrics", "path": str(metrics_path)})
    for result in results:
        run_log = _write_run_log(run_root=run_root, result=result, project=project)
        artifacts.append({"kind": "run_log", "path": str(run_log)})
        log_path = logs_dir / f"{result.experiment_id}.log"
        log_path.write_text(
            "\n".join(
                [
                    f"experiment_id={result.experiment_id}",
                    f"status={result.status}",
                    f"fingerprint_hash={result.fingerprint_hash}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        artifacts.append({"kind": "log", "path": str(log_path)})
        if result.loss_curve:
            curve = _write_curve(
                run_root=run_root,
                experiment_id=result.experiment_id,
                metric_name="loss",
                values=result.loss_curve,
            )
            artifacts.append({"kind": "curve", "path": str(curve)})
    return artifacts


def _seed_for(spec: _ExecutionSpec) -> int:
    if spec.seed is not None:
        return spec.seed
    raw = f"{spec.run_id}:{spec.experiment_id}:{spec.config}"
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8], 16)


def _run_mock_spec(spec: _ExecutionSpec, *, steps: int) -> _ExecutionResult:
    started = time.monotonic()
    seed = _seed_for(spec)
    n_steps = max(1, steps)
    expert_count = _config_float(spec.config, "expert_count", 8.0)
    learning_rate = _config_float(spec.config, "learning_rate", 0.06)
    floor = max(0.008, 0.022 - min(expert_count, 16.0) * 0.0005)
    decay = max(0.020, min(0.090, learning_rate))
    phase = (seed % 17) / 17.0
    loss_curve = [
        float(floor + 0.34 * math.exp(-decay * step) + 0.002 * math.sin(step * 0.7 + phase))
        for step in range(n_steps)
    ]
    final_loss = max(0.001, loss_curve[-1])
    res = -20.0 - min(expert_count, 16.0) * 1.5
    metrics = {
        "loss": round(final_loss, 6),
        "RES": round(res, 3),
        "PIM": round(-res, 3),
        "APE": round(max(0.4, 5.0 / max(1.0, expert_count)), 3),
    }
    fingerprint_hash = "sha256:" + hashlib.sha256(
        f"{spec.project}:{spec.run_id}:{spec.experiment_id}:{spec.config}:harness-mock".encode(
            "utf-8"
        )
    ).hexdigest()[:24]
    return _ExecutionResult(
        run_id=spec.run_id,
        experiment_id=spec.experiment_id,
        duration_seconds=time.monotonic() - started,
        status="completed",
        metrics=metrics,
        fingerprint_hash=fingerprint_hash,
        is_mock=True,
        loss_curve=loss_curve,
    )


def _config_float(config: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(config.get(key, default))
    except (TypeError, ValueError):
        return default


def _write_run_log(
    *,
    run_root: Path,
    result: _ExecutionResult,
    project: str,
) -> Path:
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
        f"# Run log - {result.experiment_id}\n\n"
        f"Harness mock simulation completed at {datetime.now(tz=timezone.utc).isoformat()}.\n"
    )
    target_dir = run_root / "execution"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"run_log_{result.experiment_id}.v1.md"
    target.write_text(fm_dumps(metadata, body), encoding="utf-8")
    return target


def _write_metrics_json(*, run_root: Path, results: list[_ExecutionResult]) -> Path:
    target = run_root / "execution" / "metrics.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "run_id": f"{result.run_id}_{result.experiment_id}",
            "metrics": result.metrics,
            "fingerprint_hash": result.fingerprint_hash,
            "duration_seconds": result.duration_seconds,
        }
        for result in results
    ]
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def _write_curve(
    *,
    run_root: Path,
    experiment_id: str,
    metric_name: str,
    values: list[float],
) -> Path:
    target_dir = run_root / "execution" / "curves"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{experiment_id}_{metric_name}.json"
    target.write_text(
        json.dumps(
            {
                "experiment_id": experiment_id,
                "metric": metric_name,
                "values": values,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return target


def _local_commands() -> tuple[_LocalCommandSpec, ...]:
    cfg = load_execution_config()["execution"]
    raw_commands = cfg.get("local_commands", [])
    if not isinstance(raw_commands, list):
        return ()
    out: list[_LocalCommandSpec] = []
    for index, item in enumerate(raw_commands):
        if not isinstance(item, dict):
            continue
        argv_raw = item.get("argv", [])
        if not isinstance(argv_raw, list) or not all(isinstance(part, str) for part in argv_raw):
            continue
        if not argv_raw:
            continue
        out.append(
            _LocalCommandSpec(
                id=str(item.get("id") or f"local_{index + 1}"),
                label=str(item.get("label") or item.get("id") or f"local command {index + 1}"),
                argv=tuple(argv_raw),
            )
        )
    return tuple(out)


def _command_allowed(
    argv: tuple[str, ...],
    allowlist: tuple[tuple[str, ...], ...],
) -> bool:
    if not allowlist:
        return False
    return any(len(argv) >= len(prefix) and argv[: len(prefix)] == prefix for prefix in allowlist)


def _select_local_command(args: dict[str, Any], *, tool_name: str) -> _LocalCommandSpec | ToolResult:
    requested = str(args.get("command_id", "") or "")
    commands = _local_commands()
    if requested:
        commands = tuple(command for command in commands if command.id == requested)
    if not commands:
        return ToolResult(ok=False, error="no local_command is configured for execution tools")
    command = commands[0]
    allowlist = tool_config(tool_name).command_allowlist
    if not _command_allowed(command.argv, allowlist):
        return ToolResult(
            ok=False,
            error=f"{command.id} is not allowlisted for {tool_name}",
            output={"argv": list(command.argv)},
        )
    return command


async def _run_local_command(
    *,
    args: dict[str, Any],
    ctx: ToolContext,
    spec: _ExecutionSpec,
    tool_name: str,
) -> tuple[_ExecutionResult, list[dict[str, Any]]]:
    selected = _select_local_command(args, tool_name=tool_name)
    if isinstance(selected, ToolResult):
        raise RuntimeError(selected.error or "local command is not available")
    run_root = _run_root(ctx, spec.run_id)
    logs_dir = run_root / "execution" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    timeout = _command_timeout_seconds()
    env = {
        **os.environ,
        "MARS_RUN_ID": spec.run_id,
        "MARS_EXPERIMENT_ID": spec.experiment_id,
        "MARS_PROJECT": spec.project,
        "MARS_RUN_ROOT": str(run_root),
    }
    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *selected.argv,
        cwd=str(run_root),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    timed_out = False
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        timed_out = True
        process.kill()
        await process.wait()
        stdout, stderr = b"", f"timed out after {timeout}s".encode()
    duration = time.monotonic() - started
    stdout_text = stdout.decode("utf-8", errors="replace")
    stderr_text = stderr.decode("utf-8", errors="replace")
    returncode = -1 if timed_out else int(process.returncode or 0)
    log_path = logs_dir / f"{spec.experiment_id}_{selected.id}.log"
    log_path.write_text(
        "\n".join(
            [
                f"command_id={selected.id}",
                "argv=" + json.dumps(list(selected.argv), ensure_ascii=False),
                f"returncode={returncode}",
                "--- stdout ---",
                stdout_text,
                "--- stderr ---",
                stderr_text,
            ]
        ),
        encoding="utf-8",
    )
    metrics = _metrics_from_command_output(stdout_text)
    metrics.setdefault("returncode", float(returncode))
    result = _ExecutionResult(
        run_id=spec.run_id,
        experiment_id=spec.experiment_id,
        duration_seconds=duration,
        status="completed" if returncode == 0 else "failed",
        metrics=metrics,
        fingerprint_hash="sha256:" + hashlib.sha256(
            f"{spec.project}:{spec.run_id}:{spec.experiment_id}:{selected.argv}:{returncode}".encode(
                "utf-8"
            )
        ).hexdigest()[:24],
        is_mock=False,
        loss_curve=[],
    )
    return result, [{"kind": "local_command_log", "path": str(log_path)}]


def _metrics_from_command_output(stdout_text: str) -> dict[str, float]:
    lines = [line.strip() for line in stdout_text.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        raw_metrics = parsed.get("metrics", parsed)
        if not isinstance(raw_metrics, dict):
            continue
        metrics: dict[str, float] = {}
        for key, value in raw_metrics.items():
            try:
                metrics[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
        if metrics:
            return metrics
    return {}


def _command_timeout_seconds() -> float:
    cfg = load_execution_config()["execution"]
    try:
        return float(cfg.get("command_timeout_seconds", 60) or 60)
    except (TypeError, ValueError):
        return 60.0


def _backend_unavailable_result(*, backend: str, run_id: str) -> ToolResult:
    if backend == "remote_gpu":
        return ToolResult(
            ok=False,
            status="blocked",
            error="remote_gpu execution backend is interface-only in this deployment",
            output={
                "backend": backend,
                "run_id": run_id,
                "recommended_action": "use bridge callback or configure remote executor",
            },
        )
    return ToolResult(
        ok=False,
        error=f"execution backend '{backend}' requires a bridge-provided execution callback",
        output={"backend": backend, "run_id": run_id},
    )


async def metrics_collector_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Read ``runs/<run_id>/execution/metrics.json`` for the current run."""
    run_id = str(args.get("run_id") or ctx.run_id or "")
    if not run_id:
        return ToolResult(ok=False, error="run_id is required")
    path = _exec_dir_for(ctx, run_id) / "metrics.json"
    if not path.is_file():
        return ToolResult(ok=True, output={"run_id": run_id, "metrics": None,
                                           "note": "metrics.json 尚未生成"})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ToolResult(ok=False, error=f"failed to read metrics: {exc}")
    experiment = str(args.get("experiment_id", "") or "")
    if experiment and isinstance(data, list):
        data = [row for row in data if experiment in str(row.get("run_id", ""))]
    return ToolResult(ok=True, output={"run_id": run_id, "metrics": data})


async def log_streamer_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Return the tail of the execution log for the current run."""
    run_id = str(args.get("run_id") or ctx.run_id or "")
    if not run_id:
        return ToolResult(ok=False, error="run_id is required")
    tail = int(args.get("tail", 40) or 40)
    exec_dir = _exec_dir_for(ctx, run_id)
    log_files = []
    if exec_dir.exists():
        log_files = sorted([*exec_dir.glob("*.log"), *exec_dir.glob("logs/*.log")])
    if not log_files:
        return ToolResult(ok=True, output={"run_id": run_id, "lines": [],
                                           "note": "暂无执行日志"})
    lines = log_files[-1].read_text(encoding="utf-8", errors="replace").splitlines()
    return ToolResult(
        ok=True,
        output={"run_id": run_id, "log_file": log_files[-1].name, "lines": lines[-tail:]},
    )


async def simulation_runner_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Run one simulation when a bridge/system callback is provided."""
    runner = ctx.extra.get("simulation_runner") if ctx.extra else None
    if callable(runner):
        result = await runner(args, ctx)
        return result if isinstance(result, ToolResult) else ToolResult(ok=True, output=result)
    spec = _job_spec_from_args(args, ctx)
    if not spec.run_id:
        return ToolResult(ok=False, error="run_id is required")
    backend = _backend_from_args(args)
    command_artifacts: list[dict[str, Any]] = []
    if backend == "mock":
        result = _run_mock_spec(spec, steps=_steps_from_args(args))
    elif backend == "local_command":
        try:
            result, command_artifacts = await _run_local_command(
                args=args,
                ctx=ctx,
                spec=spec,
                tool_name="execution.simulation_runner",
            )
        except RuntimeError as exc:
            return ToolResult(ok=False, error=str(exc), output={"backend": backend})
    else:
        return _backend_unavailable_result(backend=backend, run_id=spec.run_id)
    artifacts = _persist_results(
        run_root=_run_root(ctx, spec.run_id),
        project=spec.project,
        results=[result],
    ) + command_artifacts
    return ToolResult(
        ok=result.status == "completed",
        output={
            "backend": backend,
            "run_id": spec.run_id,
            "experiment_id": result.experiment_id,
            "status": result.status,
            "metrics": result.metrics,
            "fingerprint_hash": result.fingerprint_hash,
        },
        artifacts=artifacts,
        metrics=dict(result.metrics),
    )


async def batch_runner_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Run an execution batch through a bridge/system-provided callback."""
    runner = ctx.extra.get("batch_runner") if ctx.extra else None
    if callable(runner):
        result = await runner(args, ctx)
        return result if isinstance(result, ToolResult) else ToolResult(ok=True, output=result)

    run_id = str(args.get("run_id") or ctx.run_id or "")
    if not run_id:
        return ToolResult(ok=False, error="run_id is required")
    raw_experiments = args.get("experiments", [{"experiment_id": "exp1", "config": {}}])
    if not isinstance(raw_experiments, list) or not raw_experiments:
        return ToolResult(ok=False, error="experiments must be a non-empty list")
    specs = []
    for i, item in enumerate(raw_experiments, start=1):
        if isinstance(item, dict):
            specs.append(_job_spec_from_args({**item, "run_id": run_id}, ctx, default_id=f"exp{i}"))
        else:
            specs.append(
                _job_spec_from_args(
                    {"run_id": run_id, "experiment_id": str(item), "config": {}},
                    ctx,
                    default_id=f"exp{i}",
                )
            )
    cfg = load_execution_config()["execution"]
    backend = _backend_from_args(args)
    if backend not in {"mock", "local_command"}:
        return _backend_unavailable_result(backend=backend, run_id=run_id)
    configured_limit = int(cfg.get("max_concurrency", 6) or 6)
    requested_limit = int(args.get("max_concurrency", configured_limit) or configured_limit)
    concurrency_limit = max(1, min(requested_limit, configured_limit))
    steps = _steps_from_args(args)
    command_artifacts: list[dict[str, Any]] = []
    if backend == "local_command":
        results = []
        failures: list[tuple[str, str]] = []
        for spec in specs:
            try:
                result, local_artifacts = await _run_local_command(
                    args=args,
                    ctx=ctx,
                    spec=spec,
                    tool_name="execution.batch_runner",
                )
            except RuntimeError as exc:
                failures.append((spec.experiment_id, str(exc)))
                continue
            results.append(result)
            command_artifacts.extend(local_artifacts)
            if result.status != "completed":
                failures.append((spec.experiment_id, "local_command failed"))
    else:
        results = [_run_mock_spec(spec, steps=steps) for spec in specs]
        failures = []
    artifacts = _persist_results(
        run_root=_run_root(ctx, run_id),
        project=ctx.project,
        results=results,
    ) + command_artifacts
    return ToolResult(
        ok=not failures,
        output={
            "backend": backend,
            "run_id": run_id,
            "max_concurrency": concurrency_limit,
            "results": [
                {
                    "experiment_id": result.experiment_id,
                    "status": result.status,
                    "metrics": result.metrics,
                    "fingerprint_hash": result.fingerprint_hash,
                }
                for result in results
            ],
            "failures": [
                {"experiment_id": experiment_id, "error": error}
                for experiment_id, error in failures
            ],
        },
        artifacts=artifacts,
        metrics={
            result.experiment_id: dict(result.metrics)
            for result in results
        },
    )
