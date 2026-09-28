"""TensorBoard events from measured values only; no dependency on agent layers."""
from __future__ import annotations

import math
from pathlib import Path
import time
from typing import Any


class ScalarWriter:
    def __init__(self, directory: Path) -> None:
        from tensorboard.summary.writer.event_file_writer import EventFileWriter

        self._writer = EventFileWriter(str(directory), flush_secs=2)

    def scalar(self, tag: str, value: object, step: int) -> None:
        from tensorboard.compat.proto.event_pb2 import Event
        from tensorboard.compat.proto.summary_pb2 import Summary

        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return
        self._writer.add_event(Event(wall_time=time.time(), step=step,
                                    summary=Summary(value=[Summary.Value(tag=tag, simple_value=float(value))])))

    def flush(self) -> None:
        self._writer.flush()

    def close(self) -> None:
        self._writer.close()

    def __enter__(self) -> ScalarWriter:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class ExecutionScalars:
    """Mirror actual execution events, keeping experiments and retries separate."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.writers: dict[str, ScalarWriter] = {}
        self.steps: dict[str, int] = {}

    def record(self, payload: dict[str, Any]) -> None:
        event = payload.get("event")
        if event not in {"execution.curve_point", "execution.completed"}:
            return
        experiment = str(payload.get("experiment_id", "experiment"))
        # Preserve identity even when two experiment names normalize identically.
        import hashlib
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in experiment)[:80]
        name = safe + "-" + hashlib.sha256(experiment.encode()).hexdigest()[:8]
        metrics = payload.get("metrics", {})
        if event == "execution.completed" and not metrics:
            return
        writer = self.writers.get(name)
        if writer is None:
            writer = self.writers[name] = ScalarWriter(self.directory / name)
        if event == "execution.curve_point":
            step = int(payload.get("step", self.steps.get(name, 0)))
            self.steps[name] = step
            writer.scalar("train/" + str(payload.get("metric", "loss")), payload.get("value"), step)
            for key, value in (payload.get("paper_metrics") or {}).items():
                writer.scalar("metrics/" + key, value, step)
        elif isinstance(metrics, dict):
            for key, value in metrics.items():
                if key not in {"returncode", "dry_run", "max_iters", "error"}:
                    writer.scalar("result/" + key, value, self.steps.get(name, 0))
        writer.flush()

    def close(self) -> None:
        for writer in self.writers.values():
            writer.close()
