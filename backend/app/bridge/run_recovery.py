"""Run recovery: rehydrate persisted runs into the orchestrator at startup.

Called once from ``app.main`` after the orchestrator + agents are wired. Runs
that were mid-flight when the process died are surfaced as WAITING_HUMAN so a
human can resume or retry them — we never silently auto-restart compute.
"""
from __future__ import annotations

from loguru import logger

from app.bridge.orchestrator import Orchestrator


def recover_runs(orchestrator: Orchestrator) -> list[str]:
    interrupted = orchestrator.recover_all()
    logger.info("run recovery complete ({} interrupted)", len(interrupted))
    return interrupted
