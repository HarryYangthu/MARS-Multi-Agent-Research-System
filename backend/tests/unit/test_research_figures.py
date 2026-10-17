"""Pure parsing checks and optional integration against real saved measurements."""
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from app.execution.research_figures import db_mean, training_rows, validation_data, verified_sources
from app.settings import repo_root


def test_power_average_is_linear_not_db() -> None:
    assert db_mean(np.array([0.0, 10.0])) == pytest.approx(10 * np.log10(5.5))
    with pytest.raises(ValueError):
        db_mean(np.array([float("nan")]))


def test_training_blanks_quoted_fields_and_partial_write() -> None:
    rows = training_rows(b'updates,mean_loss_sum,train_PIM_native,train_RES_native,centers\r\n'
                         b'0,,21.3,21.4,"[-27.5, 0]"\r\n275,.31,21.3,9.8,"[-27.5, 0]"\r\n550,.2,21')
    assert len(rows) == 2
    assert np.isnan(rows[0]["mean_loss_sum"])
    assert rows[1]["updates"] == 275
    with pytest.raises(ValueError, match="columns"):
        training_rows(b"step,loss\n1,0.1\n")


def test_receipt_required(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        verified_sources(tmp_path, {"max_source_bytes": 10000})


def test_real_saved_validation_matches_and_rejects_changed_metrics() -> None:
    # Never generate an execution receipt or synthetic training success for this test.
    pytest.importorskip("scipy")
    root = repo_root()
    exports = list((root / "runs").glob("*/execution/local_commands/*/*/channel_performance.csv"))
    if not exports:
        pytest.skip("No real local-command validation archive on this host")
    policy = yaml.safe_load((root / "configs/research_figures.yaml").read_text(encoding="utf-8"))
    sources = verified_sources(exports[0].parent, policy)
    parsed = validation_data(sources, policy)
    evaluation = json.loads(sources["evaluation.json"])
    assert db_mean(parsed["res"]) == pytest.approx(evaluation["RES"], abs=1e-6)
    changed = dict(sources)
    evaluation["RES"] += 1
    changed["evaluation.json"] = json.dumps(evaluation).encode()
    with pytest.raises(ValueError, match="Aggregate metric mismatch"):
        validation_data(changed, policy)
    changed = dict(sources)
    changed["channel_performance.csv"] = b"channel,PIM,RES,improvement_db\n0,1,2,-1\n"
    with pytest.raises(ValueError, match="channel metrics"):
        validation_data(changed, policy)
