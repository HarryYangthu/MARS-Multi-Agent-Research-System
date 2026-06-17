"""Helpers for V1 dynamic attempt node keys.

Bridge owns product topology, so attempt naming also lives here rather than
inside the generic DAG runtime.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


_ATTEMPT_RE = re.compile(r"^(?P<stage>[a-z_]+)_attempt_(?P<attempt>[1-9][0-9]*)$")


@dataclass(frozen=True)
class NodeIdentity:
    key: str
    stage: str
    attempt: int


def parse_node_key(key: str) -> NodeIdentity:
    match = _ATTEMPT_RE.match(key)
    if match is None:
        return NodeIdentity(key=key, stage=key, attempt=1)
    return NodeIdentity(
        key=key,
        stage=match.group("stage"),
        attempt=int(match.group("attempt")),
    )


def attempt_key(stage: str, attempt: int) -> str:
    if attempt <= 1:
        return stage
    return f"{stage}_attempt_{attempt}"
