"""Schema compliance test suite.

Per ACCEPTANCE §4: each schema gets ≥20 samples (mix valid + invalid).
We use fixture functions that construct minimally-valid metadata, then
parameterize valid variants and invalid mutations.

Indicator target: validator passes ≥95% of samples crafted as valid, and
flags 100% of samples crafted as invalid (false-positive ≤5%).
"""
from __future__ import annotations

from typing import Any, Callable

import pytest

from app.harness.schema.validator import (
    SUPPORTED_SCHEMAS,
    ValidationResult,
    validate_metadata,
)

# --------------------------------------------------------------------- builders


def proposal_v1_base() -> dict[str, Any]:
    return {
        "schema": "proposal.v1",
        "project": "moe-pimc",
        "agent": "idea",
        "research_question": "How to simplify routing while preserving RES?",
        "hypothesis": "Hard top-2 routing degrades RES <1.5 dB.",
        "novelty": "Stream-aware hard routing not in survey.",
    }


def experiment_plan_v1_base() -> dict[str, Any]:
    return {
        "schema": "experiment_plan.v1",
        "project": "moe-pimc",
        "agent": "experiment",
        "variables": {
            "independent": ["expert_count"],
            "controlled": ["batch_size"],
            "dependent": ["RES", "PIM"],
        },
        "metrics": {"primary": "RES", "secondary": ["PIM", "APE"]},
        "ablations": [{"name": "k4", "config": {"expert_count": 4}}],
        "estimated_runs": 4,
    }


def code_spec_v1_base() -> dict[str, Any]:
    return {
        "schema": "code_spec.v1",
        "project": "moe-pimc",
        "agent": "coding",
        "target_lang": "python",
        "baseline_compat": {"preserved": True, "rationale": "signature unchanged"},
        "files_changed": [{"path": "libs/Model.py", "type": "modified", "risk": "medium"}],
    }


def run_log_v1_base() -> dict[str, Any]:
    return {
        "schema": "run_log.v1",
        "project": "moe-pimc",
        "agent": "execution",
        "run_id": "2026-05-04T2310_demo",
        "status": "completed",
        "metrics": {"RES": -42.0},
        "fingerprint_hash": "sha256:abc123",
    }


def report_v1_base() -> dict[str, Any]:
    return {
        "schema": "report.v1",
        "project": "moe-pimc",
        "agent": "writing",
        "deliverable_type": "research_report",
        "target_audience": "phd_advisor",
        "chain_refs": {"proposal": "idea_proposal.approved.md"},
    }


def diagnosis_v1_base() -> dict[str, Any]:
    return {
        "schema": "diagnosis.v1",
        "project": "moe-pimc",
        "agent": "diagnosis",
        "failed_node": "execution",
        "root_cause": "Execution crashed: bad batch config produced NaN loss.",
        "recommended_action": "revise_coding",
        "target_node": "coding",
    }


BASE_BUILDERS: dict[str, Callable[[], dict[str, Any]]] = {
    "proposal.v1": proposal_v1_base,
    "experiment_plan.v1": experiment_plan_v1_base,
    "code_spec.v1": code_spec_v1_base,
    "run_log.v1": run_log_v1_base,
    "report.v1": report_v1_base,
    "diagnosis.v1": diagnosis_v1_base,
}


# ----------------------------------------------------------- valid permutations


def _proposal_valid_variants() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [proposal_v1_base()]
    v2 = proposal_v1_base()
    v2["constraints"] = ["X", "Y"]
    out.append(v2)
    v3 = proposal_v1_base()
    v3["theoretical_basis"] = "Sparse activation theory."
    out.append(v3)
    v4 = proposal_v1_base()
    v4["debate_summary"] = {"rounds": 2, "consensus": "agreed."}
    out.append(v4)
    v5 = proposal_v1_base()
    v5["related_literature"] = [{"title": "Paper A"}, {"title": "Paper B", "url": "https://x"}]
    out.append(v5)
    v6 = proposal_v1_base()
    v6["created"] = "2026-05-04T10:00Z"
    out.append(v6)
    # variations of the long-text fields
    for i in range(6):
        v = proposal_v1_base()
        v["research_question"] = f"Long enough question version {i} of stream switching?"
        out.append(v)
    return out


