"""Product policy for turning evaluation results into workflow actions.

The harness decides what the artifact quality signals are. The bridge decides
how the product should react to those signals: review priority, auto-approval
guardrails, and run completion quality gates.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, TypeGuard, overload

import yaml

from app.harness.evaluation.models import EvaluationDecision
from app.settings import repo_root

GateStatus = Literal["pass", "warn", "revise", "block"]
ReviewPriority = Literal["normal", "elevated", "high", "critical"]

_DECISION_RANK: dict[EvaluationDecision, int] = {
    "pass": 0,
    "warn": 1,
    "revise": 2,
    "block": 3,
    "fail": 4,
}
_GATE_RANK: dict[GateStatus, int] = {
    "pass": 0,
    "warn": 1,
    "revise": 2,
    "block": 3,
}
_DEFAULT_POLICY: dict[str, Any] = {
    "artifact": {
        "pass_min_score": 0.8,
        "revise_below_score": 0.65,
        "block_below_score": 0.4,
        "auto_approval": {
            "enabled": True,
            "enforce": False,
            "allow_gates": ["pass", "warn"],
            "min_score": 0.65,
        },
    },
    "review_priority": {
        "pass": "normal",
        "warn": "elevated",
        "revise": "high",
        "block": "critical",
    },
    "run": {
        "pass_min_score": 0.75,
        "warn_below_score": 0.75,
        "fail_below_score": 0.5,
        "max_allowed_decision": "warn",
        "fail_on_blocking": True,
        "completion_gate": {
            "mode": "audit_only",
        },
    },
}


@lru_cache(maxsize=1)
def load_evaluation_policy(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or repo_root() / "configs" / "evaluation.yaml"
    raw: dict[str, Any] = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            raw = loaded
    policy = _deep_merge(_DEFAULT_POLICY, _as_dict(raw.get("policy")))
    return policy


def reset_evaluation_policy_cache_for_tests() -> None:
    load_evaluation_policy.cache_clear()


def evaluate_artifact_summary(
    summary: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = policy or load_evaluation_policy()
    artifact_cfg = _as_dict(cfg.get("artifact"))
    reasons: list[str] = []

    decision = _as_decision(summary.get("decision")) or "pass"
    gate = _gate_from_decision(decision)
    if decision != "pass":
        reasons.append(f"evaluation decision is {decision}")

    score = _as_float(summary.get("overall_score"))
    block_below = _as_float(artifact_cfg.get("block_below_score")) or 0.4
    revise_below = _as_float(artifact_cfg.get("revise_below_score")) or 0.65
    pass_min = _as_float(artifact_cfg.get("pass_min_score")) or 0.8
    if score is not None:
        if score < block_below:
            gate = _max_gate(gate, "block")
            reasons.append(f"overall score {score:.3f} is below block threshold {block_below:.3f}")
        elif score < revise_below:
            gate = _max_gate(gate, "revise")
            reasons.append(f"overall score {score:.3f} is below revise threshold {revise_below:.3f}")
        elif score < pass_min:
            gate = _max_gate(gate, "warn")
            reasons.append(f"overall score {score:.3f} is below pass threshold {pass_min:.3f}")

    if summary.get("blocking") is True:
        gate = _max_gate(gate, "block")
        reasons.append("at least one evaluator marked the artifact as blocking")

    top_findings = _as_dict_list(summary.get("top_findings"))
    if any(str(item.get("severity")) == "blocker" for item in top_findings):
        gate = _max_gate(gate, "block")
        reasons.append("blocker finding present")
    elif any(str(item.get("severity")) == "high" for item in top_findings):
        gate = _max_gate(gate, "revise")
        reasons.append("high-severity finding present")

    auto_cfg = _as_dict(artifact_cfg.get("auto_approval"))
    auto_enabled = _as_bool(auto_cfg.get("enabled"), True)
    auto_enforced = _as_bool(auto_cfg.get("enforce"), False)
    allow_gates = set(_as_str_list(auto_cfg.get("allow_gates"), ("pass", "warn")))
    min_auto_score = _as_float(auto_cfg.get("min_score")) or revise_below
    auto_allowed = auto_enabled and gate in allow_gates
    if score is not None and score < min_auto_score:
        auto_allowed = False
    if summary.get("blocking") is True:
        auto_allowed = False

    if not reasons:
        reasons.append("evaluation policy passed")

    return {
        "schema": "evaluation_policy_decision.v1",
        "scope": "artifact",
        "gate": gate,
        "action": _action_for_gate(gate),
        "review_priority": _priority_for_gate(gate, cfg),
        "auto_approval_allowed": auto_allowed,
        "auto_approval_enforced": auto_enforced,
        "thresholds": {
            "pass_min_score": pass_min,
            "revise_below_score": revise_below,
            "block_below_score": block_below,
            "auto_approval_min_score": min_auto_score,
        },
        "reasons": reasons,
    }


def evaluate_scorecard(
    scorecard: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = policy or load_evaluation_policy()
    run_cfg = _as_dict(cfg.get("run"))
    reasons: list[str] = []

    decision = _as_decision(scorecard.get("overall_decision")) or "pass"
    gate = _gate_from_decision(decision)
    if decision != "pass":
        reasons.append(f"overall decision is {decision}")

    score = _as_float(scorecard.get("overall_score"))
    fail_below = _as_float(run_cfg.get("fail_below_score")) or 0.5
    warn_below = _as_float(run_cfg.get("warn_below_score")) or 0.75
    pass_min = _as_float(run_cfg.get("pass_min_score")) or warn_below
    if score is not None:
        if score < fail_below:
            gate = _max_gate(gate, "block")
            reasons.append(f"overall score {score:.3f} is below fail threshold {fail_below:.3f}")
        elif score < warn_below or score < pass_min:
            gate = _max_gate(gate, "warn")
            reasons.append(f"overall score {score:.3f} is below run pass threshold {pass_min:.3f}")

    max_allowed = _as_decision(run_cfg.get("max_allowed_decision")) or "warn"
    if _DECISION_RANK[decision] > _DECISION_RANK[max_allowed]:
        gate = _max_gate(gate, "block")
        reasons.append(f"overall decision exceeds max allowed decision {max_allowed}")

    if _as_bool(run_cfg.get("fail_on_blocking"), True):
        reports = _as_dict_list(scorecard.get("reports"))
        if any(item.get("blocking") is True for item in reports):
            gate = _max_gate(gate, "block")
            reasons.append("at least one report is blocking")

    top_findings = _as_dict_list(scorecard.get("top_findings"))
    if any(str(item.get("severity")) == "blocker" for item in top_findings):
        gate = _max_gate(gate, "block")
        reasons.append("blocker finding present")

    completion_cfg = _as_dict(run_cfg.get("completion_gate"))
    mode = str(completion_cfg.get("mode", "audit_only") or "audit_only")
    completion_allowed = mode != "enforce" or gate != "block"
    if not reasons:
        reasons.append("run quality gate passed")

    return {
        "schema": "evaluation_quality_gate.v1",
        "scope": "run",
        "gate": gate,
        "action": _completion_action(gate, completion_allowed),
        "review_priority": _priority_for_gate(gate, cfg),
        "completion_allowed": completion_allowed,
        "enforcement_mode": mode,
        "thresholds": {
            "pass_min_score": pass_min,
            "warn_below_score": warn_below,
            "fail_below_score": fail_below,
            "max_allowed_decision": max_allowed,
        },
        "reasons": reasons,
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(_as_dict(out[key]), value)
        else:
            out[key] = value
    return out


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_dict_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _as_str_list(value: object, default: tuple[str, ...]) -> list[str]:
    if not isinstance(value, list):
        return list(default)
    return [str(item) for item in value if isinstance(item, str)]


def _as_bool(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


@overload
def _as_float(value: object) -> float | None: ...


@overload
def _as_float(value: object, default: float) -> float: ...


@overload
def _as_float(value: object, default: None) -> float | None: ...


def _as_float(value: object, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _as_decision(value: object) -> EvaluationDecision | None:
    if _is_decision(value):
        return value
    return None


def _is_decision(value: object) -> TypeGuard[EvaluationDecision]:
    return value in _DECISION_RANK


def _gate_from_decision(decision: EvaluationDecision) -> GateStatus:
    if decision in {"block", "fail"}:
        return "block"
    if decision == "revise":
        return "revise"
    if decision == "warn":
        return "warn"
    return "pass"


def _max_gate(left: GateStatus, right: GateStatus) -> GateStatus:
    return left if _GATE_RANK[left] >= _GATE_RANK[right] else right


def _priority_for_gate(gate: GateStatus, cfg: dict[str, Any]) -> ReviewPriority:
    raw = _as_dict(cfg.get("review_priority")).get(gate)
    if raw == "critical":
        return "critical"
    if raw == "high":
        return "high"
    if raw == "elevated":
        return "elevated"
    return "normal"


def _action_for_gate(gate: GateStatus) -> str:
    if gate == "block":
        return "block_until_fixed"
    if gate == "revise":
        return "request_revision"
    if gate == "warn":
        return "review_before_approval"
    return "allow"


def _completion_action(gate: GateStatus, completion_allowed: bool) -> str:
    if not completion_allowed:
        return "block_completion"
    if gate == "block":
        return "complete_with_quality_exception"
    if gate in {"warn", "revise"}:
        return "complete_with_warnings"
    return "allow_completion"


__all__ = [
    "evaluate_artifact_summary",
    "evaluate_scorecard",
    "load_evaluation_policy",
    "reset_evaluation_policy_cache_for_tests",
]
