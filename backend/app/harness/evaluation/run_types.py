"""Protocol types for evaluating run-like objects without importing storage."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol


class EvaluationRun(Protocol):
    run_id: str
    project: str
    task: str
    entrypoint: str
    root: Path

