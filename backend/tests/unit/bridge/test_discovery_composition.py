from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.bridge.discovery_composition import (
    ProjectPackCandidateAgent,
    ProjectPackRoutingAdapter,
)
from app.bridge.discovery_types import CandidateProposalRequest, DiscoveryRunSpec
from app.bridge.extension_runtime import ExtensionRuntime, build_extension_runtime
from app.execution.adapters.base import AdapterAction, AdapterRequest, AdapterResponse
from app.execution.adapters.registry import AdapterRegistry
from app.harness.discovery.models import (
    ObjectiveDirection,
    ObjectiveSpec,
    ResearchTaskContract,
)


class _ReadyAdapter:
    name = "demo:evaluator"

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(request_id=request.request_id, status="ready")


def _pack_root(root: Path) -> Path:
    pack = root / "demo"
    pack.mkdir(parents=True)
    (pack / "project_pack.yaml").write_text(
        "\n".join(
            (
                "schema_id: project_pack.v1",
                "project_id: demo",
                "display_name: Demo",
                "pack_version: 1.0.0",
                'requires_core: ">=3.0.0,<3.1.0"',
                "distribution: public",
                "capabilities: [model_discovery]",
                "adapters:",
                "  evaluator:",
                "    protocol: adapter.v1",
                "    argv: ['{python}', -c, 'pass']",
            )
        ),
        encoding="utf-8",
    )
    (pack / "project.yaml").write_text("description: demo\n", encoding="utf-8")
    (pack / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
    (pack / "repo_link.yaml").write_text("repo_path: ''\n", encoding="utf-8")
    (pack / "metrics.yaml").write_text("metrics: {}\n", encoding="utf-8")
    (pack / "workflow.yaml").write_text("stages: []\n", encoding="utf-8")
    (pack / "ui_schema.json").write_text(json.dumps({"type": "object"}), encoding="utf-8")
    (pack / "discovery.yaml").write_text(
        "\n".join(
            (
                "schema_id: discovery_config.v1",
                "family: bounded_regressor",
                "candidate_count: 4",
                "operator: config_mutation",
                "operators: [mutate, crossover]",
                "models: [model_a, model_b]",
                "mutable_zones:",
                "  - hyperparameters.depth",
                "  - hyperparameters.rate",
                "search_space:",
                "  depth: [1, 2]",
                "  rate: [0.1, 0.2]",
            )
        ),
        encoding="utf-8",
    )
    return pack


@pytest.mark.asyncio
async def test_pack_candidate_generation_is_seeded_and_audited(tmp_path: Path) -> None:
    pack = _pack_root(tmp_path / "packs")
    runtime = build_extension_runtime(
        distribution="v30-core",
        pack_roots=(pack,),
    )
    agent = ProjectPackCandidateAgent(runtime)
    contract = ResearchTaskContract(
        run_id="run-demo",
        project="demo",
        objective="minimize validation loss",
        seed=17,
    )
    request = CandidateProposalRequest(
        contract=contract,
        iteration=0,
        ordinal=0,
        child_run_id="child-0",
    )

    first = await agent.propose(request)
    repeated = await agent.propose(request)
    second = await agent.propose(request.model_copy(update={"ordinal": 1}))

    assert first.candidate_id == repeated.candidate_id
    assert first.genome == repeated.genome
    assert first.metadata == repeated.metadata
    assert first.candidate_id != second.candidate_id
    assert first.genome.hyperparameters != second.genome.hyperparameters
    audit = first.metadata["selection_audit"]
    assert isinstance(audit, dict)
    assert audit["parent"]["reason"] == "root generation has no parent candidates"
    assert audit["model"]["selected_id"] in {"model_a", "model_b"}
    assert audit["operator"]["selected_id"] in {"mutate", "crossover"}

    child = await agent.propose(
        request.model_copy(
            update={
                "iteration": 1,
                "parent_candidate_ids": (first.candidate_id, second.candidate_id),
            }
        )
    )
    assert len(child.parent_ids) == 1
    assert child.parent_ids[0] in {first.candidate_id, second.candidate_id}


@pytest.mark.asyncio
async def test_router_resolves_one_trusted_adapter_and_fails_closed(tmp_path: Path) -> None:
    pack = _pack_root(tmp_path / "packs")
    base = build_extension_runtime(distribution="v30-core", pack_roots=(pack,))
    adapters = AdapterRegistry()
    adapters.register("demo:evaluator", _ReadyAdapter())
    runtime = ExtensionRuntime(
        profile=base.profile,
        project_packs=base.project_packs,
        adapters=adapters,
        adapter_bindings={("demo", "evaluator"): "demo:evaluator"},
    )
    router = ProjectPackRoutingAdapter(runtime)

    ready = await router.invoke(
        AdapterRequest(
            action=AdapterAction.READINESS,
            request_id="ready-1",
            project="demo",
        )
    )
    missing = await router.invoke(
        AdapterRequest(
            action=AdapterAction.READINESS,
            request_id="ready-2",
            project="missing",
        )
    )

    assert ready.status == "ready"
    assert missing.status == "blocked"
    assert missing.error_code == "project_adapter_unavailable"


def test_production_spec_requires_frozen_evidence_hashes() -> None:
    base = {
        "task": "production-discovery",
        "project": "demo",
        "objective": "minimize validation loss",
        "objectives": (
            ObjectiveSpec(
                name="validation_loss",
                direction=ObjectiveDirection.MINIMIZE,
            ),
        ),
        "project_inputs": {"mode": "production", "fidelity": "F0"},
    }
    with pytest.raises(ValueError, match="production discovery requires frozen"):
        DiscoveryRunSpec.model_validate(base)

    valid = DiscoveryRunSpec.model_validate(
        {
            **base,
            "dataset_hash": "sha256:dataset",
            "baseline_hash": "sha256:baseline",
            "evaluator_hash": "sha256:evaluator",
        }
    )
    assert valid.project_inputs["mode"] == "production"
