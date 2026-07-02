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
        "project": "pimc",
        "agent": "idea",
        "research_question": "How to simplify routing while preserving RES?",
        "hypothesis": "Hard top-2 routing degrades RES <1.5 dB.",
        "novelty": "Stream-aware hard routing not in survey.",
    }


def experiment_plan_v1_base() -> dict[str, Any]:
    return {
        "schema": "experiment_plan.v1",
        "project": "pimc",
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
        "project": "pimc",
        "agent": "coding",
        "target_lang": "python",
        "baseline_compat": {"preserved": True, "rationale": "signature unchanged"},
        "files_changed": [{"path": "libs/Model.py", "type": "modified", "risk": "medium"}],
    }


def run_log_v1_base() -> dict[str, Any]:
    return {
        "schema": "run_log.v1",
        "project": "pimc",
        "agent": "execution",
        "run_id": "2026-05-04T2310_demo",
        "status": "completed",
        "metrics": {"RES": -42.0},
        "fingerprint_hash": "sha256:abc123",
    }


def diagnosis_v1_base() -> dict[str, Any]:
    return {
        "schema": "diagnosis.v1",
        "project": "pimc",
        "agent": "bridge",
        "run_id": "2026-05-04T2310_demo",
        "attempt": 1,
        "passed": False,
        "failed_metrics": [
            {
                "metric": "loss",
                "observed": 0.12,
                "target": 0.04,
                "direction": "lte",
                "gap": 0.08,
                "aggregation": "max",
            }
        ],
        "suspected_causes": [
            {
                "kind": "metrics_gap",
                "summary": "Loss exceeded threshold.",
                "severity": "high",
                "evidence": ["execution/metrics.json"],
            }
        ],
        "recommended_target": "coding",
        "recommended_action": "Generate patch diff.",
        "evidence_refs": ["execution/metrics.json"],
        "budget_status": "within_budget",
    }


def evaluation_report_v1_base() -> dict[str, Any]:
    return {
        "schema": "evaluation_report.v1",
        "project": "pimc",
        "scope": "artifact",
        "target_ref": "idea/idea_proposal.v1.md",
        "target_schema": "proposal.v1",
        "evaluator": "contract.schema_validity",
        "evaluator_version": 1,
        "decision": "pass",
        "overall_score": 1.0,
        "blocking": False,
        "scores": {"schema_validity": 1.0},
        "findings": [],
        "recommended_actions": [],
        "created": "2026-06-17T00:00:00Z",
    }


def feedback_packet_v1_base() -> dict[str, Any]:
    return {
        "schema": "feedback_packet.v1",
        "project": "pimc",
        "agent": "commander",
        "run_id": "2026-05-04T2310_demo",
        "target_agent": "coding",
        "attempt": 2,
        "source_attempt": 1,
        "confidence": 0.82,
        "why_this_agent": "Metrics gap is most consistent with code change risk.",
        "evidence_refs": ["execution/metrics.json", "coding/code_spec.approved.md"],
        "failed_metrics": [{"metric": "loss", "observed": 0.12, "target": 0.04}],
        "do_next": ["Generate a focused patch and rerun execution."],
        "avoid_repeating": ["Do not alter baseline interfaces."],
        "context_refs": ["diagnosis/diagnosis.v1.md"],
        "memory_candidates": [{"zone": "run_archive", "id": "baseline_a"}],
    }


def report_v1_base() -> dict[str, Any]:
    return {
        "schema": "report.v1",
        "project": "pimc",
        "agent": "writing",
        "deliverable_type": "research_report",
        "target_audience": "phd_advisor",
        "chain_refs": {"proposal": "idea_proposal.approved.md"},
    }


def report_bundle_v1_base() -> dict[str, Any]:
    return {
        "schema": "report_bundle.v1",
        "project": "pimc",
        "agent": "writing",
        "run_id": "2026-06-20T1212_demo",
        "created_at": "2026-06-20T12:12:00Z",
        "data_pack": "writing/report_data_pack.v1.json",
        "deliverables": [
            {
                "kind": "excel",
                "path": "writing/deliverables/results_workbook.xlsx",
                "status": "completed",
                "bytes": 2048,
            },
            {
                "kind": "word",
                "path": "writing/deliverables/research_report.docx",
                "status": "completed",
                "bytes": 4096,
            },
            {
                "kind": "powerpoint",
                "path": "writing/deliverables/research_deck.pptx",
                "status": "completed",
                "bytes": 4096,
            },
        ],
        "source_refs": ["execution/metrics.json", "writing/research_report.approved.md"],
        "qa_status": {
            "status": "passed",
            "checks": [
                {
                    "name": "excel.zip_structure",
                    "status": "passed",
                    "detail": "results_workbook.xlsx",
                }
            ],
        },
        "generation_errors": [],
    }


