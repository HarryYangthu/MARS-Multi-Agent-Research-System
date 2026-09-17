"""One bounded real worker attempt; crash retries keep previous evidence intact."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any

from app.execution.subprocess_env import sanitized_subprocess_environment
from app.harness.tools.process_runtime import start_process, wait_process
from app.harness.agent_loop.trace import atomic_json
from app.harness.research_trial import read_record


async def run_worker(job: dict[str, Any], output: Path, timeout: int) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    job = {**job, "output": str(output)}
    atomic_json(output / "job.json", job)
    command = [sys.executable, "-m", "app.execution.pimc_static_worker", str(output / "job.json")]
    with (output / "worker.log").open("w", encoding="utf-8") as log:
        process = await start_process(command, cwd=Path(job["repo"]), stdout=log, stderr=asyncio.subprocess.STDOUT,
            env=sanitized_subprocess_environment(overrides={"PYTHONDONTWRITEBYTECODE": "1", "CUDA_VISIBLE_DEVICES": "",
                "PIMC_CFG": str(Path(job["repo"]) / "configs/base.yaml"),
                "PYTHONPATH": str(Path(__file__).resolve().parents[2])}))
        atomic_json(output / "process.json", {"pid": process.pid, "command": command})
        try:
            code = await wait_process(process, timeout=timeout)
        except (TimeoutError, asyncio.CancelledError, KeyboardInterrupt) as exc:
            result: dict[str, Any] = {"status": "failed", "error": "Worker interrupted or exceeded wall-time budget"}
            atomic_json(output / "result.json", result)
            if not isinstance(exc, TimeoutError):
                raise
            code = process.returncode if process.returncode is not None else -1
    result_path = output / "result.json"
    result = read_record(result_path) if result_path.exists() else {"status": "failed", "error": "Worker exited without results"}
    if code != 0 and result.get("status") == "completed":
        result = {"status": "failed", "error": "Worker returned nonzero after writing results"}
    result["exit_code"] = code
    result["output"] = str(output)
    atomic_json(result_path, result)
    return result
