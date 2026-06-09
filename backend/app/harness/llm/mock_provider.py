"""Mock LLM provider (★ V0 critical).

When no real API key is configured (or ``MARS_MOCK_MODE=always``) every Agent
falls through to this provider. Outputs are crafted so that, when written
into an Agent's expected ``output_schema``, validation succeeds 100% of the
time. This is what lets the Dev E2E demo (ACCEPTANCE §1.1) run with zero
external dependencies.

Per DESIGN §16.1 + ACCEPTANCE §1.1.
"""
from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from typing import Any

from app.harness.llm.provider_base import (
    Completion,
    Delta,
    LLMConfig,
    LLMProvider,
    Message,
)
from app.harness.schema.frontmatter_parser import dumps as fm_dumps


# ----------------------------------------------------------- per-schema fakes


def _fake_proposal(seed: str, debate_role: str | None) -> dict[str, Any]:
    angles = {
        "proposer": "argues a hard top-2 router achieves the goal cleanly",
        "critic": "raises router robustness under stream switching",
        "judge": "synthesizes proposer + critic into a hybrid recommendation",
        None: "balanced",
    }
    angle = angles.get(debate_role or "", "balanced")
    return {
        "schema": "proposal.v1",
        "project": "moe-pimc",
        "agent": "idea",
        "created": datetime.now(tz=timezone.utc).isoformat(),
        "research_question": f"How to simplify ATK-MoE routing while preserving RES? (seed:{seed[:8]})",
        "hypothesis": (
            f"A hard top-2 router preserves RES within 1.5 dB. "
            f"[{angle}]"
        ),
        "novelty": (
            f"Combines stream-aware gating with hard routing — angle: {angle}."
        ),
        "theoretical_basis": "Sparse expert activation theory.",
        "constraints": ["baseline_compat: required", "ASIC_resource: ≤40% reduction"],
        "related_literature": [
            {"title": "MoE Routing Survey 2024", "url": "https://arxiv.org/abs/2404.00000"},
        ],
        "debate_summary": {"rounds": 0, "consensus": ""},
    }


def _fake_experiment_plan(seed: str) -> dict[str, Any]:
    return {
        "schema": "experiment_plan.v1",
        "project": "moe-pimc",
        "agent": "experiment",
        "upstream_artifact": "idea_proposal.approved.md",
        "variables": {
            "independent": ["expert_count", "router_type"],
            "controlled": ["batch_size", "epochs"],
            "dependent": ["RES", "PIM", "APE"],
        },
        "metrics": {"primary": "RES", "secondary": ["PIM", "APE", "param_count"]},
        "baseline_ref": {
            "matched_run_id": None,
            "match_score": None,
            "reuse_decision": "rerun",
        },
        "ablations": [
            {"name": "expert_count_4", "config": {"expert_count": 4}},
            {"name": "expert_count_8", "config": {"expert_count": 8}},
            {"name": "expert_count_16", "config": {"expert_count": 16}},
        ],
        "estimated_runs": 6,
        "estimated_gpu_hours": 18,
    }


def _fake_code_spec(seed: str) -> dict[str, Any]:
    return {
        "schema": "code_spec.v1",
        "project": "moe-pimc",
        "agent": "coding",
        "upstream_artifact": "experiment_plan.approved.md",
        "target_lang": "python",
        "baseline_compat": {
            "preserved": True,
            "rationale": (
                "forward(x, stream_label) signature unchanged; new "
                "Paper_Router_v2 added alongside existing Paper_Total_0327."
            ),
        },
        "files_changed": [
            {"path": "libs/Model.py", "type": "modified", "risk": "medium"},
            {"path": "tests/test_router_v2.py", "type": "added", "risk": "low"},
        ],
        "new_dependencies": [],
        "test_coverage": {"unit_tests_added": 3, "baseline_smoke_test": "pass"},
    }


def _fake_run_log(seed: str) -> dict[str, Any]:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return {
        "schema": "run_log.v1",
        "project": "moe-pimc",
        "agent": "execution",
        "upstream_artifact": "code_spec.approved.md",
        "run_id": f"mock_{digest}",
        "batch_size": 512,
        "gpu_used": [],
        "duration_seconds": 30.0,
        "status": "completed",
        "metrics": {"RES": -41.7, "PIM": -18.2, "APE": 23.1, "loss": 0.0142},
        "fingerprint_hash": f"sha256:{digest}",
        "is_mock": True,
    }


