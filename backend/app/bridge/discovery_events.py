"""Durable Discovery events mirrored to the runtime EventBus."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from app.harness.discovery.protocol import DiscoveryErrorCode, DiscoveryEventName
from app.harness.runtime.event_bus import EventBus
from app.storage.discovery_common import (
    DiscoveryPaths,
    atomic_write_json,
    discovery_lock,
    iter_json_files,
    model_payload,
    read_json,
)


class DiscoveryEventRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["discovery_event.v1"] = "discovery_event.v1"
    event_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    name: DiscoveryEventName
    run_id: str = Field(min_length=1)
    iteration: int | None = Field(default=None, ge=0)
    child_run_id: str = ""
    candidate_id: str = ""
    error_code: DiscoveryErrorCode | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class DiscoveryEventSink:
    """Write an event before best-effort publication to live subscribers."""

    def __init__(self, run_root: Path, *, run_id: str, bus: EventBus) -> None:
        self.paths = DiscoveryPaths(run_root=run_root, run_id=run_id)
        self.run_id = run_id
        self.bus = bus
        self.records_dir = self.paths.root / "events" / "records"
        self.channel = f"run.{run_id}.discovery"

    async def emit(
        self,
        name: DiscoveryEventName,
        *,
        iteration: int | None = None,
        child_run_id: str = "",
        candidate_id: str = "",
        error_code: DiscoveryErrorCode | None = None,
        payload: dict[str, Any] | None = None,
    ) -> DiscoveryEventRecord:
        with discovery_lock(self.paths):
            sequence = len(iter_json_files(self.records_dir)) + 1
            record = DiscoveryEventRecord(
                event_id=f"event-{sequence:020d}",
                sequence=sequence,
                name=name,
                run_id=self.run_id,
                iteration=iteration,
                child_run_id=child_run_id,
                candidate_id=candidate_id,
                error_code=error_code,
                payload=dict(payload or {}),
            )
            atomic_write_json(
                self.records_dir / f"{sequence:020d}.json",
                model_payload(record),
            )
        try:
            await self.bus.publish(self.channel, model_payload(record))
        except Exception as exc:  # pragma: no cover - durable event remains authoritative
            logger.warning(
                "discovery event publication failed: run={} event={} error={}",
                self.run_id,
                name.value,
                exc,
            )
        return record

    def replay(self) -> list[DiscoveryEventRecord]:
        return [
            DiscoveryEventRecord.model_validate(read_json(path))
            for path in iter_json_files(self.records_dir)
        ]