BASE_BUILDERS: dict[str, Callable[[], dict[str, Any]]] = {
    "proposal.v1": proposal_v1_base,
    "experiment_plan.v1": experiment_plan_v1_base,
    "code_spec.v1": code_spec_v1_base,
    "run_log.v1": run_log_v1_base,
    "diagnosis.v1": diagnosis_v1_base,
    "evaluation_report.v1": evaluation_report_v1_base,
    "feedback_packet.v1": feedback_packet_v1_base,
    "report.v1": report_v1_base,
    "report_bundle.v1": report_bundle_v1_base,
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
    v7 = proposal_v1_base()
    v7["testable_predictions"] = [
        {
            "prediction": "RES degradation stays below 1.5 dB.",
            "metric": "RES",
            "expected_direction": "lte_degradation",
            "success_threshold": "<=1.5 dB",
        }
    ]
    out.append(v7)
    v8 = proposal_v1_base()
    v8["experiment_hint"] = {
        "variables": ["router_type", "expert_count"],
        "metrics": ["RES", "PIM", "APE"],
        "minimal_ablations": [
            {"name": "soft", "config": {"router_type": "soft"}},
            {"name": "hard", "config": {"router_type": "hard-top2"}},
        ],
    }
    v8["evidence_refs"] = [
        {"ref": "self_context_1", "kind": "self_context", "summary": "rubric"}
    ]
    out.append(v8)
    v9 = proposal_v1_base()
    v9["risk_register"] = [
        {"risk": "routing instability", "severity": "medium", "mitigation": "ablate"}
    ]
    v9["downstream_requirements"] = ["Build router_type ablation."]
    v9["debate_summary"] = {
        "rounds": 1,
        "consensus": "Proceed.",
        "disagreements": ["Need more evidence."],
        "risks": ["Baseline compatibility."],
        "evidence_gaps": ["No network research."],
    }
    out.append(v9)
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


def _report_bundle_valid_variants() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [report_bundle_v1_base()]
    v2 = report_bundle_v1_base()
    v2["deliverables"].append(
        {
            "kind": "markdown",
            "path": "writing/research_report.approved.md",
            "status": "completed",
            "bytes": 1200,
        }
    )
    out.append(v2)
    v3 = report_bundle_v1_base()
    v3["qa_status"] = {
        "status": "degraded",
        "checks": [{"name": "input.degraded", "status": "degraded", "detail": "no plots"}],
    }
    out.append(v3)
    v4 = report_bundle_v1_base()
    v4["deliverables"][0]["status"] = "failed"
    v4["deliverables"][0]["error"] = "xlsx writer failed"
    v4["qa_status"]["status"] = "failed"
    v4["qa_status"]["checks"][0]["status"] = "failed"
    v4["generation_errors"] = ["excel: xlsx writer failed"]
    out.append(v4)
    v5 = report_bundle_v1_base()
    v5["deliverables"][1]["status"] = "skipped"
    v5["deliverables"][1]["error"] = "docx disabled"
    out.append(v5)
    v6 = report_bundle_v1_base()
    v6["source_refs"] = []
    out.append(v6)
    for i in range(6):
        v = report_bundle_v1_base()
        v["run_id"] = f"run_{i}"
        v["created_at"] = f"2026-06-20T12:1{i}:00Z"
        v["data_pack"] = f"writing/report_data_pack.v{i + 1}.json"
        out.append(v)
    return out


def _diagnosis_valid_variants() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [diagnosis_v1_base()]
    v2 = diagnosis_v1_base()
    v2["passed"] = True
    v2["failed_metrics"] = []
    v2["suspected_causes"] = []
    v2["recommended_target"] = "writing"
    v2["budget_status"] = "not_applicable"
    out.append(v2)
    v3 = diagnosis_v1_base()
    v3["recommended_target"] = "experiment"
    out.append(v3)
    v4 = diagnosis_v1_base()
    v4["budget_status"] = "exhausted"
    out.append(v4)
    v5 = diagnosis_v1_base()
    v5["failed_metrics"][0]["direction"] = "gte"
    out.append(v5)
    v6 = diagnosis_v1_base()
    v6["suspected_causes"] = [
        {"kind": "config_sanity", "summary": "Bad ablation plan."}
    ]
    out.append(v6)
    for i in range(6):
        v = diagnosis_v1_base()
        v["attempt"] = i + 1
        v["run_id"] = f"run_{i}"
        out.append(v)
    return out


def _evaluation_report_valid_variants() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [evaluation_report_v1_base()]
    v2 = evaluation_report_v1_base()
    v2["scope"] = "run"
    v2["target_ref"] = "runs/example"
    v2["target_schema"] = None
    out.append(v2)
    v3 = evaluation_report_v1_base()
    v3["scope"] = "benchmark"
    v3["target_ref"] = "evals/suites/smoke_mock.yaml"
    v3["decision"] = "warn"
    v3["overall_score"] = 0.72
    v3["findings"] = [
        {
            "id": "W001",
            "severity": "low",
            "category": "latency",
            "message": "Run exceeded the soft latency target.",
            "evidence_refs": ["events/agent_events.jsonl"],
        }
    ]
    out.append(v3)
    v4 = evaluation_report_v1_base()
    v4["decision"] = "revise"
    v4["overall_score"] = 0.55
    v4["findings"] = [
        {
            "id": "R001",
            "severity": "high",
            "category": "evidence",
            "message": "Novelty claim lacks a traceable evidence reference.",
            "evidence_refs": ["idea/idea_proposal.v1.md#frontmatter.novelty"],
        }
    ]
    v4["recommended_actions"] = ["Add one cited baseline comparison."]
    out.append(v4)
    v5 = evaluation_report_v1_base()
    v5["decision"] = "block"
    v5["blocking"] = True
    v5["overall_score"] = None
    v5["scores"] = {"schema_validity": 0.0}
    v5["findings"] = [
        {
            "id": "B001",
            "severity": "blocker",
            "category": "schema",
            "message": "Required schema field is missing.",
            "evidence_refs": ["idea/idea_proposal.v1.md"],
        }
    ]
    out.append(v5)
    v6 = evaluation_report_v1_base()
    v6["decision"] = "fail"
    v6["overall_score"] = 0.0
    out.append(v6)
    v7 = evaluation_report_v1_base()
    v7.pop("target_schema")
    out.append(v7)
    v8 = evaluation_report_v1_base()
    v8["scores"] = {"testability": 0.8, "evidence": 0.7, "novelty": 0.5}
    out.append(v8)
    v9 = evaluation_report_v1_base()
    v9["scope"] = "model_backend"
    v9["target_ref"] = "models/deepseek-chat"
    out.append(v9)
    for i in range(4):
        v = evaluation_report_v1_base()
        v["evaluator_version"] = i + 1
        v["evaluator"] = f"artifact_quality.rubric_{i}"
        out.append(v)
    return out


def _feedback_packet_valid_variants() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [feedback_packet_v1_base()]
    v2 = feedback_packet_v1_base()
    v2["target_agent"] = "experiment"
    v2["why_this_agent"] = "Experiment coverage is too narrow."
    out.append(v2)
    v3 = feedback_packet_v1_base()
    v3["confidence"] = 0.0
    out.append(v3)
    v4 = feedback_packet_v1_base()
    v4["confidence"] = 1.0
    out.append(v4)
    v5 = feedback_packet_v1_base()
    v5["failed_metrics"] = []
    out.append(v5)
    v6 = feedback_packet_v1_base()
    v6["avoid_repeating"] = []
    v6["context_refs"] = []
    v6["memory_candidates"] = []
    out.append(v6)
    for i in range(6):
        v = feedback_packet_v1_base()
        v["attempt"] = i + 2
        v["source_attempt"] = i + 1
        v["run_id"] = f"run_{i}"
        out.append(v)
    return out


VALID_VARIANTS: dict[str, list[dict[str, Any]]] = {
    "proposal.v1": _proposal_valid_variants(),
    "experiment_plan.v1": _experiment_plan_valid_variants(),
    "code_spec.v1": _code_spec_valid_variants(),
    "run_log.v1": _run_log_valid_variants(),
    "diagnosis.v1": _diagnosis_valid_variants(),
    "evaluation_report.v1": _evaluation_report_valid_variants(),
    "feedback_packet.v1": _feedback_packet_valid_variants(),
    "report.v1": _report_valid_variants(),
    "report_bundle.v1": _report_bundle_valid_variants(),
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
    bad_target = _set(base, "recommended_target", "router")
    bad_budget = _set(base, "budget_status", "open")
    bad_agent = _set(base, "agent", "coding")
    bad_metric = _set(base, "failed_metrics", [{"metric": "loss"}])
    bad_cause = _set(base, "suspected_causes", [{"kind": "metrics_gap"}])
    return [
        _drop(base, "schema"),
        _drop(base, "run_id"),
        _drop(base, "attempt"),
        _set(base, "attempt", 0),
        bad_target,
        bad_budget,
        bad_agent,
        bad_metric,
        bad_cause,
    ]


def _evaluation_report_invalid_variants() -> list[dict[str, Any]]:
    base = evaluation_report_v1_base()
    bad_scope = _set(base, "scope", "case")
    bad_decision = _set(base, "decision", "maybe")
    bad_score = _set(base, "overall_score", 1.2)
    bad_version = _set(base, "evaluator_version", 0)
    bad_block = _set(base, "decision", "block")
    bad_block["blocking"] = False
    bad_finding = _set(
        base,
        "findings",
        [
            {
                "id": "F001",
                "severity": "medium",
                "category": "evidence",
                "message": "Missing evidence refs.",
            }
        ],
    )
    bad_severity = _set(
        base,
        "findings",
        [
            {
                "id": "F001",
                "severity": "critical",
                "category": "evidence",
                "message": "Unsupported severity.",
                "evidence_refs": ["x"],
            }
        ],
    )
    return [
        _drop(base, "schema"),
        _drop(base, "project"),
        _drop(base, "target_ref"),
        bad_scope,
        bad_decision,
        bad_score,
        bad_version,
        bad_block,
        bad_finding,
        bad_severity,
    ]


def _feedback_packet_invalid_variants() -> list[dict[str, Any]]:
    base = feedback_packet_v1_base()
    return [
        _drop(base, "schema"),
        _drop(base, "run_id"),
        _drop(base, "target_agent"),
        _set(base, "agent", "bridge"),
        _set(base, "target_agent", "writing"),
        _set(base, "attempt", 1),
        _set(base, "source_attempt", 0),
        _set(base, "confidence", 1.5),
        _set(base, "do_next", []),
    ]


def _report_bundle_invalid_variants() -> list[dict[str, Any]]:
    base = report_bundle_v1_base()
    bad_agent = _set(base, "agent", "bridge")
    bad_data_pack = _set(base, "data_pack", "")
    bad_deliverable_kind = report_bundle_v1_base()
    bad_deliverable_kind["deliverables"][0]["kind"] = "pdf"
    bad_deliverable_status = report_bundle_v1_base()
    bad_deliverable_status["deliverables"][0]["status"] = "ok"
    bad_deliverable_missing = report_bundle_v1_base()
    bad_deliverable_missing["deliverables"] = [{"path": "x.xlsx", "status": "completed"}]
    bad_qa_status = report_bundle_v1_base()
    bad_qa_status["qa_status"]["status"] = "ok"
    bad_qa_check = report_bundle_v1_base()
    bad_qa_check["qa_status"]["checks"] = [{"name": "excel.zip_structure"}]
    return [
        _drop(base, "schema"),
        _drop(base, "run_id"),
        _drop(base, "deliverables"),
        bad_agent,
        bad_data_pack,
        bad_deliverable_kind,
        bad_deliverable_status,
        bad_deliverable_missing,
        bad_qa_status,
        bad_qa_check,
    ]


INVALID_VARIANTS: dict[str, list[dict[str, Any]]] = {
    "proposal.v1": _proposal_invalid_variants(),
    "experiment_plan.v1": _experiment_plan_invalid_variants(),
    "code_spec.v1": _code_spec_invalid_variants(),
    "run_log.v1": _run_log_invalid_variants(),
    "diagnosis.v1": _diagnosis_invalid_variants(),
    "evaluation_report.v1": _evaluation_report_invalid_variants(),
    "feedback_packet.v1": _feedback_packet_invalid_variants(),
    "report.v1": _report_invalid_variants(),
    "report_bundle.v1": _report_bundle_invalid_variants(),
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
