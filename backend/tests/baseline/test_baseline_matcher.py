"""Match plans against receipts from actual CPU polynomial fits.

This small retrieval contract is not a scientific baseline or a benchmark of
research quality. Unproven profile metadata must not outrank verified records.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.execution.pim_cancellation import run_pim_cancellation
from app.harness.kb.baseline_matcher import _plan_signature, find_match
from app.harness.kb.embedder import embed
from app.harness.kb.profiles import write_baseline_current
from app.harness.kb.provenance import record_artifact
from app.harness.kb.stores import KBRecord, KBStores, reset_for_tests


def _seed(stores: KBStores, n: int = 10) -> list[dict[str, object]]:
    plans: list[dict[str, object]] = []
    for i in range(n):
        config = {"order": 1 + 2 * (i % 3), "memory": 1 + i // 3}
        _, measured = run_pim_cancellation(n_points=128, steps=3, seed=i, ablation_config=config)
        plan: dict[str, object] = {"project": "cpu_polynomial", "variables": config,
                                  "metrics": {"primary": "NMSE_dB"}, "ablations": [config]}
        signature = _plan_signature(plan)
        source = stores.base / f"actual_fit_{i}.md"
        source.write_text(signature + "\n" + json.dumps({"measured_nmse_db": measured.res_db,
                          "n_basis": measured.n_basis, "seed": i}))
        stores.zone("run_archive").add(KBRecord(
            id=f"cpu-fit-{i}", zone="run_archive", text=signature, embedding=embed(signature),
            metadata=record_artifact(path=source, run_id=f"cpu_fit_{i}", project="cpu_polynomial")))
        plans.append(plan)
    return plans


def test_high_similarity_finds_actual_run(tmp_path: Path) -> None:
    stores = reset_for_tests(base=tmp_path)
    plans = _seed(stores)
    match = find_match(plan=plans[3], threshold=0.85, stores=stores)
    assert match.matched_run_id == "cpu_fit_3"
    assert match.match_score >= 0.85


def test_low_similarity_no_match(tmp_path: Path) -> None:
    stores = reset_for_tests(base=tmp_path)
    _seed(stores, n=1)
    match = find_match(plan={"project": "unrelated", "variables": {"axis": "fruit"},
                            "metrics": {"primary": "banana"}, "ablations": [99]}, stores=stores)
    assert match.record is None


def test_unverified_profile_cannot_override_actual_archive(tmp_path: Path) -> None:
    stores = reset_for_tests(base=tmp_path)
    plans = _seed(stores, n=1)
    write_baseline_current("cpu_polynomial", {"run_id": "unproven-profile",
                           "signature": _plan_signature(plans[0])}, base=tmp_path)
    assert find_match(plan=plans[0], stores=stores).matched_run_id == "cpu_fit_0"


def test_exact_plan_recall_on_actual_fit_archive(tmp_path: Path) -> None:
    stores = reset_for_tests(base=tmp_path)
    plans = _seed(stores)
    assert all(find_match(plan=plan, stores=stores).matched_run_id == f"cpu_fit_{i}"
               for i, plan in enumerate(plans))
