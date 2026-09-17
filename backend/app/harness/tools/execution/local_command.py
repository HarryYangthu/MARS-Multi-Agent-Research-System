"""Host-configured real command execution with request-bound measurement receipts.

Commands read MARS_JOB_REQUEST and write MARS_RESULT_PATH plus actual evidence
files. Stdout, a zero exit code, or old metrics alone cannot establish success.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import asyncio
from pathlib import Path
import time
import shutil
from typing import Any
import uuid

from jsonschema import validate

from app.harness.persistence import atomic_write_json, atomic_write_text
from app.harness.tools.config import load_execution_config, tool_config
from app.harness.tools.process_runtime import communicate_process, start_process


@dataclass(frozen=True)
class LocalCommandJob:
    run_id: str
    experiment_id: str
    project: str
    run_root: Path
    config: dict[str, Any] = field(default_factory=dict)
    seed: int | None = None
    steps: int = 1
    command_id: str = ""


@dataclass(frozen=True)
class LocalCommandResult:
    status: str
    duration_seconds: float
    metrics: dict[str, float]
    loss_curve: list[float]
    fingerprint_hash: str
    artifacts: list[dict[str, Any]]
    error: str = ""


_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["schema", "invocation_id", "run_id", "experiment_id", "status", "metrics", "evidence_paths"],
    "properties": {
        "schema": {"const": "local_command_result.v1"},
        "invocation_id": {"type": "string"}, "run_id": {"type": "string"},
        "experiment_id": {"type": "string"}, "status": {"const": "completed"},
        "metrics": {"type": "object", "minProperties": 1, "additionalProperties": {"type": "number"}},
        "evidence_paths": {"type": "array", "minItems": 1, "maxItems": 32,
                           "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
        "loss_curve": {"type": "array", "items": {"type": "number"}},
    },
}


def _file_hash(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return "sha256:" + checksum.hexdigest()


def _command(job: LocalCommandJob, tool_name: str) -> tuple[tuple[str, ...], tuple[str, ...], float]:
    cfg = load_execution_config()["execution"]
    rows = cfg.get("local_commands", [])
    selected = next((row for row in rows if isinstance(row, dict)
                     and (not job.command_id or row.get("id") == job.command_id)), None) if isinstance(rows, list) else None
    if selected is None:
        raise RuntimeError("no local_command is configured for execution tools")
    raw = selected.get("argv")
    if not isinstance(raw, list) or not raw or any(not isinstance(part, str) or not part for part in raw):
        raise ValueError("configured local_command argv must be a nonempty string list")
    argv = tuple(raw)
    allowlist = tool_config(tool_name).command_allowlist
    if not any(len(argv) >= len(prefix) and argv[:len(prefix)] == prefix for prefix in allowlist if prefix):
        raise ValueError("configured local_command is not allowlisted for " + tool_name)
    required = selected.get("required_metrics", [])
    if not isinstance(required, list) or any(not isinstance(name, str) or not name for name in required):
        raise ValueError("required_metrics must be a list of metric names")
    timeout = float(cfg.get("command_timeout_seconds", 60))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("local_command timeout must be positive and finite")
    return argv, tuple(required), timeout


def _read_result(path: Path, request: dict[str, Any], required_metrics: tuple[str, ...]) -> tuple[dict[str, float], list[float], list[dict[str, Any]]]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("command did not create a bounded result.json in its attempt directory")
    raw = json.loads(path.read_text(encoding="utf-8"))
    validate(instance=raw, schema=_RESULT_SCHEMA)
    for key in ("invocation_id", "run_id", "experiment_id"):
        if raw[key] != request[key]:
            raise ValueError("local_command result belongs to a different " + key)
    metrics = {name: float(value) for name, value in raw["metrics"].items()}
    if not all(math.isfinite(value) for value in metrics.values()):
        raise ValueError("command measurements must all be finite")
    if not set(required_metrics).issubset(metrics):
        raise ValueError("command omitted required measurements: " + ", ".join(sorted(set(required_metrics) - metrics.keys())))
    if not set(metrics) - {"returncode", "dry_run", "max_iters"}:
        raise ValueError("process metadata cannot substitute for research measurements")
    curve = [float(value) for value in raw.get("loss_curve", [])]
    if not all(math.isfinite(value) for value in curve):
        raise ValueError("loss_curve must contain finite measurements")
    artifacts: list[dict[str, Any]] = []
    root = path.parent.resolve()
    for name in raw["evidence_paths"]:
        relative = Path(name)
        source = root / relative
        if (relative.is_absolute() or ".." in relative.parts or source.is_symlink()
                or not source.resolve().is_relative_to(root) or not source.is_file()
                or source.name in {"result.json", "job.json", "execution_receipt.json", "stdout.log", "stderr.log"}):
            raise ValueError("evidence must identify an actual measurement file inside this attempt")
        artifacts.append({"kind": "measurement_evidence", "path": str(source), "sha256": _file_hash(source),
                          "bytes": source.stat().st_size})
    return metrics, curve, artifacts


async def run_local_command(job: LocalCommandJob, *, tool_name: str = "execution.simulation_runner") -> LocalCommandResult:
    argv, required, timeout = _command(job, tool_name)
    invocation_id = uuid.uuid4().hex
    safe_name = "".join(character if character.isalnum() or character in "-_" else "_" for character in job.experiment_id) or "experiment"
    output_dir = job.run_root.resolve() / "execution/local_commands" / safe_name / invocation_id
    output_dir.mkdir(parents=True, exist_ok=False)
    request_path = output_dir / "job.json"
    result_path = output_dir / "result.json"
    request: dict[str, Any] = {
        "schema": "local_command_request.v1", "invocation_id": invocation_id,
        "run_id": job.run_id, "experiment_id": job.experiment_id, "project": job.project,
        "config": job.config, "seed": job.seed, "steps": job.steps, "output_dir": str(output_dir),
    }
    atomic_write_json(request_path, request)
    request_sha256 = _file_hash(request_path)
    command_files = []
    for index, argument in enumerate(argv):
        resolved = shutil.which(argument) if index == 0 else argument
        if resolved is not None and Path(resolved).is_file():
            source = Path(resolved).resolve()
            command_files.append({"path": str(source), "sha256": _file_hash(source)})
    started = time.monotonic()
    stdout, stderr = b"", b""
    code: int | None = None
    metrics: dict[str, float] = {}
    curve: list[float] = []
    evidence: list[dict[str, Any]] = []
    error = ""
    status = "failed"
    policy = tool_config(tool_name)
    interruption: BaseException | None = None
    process: asyncio.subprocess.Process | None = None
    try:
        process = await start_process(argv, cwd=output_dir, backend=policy.process_backend,
            require_isolation=policy.require_isolation, env={
                "MARS_RUN_ID": job.run_id, "MARS_EXPERIMENT_ID": job.experiment_id,
                "MARS_PROJECT": job.project, "MARS_RUN_ROOT": str(job.run_root.resolve()),
                "MARS_JOB_REQUEST": str(request_path), "MARS_RESULT_PATH": str(result_path),
            })
        stdout, stderr = await communicate_process(process, timeout=timeout)
        code = process.returncode
        if code != 0:
            raise ValueError(f"local_command exited with status {code}")
        if _file_hash(request_path) != request_sha256:
            raise ValueError("command modified its host-owned job request")
        metrics, curve, evidence = _read_result(result_path, request, required)
        status = "completed"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    except BaseException as exc:
        status, error, interruption = "cancelled", type(exc).__name__, exc
    duration = time.monotonic() - started
    if process is not None:
        code = process.returncode
    atomic_write_text(output_dir / "stdout.log", stdout.decode("utf-8", errors="replace"))
    atomic_write_text(output_dir / "stderr.log", stderr.decode("utf-8", errors="replace"))
    receipt: dict[str, Any] = {
        "schema": "local_command_receipt.v1", "invocation_id": invocation_id,
        "run_id": job.run_id, "experiment_id": job.experiment_id,
        "status": status, "returncode": code, "duration_seconds": duration,
        "argv": list(argv), "command_files": command_files, "execution_backend": "local_process", "os_isolated": False,
        "request_sha256": request_sha256,
        "result_sha256": _file_hash(result_path) if result_path.is_file() and not result_path.is_symlink() else None,
        "evidence": evidence, "error": error,
    }
    receipt_path = output_dir / "execution_receipt.json"
    atomic_write_json(receipt_path, receipt)
    if interruption is not None:
        raise interruption
    return LocalCommandResult(status=status, duration_seconds=duration, metrics=metrics, loss_curve=curve,
        fingerprint_hash=_file_hash(receipt_path), error=error,
        artifacts=[{"kind": "local_command_receipt", "path": str(receipt_path)},
                   {"kind": "local_command_request", "path": str(request_path)}, *evidence])
