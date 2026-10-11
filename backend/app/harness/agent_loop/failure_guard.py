"""Stop unchanged failure loops without imposing a quota on useful work."""
from __future__ import annotations

from typing import Any

from app.harness.agent_loop.trace import digest


def repeated_failure(state: dict[str, Any], kind: str, evidence: Any, *, limit: int) -> bool:
    guards = state.setdefault('failure_streaks', {})
    previous = guards.get(kind, {})
    signature = digest(evidence)
    count = previous.get('count', 0) + 1 if previous.get('signature') == signature else 1
    guards[kind] = {'signature': signature, 'count': count}
    return bool(count > limit)


def clear_failure(state: dict[str, Any], kind: str) -> None:
    state.get('failure_streaks', {}).pop(kind, None)
