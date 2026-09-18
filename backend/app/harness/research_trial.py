"""Auditable trial identities and validation-only selection, independent of agents."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResearchBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    reduction: float = Field(default=0.2, gt=0, lt=1)
    max_degradation_db: float = Field(default=0.0, ge=0)
    rounds: int = Field(default=3, ge=1, le=20)
    max_steps: int = Field(default=50, ge=1, le=100000)
    timeout_seconds: int = Field(default=1800, ge=1, le=86400)


def candidate_factory_config(protocol: dict[str, Any]) -> dict[str, Any]:
    """One exact factory interface shared by planning and the actual worker."""
    return {"channels": 16, "baseline": deepcopy(protocol["baseline"]), "context": protocol["context"]}


def file_sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_record(path: Path) -> dict[str, Any]:
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"Expected object: {path}")
    return result


def compare(baseline: dict[str, Any], candidate: dict[str, Any],
            budget: ResearchBudget, *, split: str = "validation") -> dict[str, Any]:
    """Never substitute a missing measurement, and count complex scalars twice upstream."""
    base_count, count = int(baseline["real_parameters"]), int(candidate["real_parameters"])
    base_score, score = float(baseline[split]["RES_db"]), float(candidate[split]["RES_db"])
    if min(base_count, count) < 1 or not all(math.isfinite(x) for x in (base_score, score)):
        raise ValueError("Non-finite metric or invalid parameter count")
    reduction = 1 - count / base_count
    delta = score - base_score
    return {"parameter_reduction": reduction, "degradation_db": delta,
            "parameter_pass": reduction + 1e-12 >= budget.reduction,
            "performance_pass": delta <= budget.max_degradation_db,
            "passed": reduction + 1e-12 >= budget.reduction and delta <= budget.max_degradation_db}


def select_candidate(baseline: dict[str, Any], candidates: list[dict[str, Any]],
                     budget: ResearchBudget) -> dict[str, Any] | None:
    eligible = [c for c in candidates if c.get("status") == "completed"
                and compare(baseline, c, budget)["parameter_pass"]]
    return min(eligible, key=lambda c: float(c["validation"]["RES_db"])) if eligible else None
