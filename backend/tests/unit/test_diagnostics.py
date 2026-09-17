from __future__ import annotations

import json
import math
from pathlib import Path
import pytest

from app.bridge.bridge_agent import BridgeAgent
from app.bridge.diagnostics import DiagnosticsConfig, MetricRule, analyze_run, load_diagnostics_config
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.schema.validator import validate_document
from app.storage.run_store import RunStore


def _write_inputs(run_root: Path, *, loss: float) -> None:
    (run_root / "execution" / "metrics.json").write_text(
        json.dumps([{"metrics": {"loss": loss, "RES": -42.0}}]),
        encoding="utf-8",
    )
    experiment_meta = {
        "schema": "experiment_plan.v1",
        "project": "pimc",
        "agent": "experiment",
        "variables": {"independent": ["k"], "dependent": ["loss"]},
        "metrics": {"primary": "loss"},
        "ablations": [{"name": "a", "config": {"k": 1}}],
        "estimated_runs": 1,
    }
    (run_root / "experiment" / "experiment_plan.approved.md").write_text(
        fm_dumps(experiment_meta, "plan"),
        encoding="utf-8",
    )
    code_meta = {
        "schema": "code_spec.v1",
        "project": "pimc",
        "agent": "coding",
        "target_lang": "python",
        "baseline_compat": {"preserved": True},
        "files_changed": [{"path": "libs/router_v2.py", "type": "modified", "risk": "low"}],
    }
    (run_root / "coding" / "code_spec.approved.md").write_text(
        fm_dumps(code_meta, "code"),
        encoding="utf-8",
    )


def test_diagnostics_config_loads_project_yaml() -> None:
    cfg = load_diagnostics_config("pimc")
    assert cfg.max_iterations == 2
    assert "coding" in cfg.allowed_targets
    assert any(rule.name == "loss" for rule in cfg.metric_rules)


def test_metrics_gap_flags_failed_threshold(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="t", project="pimc")
    _write_inputs(run.root, loss=0.5)
    cfg = DiagnosticsConfig(
        project="pimc",
        metric_rules=(MetricRule(name="loss", target=0.04, direction="lte", aggregation="max"),),
        analyzers={"metrics_gap": True, "config_sanity": True, "code_change_risk": True},
    )
    analysis = analyze_run(run, cfg)
    assert not analysis.passed
    assert analysis.failed_metrics[0].metric == "loss"
    assert analysis.suspected_causes[0].kind == "metrics_gap"


def test_bridge_agent_writes_schema_valid_diagnosis(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="t", project="pimc")
    _write_inputs(run.root, loss=0.5)
    decision = BridgeAgent().diagnose(run=run, attempt=1)
    assert decision.should_continue
    diagnosis = run.subdir("diagnosis") / "diagnosis.v1.md"
    result = validate_document(
        diagnosis.read_text(encoding="utf-8"),
        expected_schema="diagnosis.v1",
    )
    assert result.valid


def test_missing_criteria_are_not_a_pass_or_an_automatic_repair(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="no criterion", project="pimc")
    config = DiagnosticsConfig(project="pimc")
    analysis = analyze_run(run, config)
    assert not analysis.evaluated and not analysis.passed
    decision = BridgeAgent()._decide(config=config, analysis=analysis, attempt=1)
    assert not decision.passed and not decision.should_continue
    assert decision.recommended_target == "writing"


@pytest.mark.parametrize("value", [None, math.nan, math.inf, -math.inf])
def test_missing_or_nonfinite_measurement_is_never_zero_or_success(tmp_path: Path, value: float | None) -> None:
    run = RunStore(tmp_path).create(task="invalid measurement", project="pimc")
    rows = [{"metrics": {"score": 1.0}}, {"metrics": {} if value is None else {"score": value}}]
    (run.root / "execution/metrics.json").write_text(json.dumps(rows))
    config = DiagnosticsConfig(project="pimc", metric_rules=(MetricRule("score", 0.0, "gte"),))
    analysis = analyze_run(run, config)
    assert analysis.evaluated and not analysis.passed
    assert analysis.failed_metrics[0].observed is None
    assert analysis.failed_metrics[0].gap is None
