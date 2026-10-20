"""Pure text grading and optional read-only evaluation of an actual report.

Example report bodies are language fixtures, not claimed experiment results.
The real-file check runs only when its path is supplied by the test caller.
No run artifacts, services, model responses, or evaluation records are written.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from app.harness.evaluation.evaluators.artifact_quality import (
    ArtifactQualityEvaluator,
    _score_report,
    _score_proposal,
)
from app.harness.evaluation.models import EvaluationTarget
from app.harness.evaluation.rubrics import load_rubric
from app.harness.schema.frontmatter_parser import dumps


def test_focused_research_contract_has_equivalent_reference_presence_score() -> None:
    source = {'source_id': 'paper_fixture', 'decision': 'use', 'url': 'https://example.org/paper',
              'method_summary': 'Authored schema fixture, not actual research evidence',
              'method_spec_ref': '/method_spec/example'}
    linked = {'research_context': {'sources': [source], 'coverage': [{'source_ids': ['paper_fixture']}]}}
    assert _score_proposal(linked, '')['evidence'] == _score_proposal({'evidence_refs': ['fixture']}, '')['evidence']
    linked['research_context']['coverage'] = [{'source_ids': ['unrelated']}]
    assert _score_proposal(linked, '')['evidence'] == .25


def metadata() -> dict[str, Any]:
    return {
        "schema": "report.v1", "project": "language_contract", "agent": "writing",
        "deliverable_type": "research_report", "target_audience": "reviewer",
        "chain_refs": {"code": "coding/code_spec.approved.md", "runs": ["execution/metrics.json"]},
    }


@pytest.mark.parametrize("body", [
    "局限：只覆盖一个采集场景，不能据此判断其他场景。",
    "风险：采样偏差仍需后续检查。",
    "约束：预算有限，其他数据分布需要独立核验。",
    "失败的实验已保留，原因尚不确定。",
    "限制条件：测试只覆盖离线输入。",
    "适用范围为当前静态采集，不能外推到其他输入。",
    "数据切换情况下的鲁棒性未验证。",
    "部署资源消耗未评估。",
    "当前测试未覆盖连续流处理。",
    "本次结果不能外推到其他输入分布。",
    "本次结果无法外推到其他输入分布。",
    "本次结果不支持外推到部署场景。",
    "本次不宣称收敛或统计显著性。",
    "Limitation: this check covers one capture only.",
    "RISK: drift remains untested.",
    "Caveat: training ended at the budget boundary.",
    "Failure cases remain available for review.",
    "Constraint: memory usage has not been measured.",
])
def test_equivalent_chinese_and_english_limits_have_same_score(body: str) -> None:
    assert _score_report(metadata(), body, required_chain=("code", "runs"))["limitation_honesty"] == 1.0


@pytest.mark.parametrize("body", [
    "", "# 实验结果\n\n参数与 RES 记录见表格。",
    "模型已完成，结果适用于所有场景，所有指标均已达到目标。",
    "适用范围包括所有场景，实验已完成。",
    "# Results\n\nAll measurements are listed in the table.",
    "The experiment demonstrates universal superiority and convergence.",
])
def test_missing_limitations_keep_original_low_score(body: str) -> None:
    assert _score_report(metadata(), body, required_chain=("code", "runs"))["limitation_honesty"] == 0.45
    target = EvaluationTarget(project="language_contract", scope="artifact",
        target_ref="writing/text-contract.md", text=dumps(metadata(), body),
        expected_schema="report.v1", required_report_chain=("code", "runs"))
    report = ArtifactQualityEvaluator().evaluate(target)
    assert report.scores["limitation_honesty"] == 0.45
    assert any(finding.id == "quality_limitation_honesty" and finding.severity == "medium"
               for finding in report.findings)


def test_language_fix_preserves_report_thresholds_and_other_dimensions() -> None:
    rubric = load_rubric("report.v1")
    assert rubric.warn_threshold == 0.65
    assert rubric.pass_threshold == 0.8
    honest = _score_report(metadata(), "局限：未评估其他数据。", required_chain=("code", "runs"))
    missing = _score_report(metadata(), "RES 记录见表格。", required_chain=("code", "runs"))
    assert {key: value for key, value in honest.items() if key != "limitation_honesty"} == {
        key: value for key, value in missing.items() if key != "limitation_honesty"}


def test_actual_chinese_report_is_read_only_and_has_no_language_false_warning() -> None:
    supplied = os.environ.get("MARS_QUALITY_REAL_REPORT_PATH")
    if supplied is None:
        pytest.skip("Supply an actual report path to run this read-only environment check")
    path = Path(supplied)
    before = path.read_bytes()
    target = EvaluationTarget(project="actual_report_read_only", scope="artifact",
        target_ref=path.name, text=before.decode("utf-8"), expected_schema="report.v1",
        required_report_chain=("code", "runs"))
    report = ArtifactQualityEvaluator().evaluate(target)
    assert report.evaluator_version == 2
    assert report.scores["limitation_honesty"] == 1.0
    assert all(finding.id != "quality_limitation_honesty" for finding in report.findings)
    assert path.read_bytes() == before
