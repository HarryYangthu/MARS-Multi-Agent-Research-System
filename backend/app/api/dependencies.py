"""Shared dependency providers for the API layer."""
from __future__ import annotations

from app.bridge.orchestrator import Orchestrator
from app.harness.runtime.event_bus import InProcessEventBus
from app.storage.run_store import RunStore

_run_store: RunStore | None = None
_orchestrator: Orchestrator | None = None
_bus: InProcessEventBus | None = None


def get_run_store() -> RunStore:
    global _run_store
    if _run_store is None:
        _run_store = RunStore()
    return _run_store


def get_event_bus() -> InProcessEventBus:
    global _bus
    if _bus is None:
        _bus = InProcessEventBus()
    return _bus


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator(run_store=get_run_store(), bus=get_event_bus())
    return _orchestrator


async def shutdown_owned_runs() -> None:
    """Do not instantiate/recover an orchestrator just to shut the app down."""
    if _orchestrator is not None:
        from loguru import logger

        results = await _orchestrator.shutdown_owned_runs()
        for result in results:
            if result.get("status") != "stopped":
                logger.error("Owned run shutdown incomplete: run={} status={}", result["run_id"], result["status"])


def reset_for_tests() -> None:
    global _run_store, _orchestrator, _bus
    _run_store = None
    _orchestrator = None
    _bus = None
