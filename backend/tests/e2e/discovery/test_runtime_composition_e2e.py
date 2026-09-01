from __future__ import annotations

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.api import dependencies as deps
from app.bridge.discovery_composition import (
    ProjectPackCandidateAgent,
    ProjectPackRoutingAdapter,
)
from app.bridge.discovery_service import DiscoveryService
from app.bridge.discovery_types import DiscoveryLifecycle, DiscoveryRunSpec
from app.bridge.extension_runtime import build_extension_runtime, reset_extension_runtime
from app.harness.discovery.models import (
    BudgetLimits,
    ObjectiveDirection,
    ObjectiveSpec,
)
from app.harness.runtime.event_bus import InProcessEventBus
from app.main import create_app
from app.settings import reset_settings_cache
from app.storage.run_store import RunStore

PACK_ROOT = Path(__file__).resolve().parents[4] / "projects" / "synthetic_regression"


def _spec() -> DiscoveryRunSpec:
    return DiscoveryRunSpec(
        task="synthetic-runtime-discovery",
        project="synthetic_regression",
        objective="Find the deterministic Pareto set.",
        objectives=(
            ObjectiveSpec(
                name="validation_mse",
                direction=ObjectiveDirection.MINIMIZE,
                unit="squared_unit",
            ),
            ObjectiveSpec(
                name="model_terms",
                direction=ObjectiveDirection.MINIMIZE,
                unit="count",
            ),
            ObjectiveSpec(
                name="stability_score",
                direction=ObjectiveDirection.MAXIMIZE,
                unit="ratio",
            ),
        ),
        budget=BudgetLimits(proposals=20),
        seed=41,
        candidates_per_iteration=20,
        max_iterations=1,
        auto_approve=True,
        idea_mode="auto",
        project_inputs={
            "mode": "mock",
            "candidate_count": 20,
            "seed": 41,
            "fidelity": "F0",
        },
    )


async def _run_distribution(
    root: Path,
    distribution: str,
) -> tuple[DiscoveryService, str]:
    runtime = build_extension_runtime(
        distribution=distribution,
        pack_roots=(PACK_ROOT,),
    )
    service = DiscoveryService(
        run_store=RunStore(root / distribution),
        event_bus=InProcessEventBus(),
        candidate_agent=ProjectPackCandidateAgent(runtime),
        adapter=ProjectPackRoutingAdapter(runtime),
    )
    created = await service.create(
        _spec(),
        idempotency_key=f"runtime-e2e:{distribution}",
    )
    completed = await service.start(created.run_id, wait=True)
    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert completed.candidate_count == 20
    assert completed.evaluated_count == 20
    assert completed.failed_count == 0
    assert completed.quarantined_count == 0
    assert completed.budget.used.proposals == 20
    assert completed.latest_archive is not None
    assert completed.latest_archive.pareto_candidate_ids
    return service, created.run_id


@pytest.mark.asyncio
async def test_same_core_runs_full_synthetic_discovery_in_v30_and_v31(
    tmp_path: Path,
) -> None:
    signatures: list[list[tuple[str, dict[str, float]]]] = []
    for distribution in ("v30-core", "v31-wireless"):
        service, run_id = await _run_distribution(tmp_path, distribution)
        replay = service.replay(run_id)
        evaluations = {
            item.candidate_id: item
            for item in replay.evaluations
        }
        signature: list[tuple[str, dict[str, float]]] = []
        for candidate in replay.candidates:
            evaluation = evaluations[candidate.candidate_id]
            assert evaluation.hard_constraints_passed is True
            assert evaluation.raw_metrics["schema_id"] == "metric_envelope.v1"
            assert evaluation.seed == evaluation.raw_metrics["provenance"]["seed"]
            selection_audit = candidate.metadata["selection_audit"]
            assert isinstance(selection_audit, dict)
            assert selection_audit["search"]["seed"] == 41
            genome_key = json.dumps(
                candidate.genome.hyperparameters,
                sort_keys=True,
                separators=(",", ":"),
            )
            signature.append(
                (
                    genome_key,
                    {
                        name: metric.value
                        for name, metric in evaluation.canonical_metrics.items()
                    },
                )
            )
        signatures.append(sorted(signature, key=lambda item: item[0]))
    assert signatures[0] == signatures[1]


def test_main_registers_live_discovery_router_and_public_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_DISTRIBUTION", "v30-core")
    monkeypatch.setenv("MARS_PROJECT_PACK_PATHS", "")
    reset_settings_cache()
    reset_extension_runtime()
    deps._run_store = RunStore(tmp_path / "api-runs")
    deps._bus = InProcessEventBus()
    deps._orchestrator = None
    try:
        client = TestClient(create_app())
        projects = client.get("/api/projects")
        assert projects.status_code == 200
        assert any(
            item["name"] == "synthetic_regression"
            and "model_discovery" in item["capabilities"]
            for item in projects.json()
        )
        created = client.post(
            "/api/discovery/runs",
            json={
                "spec": _spec().model_dump(mode="json"),
                "idempotency_key": "main-router-create",
            },
        )
        assert created.status_code == 200
        assert created.json()["lifecycle"] == "created"
        run_id = str(created.json()["run_id"])

        legacy_detail = client.get(f"/api/runs/{run_id}")
        assert legacy_detail.status_code == 200, legacy_detail.text
        assert legacy_detail.json()["states"] == {"model_discovery": "pending"}
        assert legacy_detail.json()["graph"]["nodes"][0]["kind"] == "external_service"
        assert legacy_detail.json()["graph"]["nodes"][0]["metadata"]["read_only"] is True

        legacy_stats = client.get("/api/stats")
        assert legacy_stats.status_code == 200, legacy_stats.text
        assert legacy_stats.json()["runs_total"] == 1

        legacy_start = client.post(f"/api/runs/{run_id}/start")
        assert legacy_start.status_code == 409, legacy_start.text
        assert "dedicated service API" in str(legacy_start.json()["detail"])
    finally:
        deps.reset_for_tests()
        reset_settings_cache()
        reset_extension_runtime()
