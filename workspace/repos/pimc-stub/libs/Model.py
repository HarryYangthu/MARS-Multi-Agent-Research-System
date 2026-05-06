"""Stub model for the pimc-stub workspace.

Mirrors enough of the real research code that MARS Coding/Execution Agents
can reason about it without touching production. Real research code is
NEVER committed to MARS; this stub is for Dev E2E only.
"""
from __future__ import annotations

import numpy as np


class Paper_Total_0327:
    """Production baseline (frozen by AGENTS.md rule #1)."""

    def __init__(self, expert_count: int = 8) -> None:
        self.expert_count = expert_count
        # weights: (expert_count, dim, dim)
        self.weights = np.eye(4, 4)

    def forward(self, x: np.ndarray, stream_label: int) -> np.ndarray:
        # x: (B, T, D)
        # AGENTS.md rule #2: forward(x, stream_label) signature is frozen.
        return x  # passthrough placeholder


class Paper_Router_v2:
    """Hard top-2 router prototype — MARS-modifiable surface."""

    def __init__(self, expert_count: int = 8, top_k: int = 2) -> None:
        self.expert_count = expert_count
        self.top_k = top_k

    def forward(self, x: np.ndarray, stream_label: int) -> np.ndarray:
        # x: (B, T, D)
        return x  # placeholder