def _fake_report(seed: str, debate_role: str | None) -> dict[str, Any]:
    role = debate_role or "balanced"
    return {
        "schema": "report.v1",
        "project": "moe-pimc",
        "agent": "writing",
        "deliverable_type": "research_report",
        "target_audience": "phd_advisor",
        "chain_refs": {
            "proposal": "idea_proposal.approved.md",
            "plan": "experiment_plan.approved.md",
            "code": "code_spec.approved.md",
            "runs": ["execution/run_log.approved.md"],
        },
        "debate_summary": {
            "rounds": 1,
            "reviewer_critiques": [
                f"({role}) discuss ASIC area implications more concretely.",
                f"({role}) add ablation against soft router baseline.",
            ],
        },
    }


def _fake_diagnosis(seed: str, debate_role: str | None) -> dict[str, Any]:
    return {
        "schema": "diagnosis.v1",
        "project": "moe-pimc",
        "agent": "diagnosis",
        "failed_node": "execution",
        "root_cause": "Execution diverged: loss became NaN after a bad config.",
        "recommended_action": "revise_coding",
        "target_node": "coding",
        "attempt": 1,
        "confidence": 0.6,
    }


def _fake_experiment_plan_w(seed: str, role: str | None = None) -> dict[str, Any]:
    return _fake_experiment_plan(seed)


def _fake_code_spec_w(seed: str, role: str | None = None) -> dict[str, Any]:
    return _fake_code_spec(seed)


def _fake_run_log_w(seed: str, role: str | None = None) -> dict[str, Any]:
    return _fake_run_log(seed)


_FakeBuilder = Callable[[str, str | None], dict[str, Any]]

_FAKE_BUILDERS: dict[str, _FakeBuilder] = {
    "proposal.v1": _fake_proposal,
    "experiment_plan.v1": _fake_experiment_plan_w,
    "code_spec.v1": _fake_code_spec_w,
    "run_log.v1": _fake_run_log_w,
    "report.v1": _fake_report,
    "diagnosis.v1": _fake_diagnosis,
}


def build_fake_metadata(
    schema_id: str, *, seed: str = "", debate_role: str | None = None
) -> dict[str, Any]:
    """Public helper used by tests / Agent skeletons that need a fake doc.

    Returns a dict that is guaranteed to validate against the named schema.
    """
    builder = _FAKE_BUILDERS.get(schema_id)
    if builder is None:
        raise ValueError(f"no mock builder for schema '{schema_id}'")
    return builder(seed, debate_role)


def _seed_from_messages(messages: list[Message]) -> str:
    body = "".join(m.content for m in messages)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _render_body(schema_id: str, debate_role: str | None) -> str:
    role = debate_role or "default"
    return (
        f"# Mock {schema_id} (role={role})\n\n"
        f"This artifact was generated by mock_provider.\n"
        f"Contents are placeholder but schema-valid.\n"
    )


class MockProvider(LLMProvider):
    """Deterministic, schema-valid placeholder responses."""

    name = "mock"

    def __init__(self, *, default_schema: str | None = None) -> None:
        self.default_schema = default_schema

    def _resolve_schema(self, config: LLMConfig) -> str:
        return (
            config.response_schema
            or config.extra.get("response_schema")
            or self.default_schema
            or "proposal.v1"
        )

    async def complete(
        self, messages: list[Message], config: LLMConfig
    ) -> Completion:
        schema_id = self._resolve_schema(config)
        debate_role = config.extra.get("debate_role")
        seed = _seed_from_messages(messages) + (debate_role or "")
        metadata = build_fake_metadata(
            schema_id, seed=seed, debate_role=debate_role
        )
        body = _render_body(schema_id, debate_role)
        text = fm_dumps(metadata, body)
        return Completion(
            text=text,
            provider="mock",
            model=config.model or "mock-1",
            is_mock=True,
            debate_role=debate_role,
            raw={"schema": schema_id},
        )

    async def stream(
        self, messages: list[Message], config: LLMConfig
    ) -> AsyncIterator[Delta]:
        completion = await self.complete(messages, config)
        # yield in ~64-char chunks for realistic UI streaming
        chunk_size = 64
        for i in range(0, len(completion.text), chunk_size):
            yield Delta(text=completion.text[i : i + chunk_size])
            await asyncio.sleep(0)
        yield Delta(text="", finish_reason="stop")
