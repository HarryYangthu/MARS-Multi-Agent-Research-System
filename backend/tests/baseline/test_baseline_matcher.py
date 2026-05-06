"""Baseline matcher recall ≥ 80% / precision ≥ 90% target (ACCEPTANCE §6).

We seed the run_archive zone with synthetic baselines, then probe with both
"should match" and "should not match" plans.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.harness.kb.baseline_matcher import find_match
from app.harness.kb.embedder import embed
from app.harness.kb.stores import KBRecord, KBStores, reset_for_tests


def _seed(stores: KBStores, n: int = 10) -> None:
    zone = stores.zone("run_archive")
    for i in range(n):
        sig = (
            f"project=moe-pimc | variables={{'independent': ['expert_count_{i}']}} "
            f"| metrics={{'primary': 'RES_{i}'}} | ablations=[{i}]"
        )
        zone.add(
            KBRecord(
                id=f"baseline-{i}",
                zone="run_archive",
                text=sig,
                metadata={"run_id": f"hist_{i:02d}"},
                embedding=embed(sig),
            )
        )


def test_high_similarity_finds_match(tmp_path: Path) -> None:
    stores = reset_for_tests(base=tmp_path)
    _seed(stores, n=10)
    plan = {
        "project": "moe-pimc",
        "variables": {"independent": ["expert_count_3"]},
        "metrics": {"primary": "RES_3"},
        "ablations": [3],
    }
    match = find_match(plan=plan, threshold=0.85, stores=stores)
    assert match.matched_run_id == "hist_03"
    assert match.match_score >= 0.85


def test_low_similarity_no_match(tmp_path: Path) -> None:
    stores = reset_for_tests(base=tmp_path)
    _seed(stores, n=10)
    plan = {
        "project": "completely-unrelated",
        "variables": {"independent": ["totally_different_axis"]},
        "metrics": {"primary": "FOO"},
        "ablations": [99],
    }
    match = find_match(plan=plan, threshold=0.85, stores=stores)
    # We may still get the closest record by id, but score must be below threshold.
    assert match.match_score < 0.85
    assert match.record is None


def test_recall_and_precision_targets(tmp_path: Path) -> None:
    """Run a small test set and verify ≥80% recall / ≥90% precision."""
    stores = reset_for_tests(base=tmp_path)
    _seed(stores, n=10)

    positives = [
        {
            "project": "moe-pimc",
            "variables": {"independent": [f"expert_count_{i}"]},
            "metrics": {"primary": f"RES_{i}"},
            "ablations": [i],
        }
        for i in range(10)
    ]
    negatives = [
        {
            "project": "moe-pimc",
            "variables": {"independent": ["unique_axis"]},
            "metrics": {"primary": "AAA"},
            "ablations": [-1],
        }
        for _ in range(5)
    ]
    tp = sum(
        1 for p in positives if find_match(plan=p, threshold=0.85, stores=stores).match_score >= 0.85
    )
    fp = sum(
        1 for n in negatives if find_match(plan=n, threshold=0.85, stores=stores).match_score >= 0.85
    )
    recall = tp / len(positives)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    assert recall >= 0.8, f"recall {recall:.2f}"
    assert precision >= 0.9, f"precision {precision:.2f}"
