"""Host-owned execution identity; tool/model arguments cannot grant this scope."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Literal

from app.harness.runtime.research_budget_ledger import ResearchBudgetLedger
from app.harness.runtime.state_journal import StateJournal

Stage = Literal["idea", "experiment", "coding", "execution", "writing"]


@dataclass(frozen=True)
class ResearchExecutionScope:
    ledger: ResearchBudgetLedger
    stage: Stage
    invocation_id: str

    def __post_init__(self) -> None:
        if self.stage not in {"idea", "experiment", "coding", "execution", "writing"}:
            raise ValueError("invalid trusted execution stage")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", self.invocation_id) is None:
            raise ValueError("invalid execution invocation identity")

    @property
    def root(self) -> Path:
        return self.ledger.journal.path.parent.resolve()


_SCOPE: ContextVar[ResearchExecutionScope | None] = ContextVar("mars_research_execution_scope", default=None)


def bound_research_execution() -> ResearchExecutionScope | None:
    """Inspect only the host ContextVar, without accepting caller identity input."""
    return _SCOPE.get()


@contextmanager
def bind_research_execution(scope: ResearchExecutionScope) -> Iterator[None]:
    previous = _SCOPE.get()
    if previous is not None and (previous.root != scope.root
            or previous.ledger.task_sha256 != scope.ledger.task_sha256):
        raise ValueError("nested execution cannot replace its run or frozen contract")
    scope.ledger.snapshot()  # Require the existing, verified extension; never initialize here.
    token = _SCOPE.set(scope)
    try:
        yield
    finally:
        _SCOPE.reset(token)


def current_research_execution(run_root: Path) -> ResearchExecutionScope | None:
    root = run_root.resolve()
    scope = _SCOPE.get()
    if scope is not None:
        if scope.root != root:
            raise ValueError("execution scope belongs to another run")
        return scope
    path = root / "input/research_task.v1.json"
    if path.exists() or path.is_symlink():
        raise ValueError("frozen research task requires a host-bound execution scope")
    journal = StateJournal.from_authority(root, run_id=root.name)
    if journal is not None:
        request = journal.read().get("request")
        extra = request.get("extra") if isinstance(request, dict) else None
        if isinstance(extra, dict) and extra.get("research_task_sha256") is not None:
            raise ValueError("frozen research task requires a host-bound execution scope")
    return None
