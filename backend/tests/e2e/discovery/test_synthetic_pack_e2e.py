from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from app.agents.base import RunRequest
from app.agents.idea.discovery.models import IdeaMode
from app.agents.idea.discovery.workflow import resolve_idea_mode
from app.bridge.extension_runtime import ExtensionRuntime, build_extension_runtime
from app.execution.adapters.base import AdapterAction, AdapterRequest, AdapterResponse
from app.execution.adapters.process import ProcessAdapter
from app.harness.discovery.archive import ParetoArchive
from app.harness.discovery.models import (
    CandidateEvaluation,
    FidelityLevel,
    MetricValue,
    ObjectiveDirection,
    ObjectiveSpec,
)
from app.harness.runtime.distribution import profile_for
from synthetic_regression_adapter import candidate_configs


PACK_ROOT = Path(__file__).resolve().parents[4] / "projects" / "synthetic_regression"


@pytest.mark.asyncio
async def test_public_pack_runs_twenty_candidates_through_core_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PYTHONPATH", raising=False)
    runtime = build_extension_runtime(
        distribution="v30-core",
        pack_roots=(PACK_ROOT,),
        execution_device="cpu",
        workspace_runs_root=tmp_path,
    )
    adapter = _manifest_adapter(runtime)
    objectives = (
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
    )
    archive = ParetoArchive(objectives)
    candidates = candidate_configs()
    assert len(candidates) == 20

    for index, candidate in enumerate(candidates):
        response = await adapter.invoke(
            AdapterRequest(
                action=AdapterAction.EVALUATE,
                request_id=f"synthetic-{index:02d}",
                project="synthetic_regression",
                run_id="synthetic-e2e",
                candidate_id=f"core-{index:02d}",
                seed=100 + index,
                config={
                    "model_genome": _model_genome(candidate.config),
                    "mode": "mock",
                    "candidate_count": 20,
                    "seed": 100 + index,
                    "fidelity": "F0",
                },
            )
        )
        assert response.status == "ok", response.error
        archive.add(_evaluation(response, f"core-{index:02d}", seed=100 + index))

    assert archive.candidate_ids()
    assert set(archive.candidate_ids()).issubset(
        {f"core-{index:02d}" for index in range(20)}
    )


@pytest.mark.asyncio
async def test_public_pack_is_identical_under_v30_and_v31_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PYTHONPATH", raising=False)
    runtimes = [
        build_extension_runtime(
            distribution=name,
            pack_roots=(PACK_ROOT,),
            execution_device="cpu",
            workspace_runs_root=tmp_path,
        )
        for name in ("v30-core", "v31-wireless")
    ]
    candidate = candidate_configs()[3]
    request = AdapterRequest(
        action=AdapterAction.EVALUATE,
        request_id="compatibility-1",
        project="synthetic_regression",
        candidate_id="core-compatibility-1",
        seed=77,
        config={
            "model_genome": _model_genome(candidate.config),
            "project_inputs": {
                "mode": "mock",
                "candidate_count": 20,
                "seed": 77,
                "fidelity": "F0",
            },
        },
    )
    responses = []
    for runtime in runtimes:
        assert runtime.project_packs.get("synthetic_regression").manifest.distribution == "public"
        adapter = _manifest_adapter(runtime)
        responses.append(await adapter.invoke(request))

    assert responses[0].status == responses[1].status == "ok"
    assert responses[0].raw_metrics == responses[1].raw_metrics
    v30 = profile_for("v30-core")
    v31 = profile_for("v31-wireless")
    assert v30.core_version == v31.core_version
    assert set(v30.capabilities).issubset(v31.capabilities)


def test_v30_request_defaults_remain_valid_with_v31_optional_fields() -> None:
    legacy = RunRequest(
        project="synthetic_regression",
        user_request="Find a compact deterministic regressor.",
    )
    extended = RunRequest(
        project="synthetic_regression",
        user_request=legacy.user_request,
        extra={"idea_mode": "deep", "idea_budget_profile": "fast"},
    )

    assert resolve_idea_mode(legacy) is IdeaMode.FAST
    assert resolve_idea_mode(extended) is IdeaMode.DEEP


def _evaluation(
    response: AdapterResponse, candidate_id: str, *, seed: int
) -> CandidateEvaluation:
    envelope = cast(dict[str, object], response.raw_metrics)
    canonical_raw = cast(dict[str, dict[str, object]], envelope["canonical_metrics"])
    canonical = {
        name: MetricValue(
            value=_metric_number(metric["value"]),
            unit=str(metric["unit"]),
            direction=ObjectiveDirection(str(metric["direction"])),
        )
        for name, metric in canonical_raw.items()
    }
    provenance = cast(dict[str, object], envelope["provenance"])
    return CandidateEvaluation(
        evaluation_id=f"eval-{candidate_id}",
        candidate_id=candidate_id,
        run_id="synthetic-e2e",
        fidelity=FidelityLevel.F0,
        seed=seed,
        evaluator_hash=str(provenance["evaluator_hash"]),
        dataset_hash=str(provenance["dataset_hash"]),
        raw_metrics=cast(dict[str, object], envelope["raw_metrics"]),
        canonical_metrics=canonical,
        hard_constraints_passed=bool(envelope["hard_constraints_passed"]),
    )


def _model_genome(candidate_config: dict[str, object]) -> dict[str, object]:
    return {
        "schema_id": "model_genome.v1",
        "family": candidate_config["family"],
        "structure": {},
        "hyperparameters": candidate_config["hyperparameters"],
        "recipe": {},
        "mutable_zones": (
            "hyperparameters.degree",
            "hyperparameters.regularization",
        ),
    }


def _metric_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AssertionError("synthetic metric must be numeric")
    return float(value)


def _manifest_adapter(runtime: ExtensionRuntime) -> ProcessAdapter:
    # Exercise the product composition, including its explicitly trusted
    # module path and workspace resolver. Rebuilding the adapter here bypassed
    # that environment and accidentally relied on ambient editable installs.
    adapter = runtime.adapters.get(
        runtime.adapter_name("synthetic_regression", "evaluator")
    )
    assert isinstance(adapter, ProcessAdapter)
    assert adapter.env is not None
    assert str((PACK_ROOT / "src").resolve()) in adapter.env["PYTHONPATH"]
    return adapter
