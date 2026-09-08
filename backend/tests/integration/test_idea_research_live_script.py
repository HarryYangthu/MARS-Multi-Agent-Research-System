"""Real CLI preparation and pure report aggregation; no provider/tool substitutes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest
import yaml

from scripts.run_idea_research_live import aggregate_traces, evaluation_request, research_findings, source_limit

ROOT = Path(__file__).resolve().parents[3]


def scenario() -> dict[str, Any]:
    value: dict[str, Any] = yaml.safe_load((ROOT / "configs/evaluation/idea_research_delegated_real.yaml").read_text())
    return value


def test_public_request_has_no_preselected_papers_or_private_context(tmp_path: Path) -> None:
    request = evaluation_request(scenario(), tmp_path)
    assert "16×16" in request.user_request and "双线性" in request.user_request
    assert "https://" not in request.user_request
    assert request.upstream_artifacts == {}
    assert request.extra["context_sources"] == {"project_rules": False, "code_repositories": False}
    assert request.extra["idea_requirements"]["min_pdfs"] == 2


@pytest.mark.parametrize("changes", [{"scope": "project_proposal"}, {"data_scope": "private"},
                                      {"requirements": {"min_sources": 2, "min_pdfs": 2}}])
def test_invalid_scope_or_missing_dossier_rejected(tmp_path: Path, changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        evaluation_request({**scenario(), **changes}, tmp_path)


def test_empty_run_does_not_claim_research_or_complete_usage(tmp_path: Path) -> None:
    trace = aggregate_traces(tmp_path)
    assert trace["counts"] == {} and not trace["trace_consistent"] and not trace["usage_complete"]
    research = research_findings(tmp_path)
    assert all(value == 0 for value in research["counts"].values())
    assert research["reports"] == []


def test_preparation_uses_real_configs_without_calls(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(str(ROOT / p) for p in ("", "backend"))
    result = subprocess.run([sys.executable, "scripts/run_idea_research_live.py", "--prepare-only",
                             "--runs-root", str(tmp_path)], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert not list(tmp_path.rglob("events.jsonl")) and not list(tmp_path.rglob("*.pdf"))
    summary = json.loads(next(tmp_path.rglob("summary.json")).read_text())
    assert summary["status"] == "prepared" and not summary["material_ready"]
    request = json.loads(next(tmp_path.rglob("request.json")).read_text())
    assert request["credential_persisted"] is False and request["development_bypass_bridge"] is True
    assert request["resource_limits"]["source_max_mib"] == 32
    assert request["child_config"]["name"] == "idea_research"
    assert request["lead_config"]["tools"] == ["idea.research_delegate", "knowledge.kb_query"]
    assert request["child_config"]["thinking_enabled"] is False
    assert request["child_config"]["reasoning_effort"] is None


@pytest.mark.parametrize("value", [0, 65, True, 32.5, "32", None])
def test_invalid_source_limit_rejected(value: Any) -> None:
    with pytest.raises(ValueError, match="source_max_mib"):
        source_limit({"source_max_mib": value})


def test_source_limit_default_and_explicit_budget() -> None:
    assert source_limit({}) == 12
    assert source_limit(scenario()) == 32
