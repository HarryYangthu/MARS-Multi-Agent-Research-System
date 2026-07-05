"""Structured artifact quality rubrics."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.settings import repo_root


@dataclass(frozen=True)
class RubricDimension:
    id: str
    label: str
    weight: float
    signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArtifactRubric:
    schema_id: str
    pass_threshold: float
    warn_threshold: float
    dimensions: tuple[RubricDimension, ...]


class RubricNotFoundError(KeyError):
    """Raised when no structured rubric exists for a schema."""


def load_rubric(schema_id: str, *, base_dir: Path | None = None) -> ArtifactRubric:
    root = base_dir or (repo_root() / "configs" / "evaluation_rubrics")
    path = root / f"{schema_id}.yaml"
    if not path.exists():
        raise RubricNotFoundError(schema_id)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"rubric file must contain an object: {path}")

    dims_raw = raw.get("dimensions", [])
    dimensions: list[RubricDimension] = []
    if isinstance(dims_raw, list):
        for item in dims_raw:
            if not isinstance(item, dict):
                continue
            signals_raw = item.get("signals", [])
            signals = (
                tuple(str(signal) for signal in signals_raw)
                if isinstance(signals_raw, list)
                else ()
            )
            dimensions.append(
                RubricDimension(
                    id=str(item.get("id", "")),
                    label=str(item.get("label", item.get("id", ""))),
                    weight=float(item.get("weight", 1.0) or 1.0),
                    signals=signals,
                )
            )

    return ArtifactRubric(
        schema_id=str(raw.get("schema", schema_id)),
        pass_threshold=float(raw.get("pass_threshold", 0.8) or 0.8),
        warn_threshold=float(raw.get("warn_threshold", 0.65) or 0.65),
        dimensions=tuple(dim for dim in dimensions if dim.id),
    )


def weighted_score(scores: dict[str, float], rubric: ArtifactRubric) -> float:
    total_weight = 0.0
    weighted = 0.0
    for dimension in rubric.dimensions:
        score = scores.get(dimension.id)
        if score is None:
            continue
        total_weight += dimension.weight
        weighted += score * dimension.weight
    if total_weight == 0:
        return 0.0
    return max(0.0, min(1.0, weighted / total_weight))


def raw_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def raw_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
