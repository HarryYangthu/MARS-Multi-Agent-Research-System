"""Commander-facing wrappers; registration remains owned by the base bridge."""
from __future__ import annotations

from typing import Any

from app.bridge.discovery_service import DiscoveryService
from app.bridge.discovery_types import DiscoveryRunSpec


class DiscoveryCommanderTools:
    def __init__(self, service: DiscoveryService) -> None:
        self.service = service

    async def create(
        self,
        spec: DiscoveryRunSpec,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return (
            await self.service.create(spec, idempotency_key=idempotency_key)
        ).model_dump(mode="json")

    async def start(self, run_id: str, *, wait: bool = False) -> dict[str, Any]:
        return (await self.service.start(run_id, wait=wait)).model_dump(mode="json")

    def status(self, run_id: str) -> dict[str, Any]:
        return self.service.status(run_id).model_dump(mode="json")

    async def pause(self, run_id: str, *, reason: str = "commander_requested") -> dict[str, Any]:
        return (await self.service.pause(run_id, reason=reason)).model_dump(mode="json")

    async def resume(self, run_id: str, *, wait: bool = False) -> dict[str, Any]:
        return (await self.service.resume(run_id, wait=wait)).model_dump(mode="json")

    async def stop(self, run_id: str, *, reason: str = "commander_requested") -> dict[str, Any]:
        return (await self.service.stop(run_id, reason=reason)).model_dump(mode="json")

    def replay(self, run_id: str) -> dict[str, Any]:
        return self.service.replay(run_id).model_dump(mode="json")