def _experiment_plan_valid_variants() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [experiment_plan_v1_base()]
    v2 = experiment_plan_v1_base()
    v2["estimated_gpu_hours"] = 12.5
    out.append(v2)
    v3 = experiment_plan_v1_base()
    v3["baseline_ref"] = {
        "matched_run_id": "2026-05-04T2310_baseline",
        "match_score": 0.91,
        "reuse_decision": "modify",
    }
    out.append(v3)
    v4 = experiment_plan_v1_base()
    v4["ablations"] = [
        {"name": "k4", "config": {"expert_count": 4}},
        {"name": "k8", "config": {"expert_count": 8}},
        {"name": "k16", "config": {"expert_count": 16}},
    ]
    out.append(v4)
    v5 = experiment_plan_v1_base()
    v5["upstream_artifact"] = "idea_proposal.approved.md"
    out.append(v5)
    v6 = experiment_plan_v1_base()
    v6["metrics"] = {"primary": "RES"}  # secondary optional
    out.append(v6)
    for i in range(6):
        v = experiment_plan_v1_base()
        v["estimated_runs"] = i + 2
        out.append(v)
    return out


def _code_spec_valid_variants() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [code_spec_v1_base()]
    v2 = code_spec_v1_base()
    v2["target_lang"] = "c"
    out.append(v2)
    v3 = code_spec_v1_base()
    v3["new_dependencies"] = ["torch", "numpy"]
    out.append(v3)
    v4 = code_spec_v1_base()
    v4["test_coverage"] = {"unit_tests_added": 5, "baseline_smoke_test": "pass"}
    out.append(v4)
    v5 = code_spec_v1_base()
    v5["files_changed"] = [
        {"path": "libs/Model.py", "type": "modified", "risk": "low"},
        {"path": "libs/Router.py", "type": "added", "risk": "low"},
    ]
    out.append(v5)
    v6 = code_spec_v1_base()
    v6["baseline_compat"] = {"preserved": False, "rationale": "Intentional break for rfc-2"}
    out.append(v6)
    for i in range(6):
        v = code_spec_v1_base()
        v["files_changed"] = [
            {"path": f"libs/Mod{i}.py", "type": "modified", "risk": "low"}
        ]
        out.append(v)
    return out


def _run_log_valid_variants() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [run_log_v1_base()]
    v2 = run_log_v1_base()
    v2["status"] = "failed"
    out.append(v2)
    v3 = run_log_v1_base()
    v3["batch_size"] = 1024
    v3["duration_seconds"] = 600.5
    out.append(v3)
    v4 = run_log_v1_base()
    v4["gpu_used"] = ["L40S:1", "L40S:2"]
    v4["is_mock"] = True
    out.append(v4)
    v5 = run_log_v1_base()
    v5["metrics"] = {"RES": -40.1, "PIM": -18.0, "APE": 22.4, "loss": 0.013}
    out.append(v5)
    v6 = run_log_v1_base()
    v6["fingerprint_hash"] = "abcd1234deadbeef"  # without sha256: prefix is OK
    out.append(v6)
    for i in range(6):
        v = run_log_v1_base()
        v["run_id"] = f"2026-05-04T231{i}_demo"
        out.append(v)
    return out


def _report_valid_variants() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [report_v1_base()]
    v2 = report_v1_base()
    v2["deliverable_type"] = "paper_fragment"
    v2["target_audience"] = "NeurIPS reviewer"
    out.append(v2)
    v3 = report_v1_base()
    v3["deliverable_type"] = "ppt_outline"
    out.append(v3)
    v4 = report_v1_base()
    v4["chain_refs"]["runs"] = ["execution/run_log_run1.md", "execution/run_log_run2.md"]
    out.append(v4)
    v5 = report_v1_base()
    v5["debate_summary"] = {"rounds": 1, "reviewer_critiques": ["More ablation."]}
    out.append(v5)
    v6 = report_v1_base()
    v6["deliverable_type"] = "tech_summary"
    out.append(v6)
    for i in range(6):
        v = report_v1_base()
        v["target_audience"] = f"audience_{i}"
        out.append(v)
    return out


