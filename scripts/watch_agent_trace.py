"""Show factual progress from an agent's on-disk trace without another API call."""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from loguru import logger


def progress_snapshot(run_root: Path) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for path in sorted((run_root / "agent_traces").glob("*/*/facts.json")):
        try:
            facts = json.loads(path.read_text())
            snapshots.append({
                "agent": path.parent.parent.name, "invocation": path.parent.name,
                "status": facts["status"], "waiting_for": facts.get("pending"),
                "counts": facts["counts"], "usage_complete": facts["usage_complete"],
                "seconds_since_trace_update": round(max(0.0, time.time() - path.stat().st_mtime), 1),
            })
        except (OSError, ValueError, KeyError, TypeError) as exc:
            snapshots.append({"trace": str(path.parent), "status": "unreadable",
                              "error_type": type(exc).__name__})
    return snapshots


async def monitor(run_root: Path, *, interval: float = 30.0) -> None:
    while True:
        snapshots = progress_snapshot(run_root)
        logger.info("AGENT_PROGRESS {}", json.dumps(snapshots or [{"status": "starting"}], ensure_ascii=False))
        await asyncio.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not args.run_root.is_dir():
        parser.error("run_root must be an existing run directory")
    if args.once:
        logger.info("AGENT_PROGRESS {}", json.dumps(progress_snapshot(args.run_root), ensure_ascii=False))
    else:
        try:
            asyncio.run(monitor(args.run_root))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
