"""Parse a mock simulation's curve into a JSON file under runs/<id>/execution/curves/."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence


def write_curve(
    *,
    run_root: Path,
    experiment_id: str,
    metric_name: str,
    values: Sequence[float],
) -> Path:
    target_dir = run_root / "execution" / "curves"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{experiment_id}_{metric_name}.json"
    target.write_text(
        json.dumps(
            {
                "experiment_id": experiment_id,
                "metric": metric_name,
                "values": list(values),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return target