def _diagnosis_valid_variants() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [diagnosis_v1_base()]
    # each recommended_action enum value, with a matching plausible target
    for action, target in [
        ("retry", "execution"),
        ("revise_coding", "coding"),
        ("revise_experiment", "experiment"),
        ("manual", "execution"),
    ]:
        v = diagnosis_v1_base()
        v["recommended_action"] = action
        v["target_node"] = target
        out.append(v)
    # optional fields
    v = diagnosis_v1_base()
    v["created"] = "2026-06-09T10:00Z"
    out.append(v)
    v = diagnosis_v1_base()
    v["attempt"] = 2
    out.append(v)
    v = diagnosis_v1_base()
    v["confidence"] = 0.9
    out.append(v)
    v = diagnosis_v1_base()
    v["evidence"] = ["traceback line 1", "traceback line 2"]
    out.append(v)
    # long-text root_cause variations
    for i in range(4):
        v = diagnosis_v1_base()
        v["root_cause"] = f"Variant {i}: upstream code_spec produced an invalid patch."
        out.append(v)
    return out


VALID_VARIANTS: dict[str, list[dict[str, Any]]] = {
    "proposal.v1": _proposal_valid_variants(),
    "experiment_plan.v1": _experiment_plan_valid_variants(),
    "code_spec.v1": _code_spec_valid_variants(),
    "run_log.v1": _run_log_valid_variants(),
    "report.v1": _report_valid_variants(),
    "diagnosis.v1": _diagnosis_valid_variants(),
}


# ---------------------------------------------------------- invalid permutations


def _drop(d: dict[str, Any], key: str) -> dict[str, Any]:
    out = dict(d)
    out.pop(key, None)
    return out


def _set(d: dict[str, Any], key: str, value: Any) -> dict[str, Any]:
    out = dict(d)
    out[key] = value
    return out


def _proposal_invalid_variants() -> list[dict[str, Any]]:
    base = proposal_v1_base()
    return [
        _drop(base, "schema"),
        _set(base, "schema", "proposal.v2"),
        _drop(base, "research_question"),
        _drop(base, "hypothesis"),
        _drop(base, "novelty"),
        _set(base, "agent", "writing"),  # wrong agent
        _set(base, "research_question", "short"),  # below minLength
        _set(base, "related_literature", [{"url": "no title"}]),
    ]


def _experiment_plan_invalid_variants() -> list[dict[str, Any]]:
    base = experiment_plan_v1_base()
    bad_metrics = dict(base)
    bad_metrics["metrics"] = {"secondary": ["PIM"]}  # missing primary
    bad_vars = dict(base)
    bad_vars["variables"] = {"independent": [], "dependent": ["RES"]}  # empty independent
    bad_ablations = dict(base)
    bad_ablations["ablations"] = []  # empty
    bad_decision = dict(base)
    bad_decision["baseline_ref"] = {"reuse_decision": "ignore"}  # not in enum
    return [
        _drop(base, "schema"),
        _drop(base, "variables"),
        _drop(base, "metrics"),
        _drop(base, "estimated_runs"),
        _set(base, "estimated_runs", 0),
        bad_metrics,
        bad_vars,
        bad_decision,
    ]


def _code_spec_invalid_variants() -> list[dict[str, Any]]:
    base = code_spec_v1_base()
    bad_lang = _set(base, "target_lang", "haskell")
    bad_files = _set(base, "files_changed", [{"path": "x", "type": "weird"}])
    bad_compat = _set(base, "baseline_compat", {"rationale": "missing preserved"})
    bad_risk = _set(base, "files_changed", [{"path": "x", "type": "modified", "risk": "extreme"}])
    bad_smoke = _set(base, "test_coverage", {"baseline_smoke_test": "okay"})
    return [
        _drop(base, "target_lang"),
        bad_lang,
        bad_files,
        bad_compat,
        bad_risk,
        bad_smoke,
        _drop(base, "baseline_compat"),
        _set(base, "agent", "execution"),
    ]


