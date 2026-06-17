"""Context budget policy for feedback-loop context injection.

This module stays in harness because it is agent-agnostic: it knows how to
bound text and describe pruning decisions, but not which Agent should run next.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from app.settings import repo_root


@dataclass(frozen=True)
class ContextBudgetPolicy:
    max_tokens: int = 1500
    chars_per_token: int = 4
    recent_packet_count: int = 2
    ledger_summary_ref: str = "diagnosis/attempt_ledger_summary.md"
    drop_full_diagnosis: bool = True
    drop_full_logs: bool = True
    drop_full_curves: bool = True

    @property
    def max_chars(self) -> int:
        return max(1, self.max_tokens * self.chars_per_token)

    def with_max_tokens(self, max_tokens: int | None) -> "ContextBudgetPolicy":
        if max_tokens is None:
            return self
        return replace(self, max_tokens=max(1, int(max_tokens)))

    def to_manifest(self) -> dict[str, Any]:
        data = asdict(self)
        data["max_chars"] = self.max_chars
        return data


@dataclass(frozen=True)
class BoundedContext:
    text: str
    original_chars: int
    compressed_chars: int
    clipped: bool
    strategy: str
    policy: ContextBudgetPolicy
    prune_reasons: tuple[str, ...]

    def to_metadata(self) -> dict[str, Any]:
        return {
            "original_chars": self.original_chars,
            "compressed_chars": self.compressed_chars,
            "max_tokens": self.policy.max_tokens,
            "max_chars": self.policy.max_chars,
            "clipped": self.clipped,
            "strategy": self.strategy,
            "prune_reasons": list(self.prune_reasons),
            "budget_policy": self.policy.to_manifest(),
        }


def load_context_budget_policy(config_path: Path | None = None) -> ContextBudgetPolicy:
    path = config_path or repo_root() / "configs" / "context.yaml"
    if not path.exists():
        return ContextBudgetPolicy()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return ContextBudgetPolicy()
    feedback_raw = raw.get("feedback", {})
    feedback = feedback_raw if isinstance(feedback_raw, dict) else {}
    return ContextBudgetPolicy(
        max_tokens=_positive_int(feedback.get("max_tokens"), 1500),
        chars_per_token=_positive_int(feedback.get("chars_per_token"), 4),
        recent_packet_count=_positive_int(feedback.get("recent_packet_count"), 2),
        ledger_summary_ref=str(
            feedback.get("ledger_summary_ref", "diagnosis/attempt_ledger_summary.md")
        ),
        drop_full_diagnosis=bool(feedback.get("drop_full_diagnosis", True)),
        drop_full_logs=bool(feedback.get("drop_full_logs", True)),
        drop_full_curves=bool(feedback.get("drop_full_curves", True)),
    )


def bound_feedback_text(
    text: str,
    *,
    policy: ContextBudgetPolicy,
    extra_prune_reasons: tuple[str, ...] = (),
) -> BoundedContext:
    prune_reasons = list(extra_prune_reasons)
    if policy.drop_full_diagnosis:
        prune_reasons.append("full_diagnosis_replaced_by_refs")
    if policy.drop_full_logs:
        prune_reasons.append("full_logs_replaced_by_summaries")
    if policy.drop_full_curves:
        prune_reasons.append("full_curves_replaced_by_summaries")

    if len(text) <= policy.max_chars:
        return BoundedContext(
            text=text,
            original_chars=len(text),
            compressed_chars=len(text),
            clipped=False,
            strategy="bounded_commander_feedback",
            policy=policy,
            prune_reasons=tuple(dict.fromkeys(prune_reasons)),
        )

    marker = "\n\n[... commander feedback clipped to context budget ...]\n\n"
    remaining = max(0, policy.max_chars - len(marker))
    head_chars = remaining // 2
    tail_chars = remaining - head_chars
    clipped = text[:head_chars] + marker + text[-tail_chars:]
    prune_reasons.append("feedback_packet_clipped_to_budget")
    return BoundedContext(
        text=clipped,
        original_chars=len(text),
        compressed_chars=len(clipped),
        clipped=True,
        strategy="bounded_commander_feedback",
        policy=policy,
        prune_reasons=tuple(dict.fromkeys(prune_reasons)),
    )


def _positive_int(value: object, default: int) -> int:
    try:
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, float | str | bytes | bytearray):
            parsed = int(value)
        else:
            return default
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
