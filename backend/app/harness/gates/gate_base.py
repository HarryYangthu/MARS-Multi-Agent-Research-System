"""Common gate types."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GateOutcome:
    gate_id: str
    triggered: bool
    blocking: bool
    requires_human: bool
    reason: str = ""