def _run_log_invalid_variants() -> list[dict[str, Any]]:
    base = run_log_v1_base()
    bad_status = _set(base, "status", "running")  # not in enum
    bad_metrics = _set(base, "metrics", {})  # empty
    bad_fp = _set(base, "fingerprint_hash", "not-a-hash!!")  # bad pattern
    bad_batch = _set(base, "batch_size", 0)
    return [
        _drop(base, "run_id"),
        bad_status,
        bad_metrics,
        bad_fp,
        bad_batch,
        _drop(base, "fingerprint_hash"),
        _drop(base, "metrics"),
        _set(base, "agent", "idea"),
    ]


def _report_invalid_variants() -> list[dict[str, Any]]:
    base = report_v1_base()
    bad_deliv = _set(base, "deliverable_type", "blog_post")
    bad_audience = _set(base, "target_audience", "")
    return [
        _drop(base, "deliverable_type"),
        bad_deliv,
        _drop(base, "chain_refs"),
        _drop(base, "target_audience"),
        bad_audience,
        _set(base, "agent", "idea"),
        _set(base, "deliverable_type", 42),  # type
        _set(base, "schema", "report.v0"),
    ]


def _diagnosis_invalid_variants() -> list[dict[str, Any]]:
    base = diagnosis_v1_base()
    return [
        _drop(base, "schema"),
        _set(base, "schema", "diagnosis.v2"),
        _drop(base, "failed_node"),
        _drop(base, "root_cause"),
        _set(base, "root_cause", "short"),  # < 8 chars
        _set(base, "recommended_action", "explode"),  # not in enum
        _drop(base, "target_node"),
        _set(base, "agent", "execution"),  # must be const "diagnosis"
    ]


INVALID_VARIANTS: dict[str, list[dict[str, Any]]] = {
    "proposal.v1": _proposal_invalid_variants(),
    "experiment_plan.v1": _experiment_plan_invalid_variants(),
    "code_spec.v1": _code_spec_invalid_variants(),
    "run_log.v1": _run_log_invalid_variants(),
    "report.v1": _report_invalid_variants(),
    "diagnosis.v1": _diagnosis_invalid_variants(),
}


# ------------------------------------------------------------------- structure


def test_supported_schemas_count() -> None:
    assert set(SUPPORTED_SCHEMAS) == set(BASE_BUILDERS.keys())


@pytest.mark.parametrize("schema_id", SUPPORTED_SCHEMAS)
def test_each_schema_has_at_least_20_samples(schema_id: str) -> None:
    valid = VALID_VARIANTS[schema_id]
    invalid = INVALID_VARIANTS[schema_id]
    assert len(valid) + len(invalid) >= 20, schema_id
    assert len(valid) >= 12, schema_id
    assert len(invalid) >= 8, schema_id


@pytest.mark.parametrize(
    "schema_id,sample",
    [
        (sid, sample)
        for sid in SUPPORTED_SCHEMAS
        for sample in VALID_VARIANTS[sid]
    ],
)
def test_valid_samples_pass(schema_id: str, sample: dict[str, Any]) -> None:
    result: ValidationResult = validate_metadata(sample, expected_schema=schema_id)
    assert result.valid, f"expected valid; errors={result.errors}"


@pytest.mark.parametrize(
    "schema_id,sample",
    [
        (sid, sample)
        for sid in SUPPORTED_SCHEMAS
        for sample in INVALID_VARIANTS[sid]
    ],
)
def test_invalid_samples_caught(schema_id: str, sample: dict[str, Any]) -> None:
    expected = schema_id if sample.get("schema") == schema_id else None
    result = validate_metadata(sample, expected_schema=expected)
    assert not result.valid, f"expected invalid for {schema_id}: {sample}"


def test_compliance_rate_above_95_percent() -> None:
    """Aggregate: of all valid samples, ≥95% pass."""
    total = 0
    passes = 0
    for sid, samples in VALID_VARIANTS.items():
        for s in samples:
            total += 1
            r = validate_metadata(s, expected_schema=sid)
            if r.valid:
                passes += 1
    rate = passes / total if total else 0.0
    assert rate >= 0.95, f"compliance rate {rate:.2%} below 95% target"
