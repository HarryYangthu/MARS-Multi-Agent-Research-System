"""Killable lifecycle for actual generated-signal PIM CPU measurements.

The parent owns process termination. Generated signals are not production
capture evidence; metrics are computed by the real NumPy implementation.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys
from typing import Any

from app.execution.simulation_runner import JobSpec, _run_real_pim_in_worker
from app.harness.persistence import atomic_write_json


async def run(request_path: Path) -> int:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    spec_data = dict(request["spec"])
    spec_data["run_root"] = Path(spec_data["run_root"])
    spec = JobSpec(**spec_data)

    async def emit(channel: str, payload: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps({"channel": channel, "payload": payload}, ensure_ascii=False, allow_nan=False) + "\n")
        sys.stdout.flush()

    result = await _run_real_pim_in_worker(spec, bus_publish=emit, steps=int(request["steps"]),
                                          sleep_per_tick=float(request["sleep_per_tick"]))
    atomic_write_json(Path(request["result_path"]), asdict(result))
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(Path(sys.argv[1]))))
