"""Run the actual public numeric adapter from an absent trusted runs directory."""
from __future__ import annotations

import json
from pathlib import Path

from app.storage.run_store import RunStore
from scripts.release.run_synthetic_smoke import run


async def test_release_smoke_initializes_clean_runs_and_archives_actual_responses(tmp_path: Path) -> None:
    runs_root = tmp_path / "new-runs"
    assert not runs_root.exists()
    result = await run(runs_root=runs_root)
    assert result["status"] == "passed"
    assert result["candidate_count"] == result["unique_candidate_ids"] == 20
    assert result["unique_envelopes"] == 20
    handle = RunStore(runs_root).get(str(result["run_id"]))
    assert handle is not None
    execution = handle.root / "execution"
    receipts = sorted(execution.glob("candidate-*.json"))
    assert len(receipts) == 20
    for path in receipts:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        assert receipt["status"] == "ok"
        assert receipt["raw_metrics"]["schema_id"] == "metric_envelope.v1"
    assert json.loads((execution / "summary.json").read_text(encoding="utf-8")) == result
