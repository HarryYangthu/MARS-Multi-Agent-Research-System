"""TensorBoard events from measured values only; no dependency on agent layers."""
from __future__ import annotations

import math
from pathlib import Path
import time
from typing import Any, TYPE_CHECKING
import uuid

if TYPE_CHECKING:
    from tensorboard.summary.writer.record_writer import RecordWriter


class ScalarWriter:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._writer: RecordWriter | None = None
        self._closed = False

    def _open(self) -> RecordWriter:
        from tensorboard.compat.proto.event_pb2 import Event
        from tensorboard.summary.writer.record_writer import RecordWriter

        if self._closed:
            raise ValueError('Cannot write to a closed TensorBoard writer')
        if self._writer is not None:
            return self._writer
        # Repeated visualization polls commonly contain no new observations.
        # Allocate a file only when there is an actual event to preserve.
        self.directory.mkdir(parents=True, exist_ok=True)
        timestamp = time.time_ns()
        # TensorBoard follows files lexicographically. The upstream writer's
        # unpadded counter orders .10 before .9 during rapid polling in one second.
        name = f'events.out.tfevents.{timestamp // 10**9:010d}.{timestamp:020d}.{uuid.uuid4().hex}'
        self._writer = RecordWriter((self.directory / name).open('xb'))
        self._writer.write(Event(wall_time=timestamp / 10**9, file_version='brain.Event:2').SerializeToString())
        return self._writer

    def _add_event(self, event: Any) -> None:
        self._open().write(event.SerializeToString())

    def scalar(self, tag: str, value: object, step: int) -> None:
        from tensorboard.compat.proto.event_pb2 import Event
        from tensorboard.compat.proto.summary_pb2 import Summary

        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return
        self._add_event(Event(wall_time=time.time(), step=step,
                                    summary=Summary(value=[Summary.Value(tag=tag, simple_value=float(value))])))

    def flush(self) -> None:
        if self._writer is not None:
            self._writer.flush()

    def image(self, tag: str, path: Path, step: int = 0) -> None:
        from PIL import Image
        from tensorboard.compat.proto.event_pb2 import Event
        from tensorboard.compat.proto.summary_pb2 import Summary
        import io

        with Image.open(path) as source:
            picture = source.convert("RGB")
            encoded = io.BytesIO()
            picture.save(encoded, format="PNG")
            value = Summary.Image(height=picture.height, width=picture.width,
                                  colorspace=3, encoded_image_string=encoded.getvalue())
        self._add_event(Event(wall_time=time.time(), step=step,
                                    summary=Summary(value=[Summary.Value(tag=tag, image=value)])))

    def text(self, tag: str, content: str) -> None:
        from tensorboard.compat.proto.event_pb2 import Event
        from tensorboard.plugins.text.summary import text_pb

        self._add_event(Event(wall_time=time.time(), step=0, summary=text_pb(tag, content)))

    def close(self) -> None:
        if not self._closed and self._writer is not None:
            self._writer.close()
        self._closed = True

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
