"""Tail subprocess stdout into the WS bus (placeholder for real runs).

V0 mock simulations don't spawn subprocesses, so this module is mostly a
hook for future use. We expose ``stream_lines`` so a Phase 6+ caller could
adapt a real subprocess.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from typing import Any


async def stream_lines(
    lines: Iterable[str],
    *,
    bus_publish: Any | None,
    channel: str,
) -> AsyncIterator[str]:
    for line in lines:
        if bus_publish is not None:
            await bus_publish(channel, {"event": "execution.log_line", "line": line})
        yield line
