"""Baseline fingerprint hashing.

Each completed run gets a SHA256 fingerprint computed over a normalized
form of (project, code_spec hash, plan hash, metric tuple). The fingerprint
goes into ``run_archive`` so future plans can match against historical runs.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def compute(*, plan: dict[str, Any], code_spec: dict[str, Any], metrics: dict[str, Any]) -> str:
    payload = {
        "plan": _canonical(plan),
        "code_spec": _canonical(code_spec),
        "metric_keys": sorted(list((metrics or {}).keys())),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def _canonical(d: dict[str, Any]) -> dict[str, Any]:
    """Strip volatile fields before hashing (timestamps, run_id, fingerprint)."""
    drop = {"created", "timestamp", "run_id", "fingerprint_hash", "duration_seconds"}
    out: dict[str, Any] = {}
    for k, v in d.items():
        if k in drop:
            continue
        out[k] = v
    return out
