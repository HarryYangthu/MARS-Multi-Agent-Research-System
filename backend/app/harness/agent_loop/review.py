"""Explicit reviewer feedback tied to one immutable candidate, without budget resets."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.trace import canonical, digest


@dataclass(frozen=True)
class ExternalReview:
    candidate_digest: str
    reviewer: str
    issues: tuple[str, ...]

    @classmethod
    def from_mapping(cls, raw: object) -> ExternalReview:
        if not isinstance(raw, dict) or set(raw) != {"candidate_digest", "reviewer", "issues"}:
            raise ValueError("review requires candidate_digest, reviewer and issues only")
        fingerprint, reviewer, issues = raw["candidate_digest"], raw["reviewer"], raw["issues"]
        if not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
            raise ValueError("review candidate_digest must be a SHA256 of the canonical candidate string")
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 200:
            raise ValueError("reviewer must be a nonempty label")
        if not isinstance(issues, (list, tuple)) or not 1 <= len(issues) <= 32 or any(
            not isinstance(x, str) or not x.strip() or len(x) > 2000 for x in issues
        ):
            raise ValueError("review requires 1..32 nonempty bounded issues")
        return cls(fingerprint, reviewer, tuple(issues))


def review_revision(state: dict[str, Any], review: ExternalReview, policy: AgentLoopPolicy) -> dict[str, Any]:
    if not state.get("candidate") or digest(state["candidate"]) != review.candidate_digest:
        raise ValueError("external review does not match the current candidate")
    if state.get("pending") is not None:
        raise ValueError("resolve the pending operation before applying external review")
    if policy.mode == "reflection" and state["counts"]["reflections"] >= policy.max_reflections:
        raise ValueError("reflection budget exhausted; external review cannot reset it")
    required_calls = 2 if policy.mode == "reflection" else 1
    if policy.max_model_calls - state["counts"]["model_requests"] < required_calls:
        raise ValueError("insufficient model budget for a reviewed revision")
    return {"status": "running", "next_phase": "act", "reflection_accepted": False,
            "reviewed_candidate_sha": review.candidate_digest,
            "review_issues": list(dict.fromkeys([*state.get("review_issues", []), *review.issues])),
            "feedback": canonical({"external_reviewer": review.reviewer,
                "required_revision": review.issues,
                "instruction": "Resolve the concrete review issues in a complete revised candidate. "
                               "Preserve actual evidence and correct prior claims; do not just delete warnings."})}
