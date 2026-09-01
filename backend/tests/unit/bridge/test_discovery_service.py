from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.bridge.discovery_service import (
    DiscoveryService,
    DiscoveryServiceError,
    _select_candidate,
)
from app.bridge.discovery_types import (
    CandidateProposalRequest,
    DiscoveryLifecycle,
    DiscoveryRunSpec,
    IterationNode,
)
from app.execution.adapters.base import AdapterAction, AdapterRequest, AdapterResponse
from app.harness.discovery.candidate_builder import build_candidate_record
from app.harness.discovery.candidate_builder import genome_fingerprint
from app.harness.discovery.code_candidate import (
    CodeCandidateSpec,
    TensorInterfaceSpec,
    code_candidate_implementation_fingerprint,
    code_candidate_spec_sha256,
)
from app.harness.discovery.code_materialization import (
    CodeBlobOperation,
    CodeMaterializationBundle,
    bundle_sha256,
    content_sha256,
)
from app.harness.discovery.evaluation_aggregate import EvaluationAggregate
from app.harness.discovery.models import (
    BudgetLimits,
    CandidateEvaluation,
    CandidateRecord,
    CandidateStatus,
    FidelityLevel,
    MetricValue,
    ModelGenome,
    ObjectiveDirection,
    ObjectiveSpec,
    ResearchTaskContract,
)
from app.harness.discovery.protocol import DiscoveryEventName
from app.harness.runtime.event_bus import InProcessEventBus
from app.storage.run_store import RunStore
from app.storage.run_store import RunHandle


class FakeCandidateAgent:
    async def propose(self, request: CandidateProposalRequest) -> CandidateRecord:
        genome = ModelGenome(
            family="synthetic_regression",
            hyperparameters={"candidate_index": request.ordinal},
            mutable_zones=("hyperparameters.candidate_index",),
        )
        return build_candidate_record(
            run_id=request.contract.run_id,
            genome=genome,
            creator="fake_candidate_agent",
            operator="sample",
            parent_ids=request.parent_candidate_ids[:1],
            generation=request.iteration,
            iteration=request.iteration,
            metadata={"candidate_index": request.ordinal},
        )


class RecordingCandidateAgent(FakeCandidateAgent):
    def __init__(self) -> None:
        self.requests: list[CandidateProposalRequest] = []

    async def propose(self, request: CandidateProposalRequest) -> CandidateRecord:
        self.requests.append(request)
        return await super().propose(request)


class CodeArtifactCandidateAgent:
    def __init__(self, runs_root: Path, *, declaration: str = "both") -> None:
        self.runs_root = runs_root
        self.declaration = declaration

    async def propose(self, request: CandidateProposalRequest) -> CandidateRecord:
        source_hash = content_sha256(
            b"def build_model(config):\n    return config['model']\n"
        )
        spec = CodeCandidateSpec(
            base_snapshot_id="snap_0123456789abcdef01234567",
            entrypoint="candidate/model.py",
            patch_ref="artifact://coding/candidate-source.py",
            patch_sha256=source_hash,
            touched_paths=("candidate/model.py",),
            interface=TensorInterfaceSpec(
                input_rank=2,
                output_rank=2,
                input_dtype="complex64",
                output_dtype="complex64",
            ),
        )
        bundle = CodeMaterializationBundle(
            base_snapshot_id=spec.base_snapshot_id,
            code_spec_sha256=code_candidate_spec_sha256(spec),
            operations=(
                CodeBlobOperation(
                    path=spec.entrypoint,
                    action="add",
                    content_sha256=source_hash,
                ),
            ),
        )
        run_root = self.runs_root / request.contract.run_id
        spec_ref = "coding/code_candidate_spec.json"
        bundle_ref = "coding/code_materialization_bundle.json"
        (run_root / spec_ref).write_text(spec.model_dump_json(), encoding="utf-8")
        (run_root / bundle_ref).write_text(bundle.model_dump_json(), encoding="utf-8")
        artifact_refs: dict[str, str] = {}
        if self.declaration in {"both", "spec_only"}:
            artifact_refs["code_candidate_spec"] = spec_ref
        if self.declaration in {"both", "bundle_only"}:
            artifact_refs["code_materialization_bundle"] = bundle_ref
        genome = ModelGenome(
            family="synthetic_regression",
            hyperparameters={"candidate_index": request.ordinal},
            mutable_zones=("hyperparameters.candidate_index",),
        )
        implementation = code_candidate_implementation_fingerprint(
            genome_exact_sha256=genome_fingerprint(genome),
            bundle_hash=bundle_sha256(bundle),
        )
        return build_candidate_record(
            run_id=request.contract.run_id,
            genome=genome,
            creator="code_artifact_candidate_agent",
            operator="materialized_code",
            generation=request.iteration,
            iteration=request.iteration,
            implementation_fingerprint=implementation,
            artifact_refs=artifact_refs,
            metadata={"touched_paths": list(spec.touched_paths)},
        )


@dataclass(frozen=True)
class _PreparedCodeCandidate:
    root: Path
    snapshot_ref: str = "discovery/source_snapshots/snapshot"
    workspace_ref: str = "discovery/code_candidate_workspaces/candidate"
    receipt_ref: str = "discovery/code_candidate_receipts/candidate.json"
    receipt_sha256: str = "sha256:" + "1" * 64
    bundle_sha256: str = "sha256:" + "2" * 64
    workspace_manifest_sha256: str = "sha256:" + "3" * 64


class RecordingCodeCandidatePreparer:
    def __init__(self, *, failure: str = "") -> None:
        self.failure = failure
        self.calls: list[
            tuple[RunHandle, ResearchTaskContract, CandidateRecord, CodeCandidateSpec, CodeMaterializationBundle]
        ] = []

    async def prepare(
        self,
        *,
        run: RunHandle,
        contract: ResearchTaskContract,
        candidate: CandidateRecord,
        code_spec: CodeCandidateSpec,
        bundle: CodeMaterializationBundle,
    ) -> _PreparedCodeCandidate:
        self.calls.append((run, contract, candidate, code_spec, bundle))
        if self.failure:
            raise ValueError(self.failure)
        return _PreparedCodeCandidate(root=run.root / "discovery" / "prepared")


class PairedStatCandidateAgent:
    async def propose(self, request: CandidateProposalRequest) -> CandidateRecord:
        is_baseline = request.ordinal == 0
        return build_candidate_record(
            run_id=request.contract.run_id,
            genome=ModelGenome(
                family="synthetic_regression",
                hyperparameters={"candidate_index": 1 if is_baseline else 0},
                mutable_zones=("hyperparameters.candidate_index",),
            ),
            creator="paired_stat_candidate_agent",
            operator="fixed_pair",
            generation=request.iteration,
            iteration=request.iteration,
        )


class FakeAdapter:
    def __init__(self, *, fail_index: int | None = 7) -> None:
        self.fail_index = fail_index
        self.calls: list[AdapterRequest] = []

    @property
    def name(self) -> str:
        return "fake_synthetic_adapter"

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        self.calls.append(request)
        if request.action == AdapterAction.READINESS:
            return AdapterResponse(request_id=request.request_id, status="ready")
        genome = request.config["model_genome"]
        assert isinstance(genome, dict)
        hyperparameters = genome["hyperparameters"]
        assert isinstance(hyperparameters, dict)
        index = int(hyperparameters["candidate_index"])
        if request.action == AdapterAction.EXECUTE and index == self.fail_index:
            return AdapterResponse(
                request_id=request.request_id,
                status="failed",
                error_code="synthetic_failure",
                error="isolated synthetic execution failure",
            )
        if request.action == AdapterAction.EVALUATE:
            return AdapterResponse(
                request_id=request.request_id,
                status="ok",
                raw_metrics={"validation_loss": float(index + 1)},
                artifacts={"metrics": f"artifact://metrics/{index}"},
                resource_usage={
                    "llm_tokens": 0.0,
                    "gpu_seconds": 0.0,
                    "wall_seconds": 0.0,
                    "api_cost": 0.0,
                },
            )
        return AdapterResponse(request_id=request.request_id, status="ok")


class TradeoffAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__(fail_index=None)

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        if request.action != AdapterAction.EVALUATE:
            return await super().invoke(request)
        self.calls.append(request)
        genome = request.config["model_genome"]
        assert isinstance(genome, dict)
        hyperparameters = genome["hyperparameters"]
        assert isinstance(hyperparameters, dict)
        index = int(hyperparameters["candidate_index"])
        return AdapterResponse(
            request_id=request.request_id,
            status="ok",
            raw_metrics={
                "validation_loss": float(index + 1),
                "model_size": float(3 - index),
            },
        )


class SeedThresholdAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__(fail_index=None)

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        if request.action != AdapterAction.EVALUATE:
            return await super().invoke(request)
        self.calls.append(request)
        genome = request.config["model_genome"]
        assert isinstance(genome, dict)
        hyperparameters = genome["hyperparameters"]
        assert isinstance(hyperparameters, dict)
        index = int(hyperparameters["candidate_index"])
        if index == 1:
            value = 10.0
        else:
            value = {11: 9.8, 22: 9.8, 33: 9.91}[request.seed]
        return AdapterResponse(
            request_id=request.request_id,
            status="ok",
            raw_metrics={"validation_loss": value},
        )


class ResourceUsageAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__(fail_index=None)

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        self.calls.append(request)
        usage_by_action = {
            AdapterAction.READINESS: {"wall_seconds": 7.0, "gpu_seconds": 8.0},
            AdapterAction.PREFLIGHT: {"wall_seconds": 1.0, "gpu_seconds": 2.0},
            AdapterAction.EXECUTE: {"wall_seconds": 3.0, "gpu_seconds": 4.0},
            AdapterAction.EVALUATE: {"wall_seconds": 5.0, "gpu_seconds": 6.0},
        }
        if request.action == AdapterAction.READINESS:
            return AdapterResponse(
                request_id=request.request_id,
                status="ready",
                resource_usage=usage_by_action[request.action],
            )
        return AdapterResponse(
            request_id=request.request_id,
            status="ok",
            raw_metrics=(
                {"validation_loss": 1.0}
                if request.action == AdapterAction.EVALUATE
                else {}
            ),
            resource_usage=usage_by_action[request.action],
        )


class RetryablePromotionAdapter(FakeAdapter):
    def __init__(self, *, failures: int) -> None:
        super().__init__(fail_index=None)
        self.failures = failures

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        if (
            request.action == AdapterAction.EVALUATE
            and request.fidelity == "F1"
            and self.failures > 0
        ):
            self.failures -= 1
            self.calls.append(request)
            return AdapterResponse(
                request_id=request.request_id,
                status="failed",
                error_code="remote_status_unavailable",
                error="transient remote status failure",
            )
        return await super().invoke(request)


class PreflightBlockingAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__(fail_index=None)

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        if request.action == AdapterAction.PREFLIGHT:
            genome = request.config["model_genome"]
            assert isinstance(genome, dict)
            hyperparameters = genome["hyperparameters"]
            assert isinstance(hyperparameters, dict)
            if int(hyperparameters["candidate_index"]) == 0:
                return AdapterResponse(
                    request_id=request.request_id,
                    status="blocked",
                    error="synthetic preflight safety violation",
                )
        return await super().invoke(request)


class BlockingAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__(fail_index=None)
        self.execution_started = asyncio.Event()
        self.release_execution = asyncio.Event()
        self._blocked_once = False

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        if request.action == AdapterAction.EXECUTE and not self._blocked_once:
            self._blocked_once = True
            self.execution_started.set()
            await self.release_execution.wait()
        return await super().invoke(request)


def discovery_spec(
    *,
    candidates: int = 20,
    iterations: int = 1,
    idea_mode: str = "auto",
) -> DiscoveryRunSpec:
    payload: dict[str, Any] = {
        "task": "synthetic_model_discovery",
        "project": "synthetic_regression",
        "objective": "minimize validation loss",
        "evaluator_hash": "sha256:synthetic-evaluator",
        "dataset_hash": "sha256:synthetic-dataset",
        "objectives": (
            ObjectiveSpec(
                name="validation_loss",
                direction=ObjectiveDirection.MINIMIZE,
            ),
        ),
        "budget": BudgetLimits(proposals=candidates * iterations),
        "candidates_per_iteration": candidates,
        "max_iterations": iterations,
        "auto_approve": True,
        "idea_mode": idea_mode,
    }
    return DiscoveryRunSpec.model_validate(payload)


def build_service(tmp_path: Path, *, adapter: FakeAdapter | None = None) -> DiscoveryService:
    return DiscoveryService(
        run_store=RunStore(tmp_path / "runs"),
        event_bus=InProcessEventBus(),
        candidate_agent=FakeCandidateAgent(),
        adapter=adapter or FakeAdapter(),
    )


def build_code_candidate_service(
    tmp_path: Path,
    *,
    declaration: str,
    preparer: RecordingCodeCandidatePreparer | None,
) -> tuple[DiscoveryService, FakeAdapter]:
    runs_root = tmp_path / "runs"
    adapter = FakeAdapter(fail_index=None)
    return (
        DiscoveryService(
            run_store=RunStore(runs_root),
            event_bus=InProcessEventBus(),
            candidate_agent=CodeArtifactCandidateAgent(
                runs_root,
                declaration=declaration,
            ),
            adapter=adapter,
            code_candidate_preparer=preparer,
        ),
        adapter,
    )


def _candidate_adapter_calls(adapter: FakeAdapter) -> list[AdapterRequest]:
    return [call for call in adapter.calls if call.candidate_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("declaration", ["spec_only", "bundle_only"])
async def test_partial_code_artifact_declaration_is_quarantined_before_adapter(
    tmp_path: Path,
    declaration: str,
) -> None:
    preparer = RecordingCodeCandidatePreparer()
    service, adapter = build_code_candidate_service(
        tmp_path,
        declaration=declaration,
        preparer=preparer,
    )
    created = await service.create(
        discovery_spec(candidates=1),
        idempotency_key=f"partial-code-artifacts:{declaration}",
    )

    completed = await service.start(created.run_id, wait=True)

    assert completed.quarantined_count == 1
    assert not preparer.calls
    assert not _candidate_adapter_calls(adapter)


@pytest.mark.asyncio
async def test_code_preflight_failure_is_quarantined_before_adapter(
    tmp_path: Path,
) -> None:
    preparer = RecordingCodeCandidatePreparer(failure="strict provenance mismatch")
    service, adapter = build_code_candidate_service(
        tmp_path,
        declaration="both",
        preparer=preparer,
    )
    created = await service.create(
        discovery_spec(candidates=1),
        idempotency_key="code-preflight-failure",
    )

    completed = await service.start(created.run_id, wait=True)

    assert completed.quarantined_count == 1
    assert len(preparer.calls) == 1
    assert not _candidate_adapter_calls(adapter)
    candidate = service.replay(created.run_id).candidates[0]
    assert "strict provenance mismatch" in candidate.failure_reason


@pytest.mark.asyncio
async def test_code_candidate_requires_configured_secure_preparer(
    tmp_path: Path,
) -> None:
    service, adapter = build_code_candidate_service(
        tmp_path,
        declaration="both",
        preparer=None,
    )
    created = await service.create(
        discovery_spec(candidates=1),
        idempotency_key="code-preparer-missing",
    )

    completed = await service.start(created.run_id, wait=True)

    assert completed.quarantined_count == 1
    assert not _candidate_adapter_calls(adapter)


@pytest.mark.asyncio
async def test_complete_code_artifacts_use_injected_preparer_then_adapter(
    tmp_path: Path,
) -> None:
    preparer = RecordingCodeCandidatePreparer()
    service, adapter = build_code_candidate_service(
        tmp_path,
        declaration="both",
        preparer=preparer,
    )
    created = await service.create(
        discovery_spec(candidates=1),
        idempotency_key="complete-code-artifacts",
    )

    completed = await service.start(created.run_id, wait=True)

    assert completed.quarantined_count == 0
    assert completed.evaluated_count == 1
    assert len(preparer.calls) == 1
    _run, _contract, candidate, code_spec, bundle = preparer.calls[0]
    assert code_spec.entrypoint == "candidate/model.py"
    assert bundle.code_spec_sha256 == code_candidate_spec_sha256(code_spec)
    assert candidate.artifact_refs["code_candidate_spec"].endswith(".json")
    assert [call.action for call in _candidate_adapter_calls(adapter)] == [
        AdapterAction.PREFLIGHT,
        AdapterAction.EXECUTE,
        AdapterAction.EVALUATE,
    ]


@pytest.mark.asyncio
async def test_config_only_candidate_never_calls_code_preparer(tmp_path: Path) -> None:
    preparer = RecordingCodeCandidatePreparer(failure="must not be called")
    adapter = FakeAdapter(fail_index=None)
    service = DiscoveryService(
        run_store=RunStore(tmp_path / "runs"),
        event_bus=InProcessEventBus(),
        candidate_agent=FakeCandidateAgent(),
        adapter=adapter,
        code_candidate_preparer=preparer,
    )
    created = await service.create(
        discovery_spec(candidates=1),
        idempotency_key="config-only-code-preparer",
    )

    completed = await service.start(created.run_id, wait=True)

    assert completed.evaluated_count == 1
    assert completed.quarantined_count == 0
    assert not preparer.calls
    assert [call.action for call in _candidate_adapter_calls(adapter)] == [
        AdapterAction.PREFLIGHT,
        AdapterAction.EXECUTE,
        AdapterAction.EVALUATE,
    ]


@pytest.mark.asyncio
async def test_twenty_candidate_loop_isolates_one_failure_and_replays(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    created = await service.create(discovery_spec(), idempotency_key="create-20")

    completed = await service.start(created.run_id, wait=True)

    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert completed.candidate_count == 20
    assert completed.evaluated_count == 19
    assert completed.failed_count == 1
    assert completed.budget.used.proposals == 20
    assert completed.latest_archive is not None
    assert len(completed.latest_archive.pareto_candidate_ids) == 1
    assert completed.selected_candidate_id in completed.latest_archive.pareto_candidate_ids

    replay = service.replay(created.run_id)
    names = {str(event["name"]) for event in replay.events}
    assert DiscoveryEventName.RUN_CREATED.value in names
    assert DiscoveryEventName.BUDGET_DEBITED.value in names
    assert DiscoveryEventName.CANDIDATE_EVALUATED.value in names
    assert DiscoveryEventName.ARCHIVE_UPDATED.value in names
    assert DiscoveryEventName.HITL_REQUESTED.value in names
    assert DiscoveryEventName.HITL_RESOLVED.value in names
    assert DiscoveryEventName.RUN_STOPPED.value in names

    resumed = await service.resume(created.run_id, wait=True)
    assert resumed.budget.used.proposals == 20
    assert resumed.evaluated_count == 19


@pytest.mark.asyncio
async def test_readiness_preflight_execute_and_evaluate_resources_are_charged_once(
    tmp_path: Path,
) -> None:
    adapter = ResourceUsageAdapter()
    service = build_service(tmp_path, adapter=adapter)
    spec = discovery_spec(candidates=1).model_copy(
        update={
            "budget": BudgetLimits(
                proposals=1,
                gpu_seconds=20.0,
                wall_seconds=20.0,
            )
        }
    )
    created = await service.create(spec, idempotency_key="all-action-resources")

    completed = await service.start(created.run_id, wait=True)
    context = service._context(created.run_id)
    used = context.stores.budget.snapshot().used

    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert used.wall_seconds == 16.0
    assert used.gpu_seconds == 20.0
    resumed = await service.resume(created.run_id, wait=True)
    assert resumed.budget.used.proposals == 1
    assert resumed.budget.used.wall_seconds == 16.0
    assert resumed.budget.used.gpu_seconds == 20.0


@pytest.mark.asyncio
async def test_all_candidates_share_the_same_explicit_evaluation_seeds(
    tmp_path: Path,
) -> None:
    adapter = FakeAdapter(fail_index=None)
    service = build_service(tmp_path, adapter=adapter)
    shared_seeds = (101, 202, 303)
    spec = discovery_spec(candidates=3).model_copy(
        update={"evaluation_seeds": shared_seeds}
    )
    created = await service.create(spec, idempotency_key="paired-seed-comparison")

    completed = await service.start(created.run_id, wait=True)
    replay = service.replay(created.run_id)

    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert completed.candidate_count == 3
    assert completed.evaluated_count == 9
    for candidate in replay.candidates:
        candidate_seeds = tuple(
            item.seed
            for item in replay.evaluations
            if item.candidate_id == candidate.candidate_id
        )
        assert candidate_seeds == shared_seeds
    evaluated_calls = [
        request
        for request in adapter.calls
        if request.action == AdapterAction.EVALUATE
    ]
    assert len(evaluated_calls) == 9
    assert {request.seed for request in evaluated_calls} == set(shared_seeds)
    assert len({request.output_dir for request in evaluated_calls}) == 9


@pytest.mark.asyncio
async def test_paired_statistical_gate_resolves_baseline_ordinal_and_promotes_cohort(
    tmp_path: Path,
) -> None:
    adapter = FakeAdapter(fail_index=None)
    service = DiscoveryService(
        run_store=RunStore(tmp_path / "runs"),
        event_bus=InProcessEventBus(),
        candidate_agent=PairedStatCandidateAgent(),
        adapter=adapter,
    )
    shared_seeds = (11, 22, 33)
    spec = discovery_spec(candidates=2).model_copy(
        update={
            "evaluation_seeds": shared_seeds,
            "promotion_policy": {
                "enabled": True,
                "schedule_next_fidelity": True,
                "maximum_fidelity": "F1",
                "thresholds": {"validation_loss": 2.5},
                "statistical_gate": {
                    "enabled": True,
                    "baseline_candidate_ordinal": 0,
                    "objective_name": "validation_loss",
                    "dataset_role": "validation",
                },
            },
        }
    )
    created = await service.create(spec, idempotency_key="paired-stat-promotion")

    completed = await service.start(created.run_id, wait=True)
    replay = service.replay(created.run_id)

    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert completed.promotion_completed_count == 3
    assert len(replay.promotions) == 3
    assert {item.seed for item in replay.promotions} == set(shared_seeds)
    assert all(item.aggregate_refs for item in replay.promotions)
    baseline = next(
        item
        for item in replay.candidates
        if item.metadata["discovery_ordinal"] == 0
    )
    challenger = next(
        item
        for item in replay.candidates
        if item.metadata["discovery_ordinal"] == 1
    )
    baseline_evaluations = [
        item for item in replay.evaluations if item.candidate_id == baseline.candidate_id
    ]
    challenger_evaluations = [
        item for item in replay.evaluations if item.candidate_id == challenger.candidate_id
    ]
    assert len(baseline_evaluations) == 3
    assert len(challenger_evaluations) == 6
    assert {
        (item.fidelity, item.seed) for item in challenger_evaluations
    } == {
        (fidelity, seed)
        for fidelity in (FidelityLevel.F0, FidelityLevel.F1)
        for seed in shared_seeds
    }


@pytest.mark.asyncio
async def test_statistical_promotion_is_all_or_none_across_configured_seeds(
    tmp_path: Path,
) -> None:
    adapter = SeedThresholdAdapter()
    service = DiscoveryService(
        run_store=RunStore(tmp_path / "runs"),
        event_bus=InProcessEventBus(),
        candidate_agent=PairedStatCandidateAgent(),
        adapter=adapter,
    )
    spec = discovery_spec(candidates=2).model_copy(
        update={
            "evaluation_seeds": (11, 22, 33),
            "promotion_policy": {
                "enabled": True,
                "schedule_next_fidelity": True,
                "maximum_fidelity": "F1",
                "thresholds": {"validation_loss": 9.9},
                "statistical_gate": {
                    "enabled": True,
                    "baseline_candidate_ordinal": 0,
                    "objective_name": "validation_loss",
                    "dataset_role": "validation",
                },
            },
        }
    )
    created = await service.create(spec, idempotency_key="paired-all-or-none")

    completed = await service.start(created.run_id, wait=True)
    replay = service.replay(created.run_id)

    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert completed.promotion_completed_count == 0
    assert replay.promotions == ()
    challenger = next(
        item
        for item in replay.candidates
        if item.metadata["discovery_ordinal"] == 1
    )
    challenger_evaluations = tuple(
        item
        for item in replay.evaluations
        if item.candidate_id == challenger.candidate_id
    )
    assert {(item.fidelity, item.seed) for item in challenger_evaluations} == {
        (FidelityLevel.F0, 11),
        (FidelityLevel.F0, 22),
        (FidelityLevel.F0, 33),
    }


@pytest.mark.asyncio
async def test_threshold_only_promotion_is_all_or_none_across_shared_seeds(
    tmp_path: Path,
) -> None:
    adapter = SeedThresholdAdapter()
    service = DiscoveryService(
        run_store=RunStore(tmp_path / "runs"),
        event_bus=InProcessEventBus(),
        candidate_agent=PairedStatCandidateAgent(),
        adapter=adapter,
    )
    spec = discovery_spec(candidates=2).model_copy(
        update={
            "evaluation_seeds": (11, 22, 33),
            "promotion_policy": {
                "enabled": True,
                "schedule_next_fidelity": True,
                "maximum_fidelity": "F1",
                "thresholds": {"validation_loss": 9.9},
            },
        }
    )
    created = await service.create(spec, idempotency_key="threshold-all-or-none")

    completed = await service.start(created.run_id, wait=True)
    replay = service.replay(created.run_id)

    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert replay.promotions == ()
    assert {item.fidelity for item in replay.evaluations} == {FidelityLevel.F0}
    assert len(replay.evaluations) == 6


@pytest.mark.asyncio
async def test_paired_statistical_gate_advances_reference_lane_through_f2(
    tmp_path: Path,
) -> None:
    adapter = FakeAdapter(fail_index=None)
    service = DiscoveryService(
        run_store=RunStore(tmp_path / "runs"),
        event_bus=InProcessEventBus(),
        candidate_agent=PairedStatCandidateAgent(),
        adapter=adapter,
    )
    shared_seeds = (11, 22, 33)
    spec = discovery_spec(candidates=2).model_copy(
        update={
            "evaluation_seeds": shared_seeds,
            "promotion_policy": {
                "enabled": True,
                "schedule_next_fidelity": True,
                "maximum_fidelity": "F2",
                "thresholds": {"validation_loss": 2.5},
                "statistical_gate": {
                    "enabled": True,
                    "baseline_candidate_ordinal": 0,
                    "objective_name": "validation_loss",
                    "dataset_role": "validation",
                },
            },
        }
    )
    created = await service.create(
        spec,
        idempotency_key="paired-stat-promotion-f2",
    )

    completed = await service.start(created.run_id, wait=True)
    replay = service.replay(created.run_id)

    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert completed.promotion_completed_count == 12
    assert len(replay.promotions) == 12
    assert {
        task.purpose for task in replay.promotions
    } == {"candidate_promotion", "statistical_baseline"}
    assert len(
        [task for task in replay.promotions if task.purpose == "statistical_baseline"]
    ) == 6
    baseline = next(
        item
        for item in replay.candidates
        if item.metadata["discovery_ordinal"] == 0
    )
    challenger = next(
        item
        for item in replay.candidates
        if item.metadata["discovery_ordinal"] == 1
    )
    baseline_cohorts = {
        (item.fidelity, item.seed)
        for item in replay.evaluations
        if item.candidate_id == baseline.candidate_id
    }
    challenger_cohorts = {
        (item.fidelity, item.seed)
        for item in replay.evaluations
        if item.candidate_id == challenger.candidate_id
    }
    assert baseline_cohorts == {
        (fidelity, seed)
        for fidelity in (
            FidelityLevel.F0,
            FidelityLevel.F1,
            FidelityLevel.F2,
        )
        for seed in shared_seeds
    }
    assert challenger_cohorts == {
        (fidelity, seed)
        for fidelity in (
            FidelityLevel.F0,
            FidelityLevel.F1,
            FidelityLevel.F2,
        )
        for seed in shared_seeds
    }
    context = service._context(created.run_id)
    aggregates = tuple(
        EvaluationAggregate.model_validate_json(path.read_text(encoding="utf-8"))
        for path in (
            context.run.root / "discovery" / "evaluation_aggregates"
        ).glob("*.json")
    )
    assert any(
        aggregate.fidelity == FidelityLevel.F2
        and aggregate.dataset_role == "validation"
        and {pair.seed for pair in aggregate.pairs} == set(shared_seeds)
        for aggregate in aggregates
    )
    reference_evaluation_ids = {
        task.result_evaluation_id
        for task in replay.promotions
        if task.purpose == "statistical_baseline"
    }
    search_state = context.stores.search.load()
    assert search_state is not None
    assert reference_evaluation_ids.isdisjoint(
        search_state.observed_evaluation_ids
    )
    baseline_search_state = next(
        item
        for item in search_state.candidates
        if item.candidate_id == baseline.candidate_id
    )
    assert baseline_search_state.fidelity == FidelityLevel.F0
    assert baseline_search_state.evaluation_count == len(shared_seeds)
    assert all(
        evaluation.evaluation_id in search_state.observed_evaluation_ids
        for evaluation in replay.evaluations
        if evaluation.candidate_id == baseline.candidate_id
        and evaluation.fidelity == FidelityLevel.F0
    )


@pytest.mark.asyncio
async def test_iterations_are_child_run_dag_nodes(tmp_path: Path) -> None:
    service = build_service(tmp_path, adapter=FakeAdapter(fail_index=None))
    created = await service.create(
        discovery_spec(candidates=2, iterations=2),
        idempotency_key="create-dag",
    )

    completed = await service.start(created.run_id, wait=True)

    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert len(completed.iteration_nodes) == 2
    first, second = completed.iteration_nodes
    assert first.parent_run_id == created.run_id
    assert first.status == "completed"
    assert second.depends_on_run_ids == (first.child_run_id,)
    assert service.run_store.get(first.child_run_id) is not None
    assert service.run_store.get(second.child_run_id) is not None


@pytest.mark.asyncio
async def test_pause_resume_is_idempotent_and_does_not_double_debit(tmp_path: Path) -> None:
    adapter = BlockingAdapter()
    service = build_service(tmp_path, adapter=adapter)
    created = await service.create(
        discovery_spec(candidates=3),
        idempotency_key="create-pause",
    )
    await service.start(created.run_id)
    await asyncio.wait_for(adapter.execution_started.wait(), timeout=2.0)

    paused = await service.pause(created.run_id, reason="test_pause")
    repeated = await service.pause(created.run_id, reason="test_pause")
    assert paused.lifecycle == DiscoveryLifecycle.PAUSED
    assert repeated.checkpoint_sequence == paused.checkpoint_sequence

    adapter.release_execution.set()
    resumed = await service.resume(created.run_id, wait=True)
    resumed_again = await service.resume(created.run_id, wait=True)
    assert resumed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert resumed.budget.used.proposals == 3
    assert resumed_again.budget.used.proposals == 3


@pytest.mark.asyncio
async def test_stop_is_terminal_and_repeated_stop_is_safe(tmp_path: Path) -> None:
    adapter = BlockingAdapter()
    service = build_service(tmp_path, adapter=adapter)
    created = await service.create(
        discovery_spec(candidates=3),
        idempotency_key="create-stop",
    )
    await service.start(created.run_id)
    await asyncio.wait_for(adapter.execution_started.wait(), timeout=2.0)

    stopped = await service.stop(created.run_id, reason="test_stop")
    stopped_again = await service.stop(created.run_id, reason="ignored")
    adapter.release_execution.set()
    await service.wait(created.run_id)

    assert stopped.lifecycle == DiscoveryLifecycle.STOPPED
    assert stopped.stop_reason == "test_stop"
    assert stopped_again.checkpoint_sequence == stopped.checkpoint_sequence


@pytest.mark.asyncio
async def test_new_service_recovers_interrupted_candidate_without_double_debit(
    tmp_path: Path,
) -> None:
    adapter = BlockingAdapter()
    first = build_service(tmp_path, adapter=adapter)
    created = await first.create(
        discovery_spec(candidates=3),
        idempotency_key="create-recovery",
    )
    await first.start(created.run_id)
    await asyncio.wait_for(adapter.execution_started.wait(), timeout=2.0)
    task = first._controls[created.run_id].task
    assert task is not None
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    recovered = DiscoveryService(
        run_store=first.run_store,
        event_bus=InProcessEventBus(),
        candidate_agent=FakeCandidateAgent(),
        adapter=FakeAdapter(fail_index=None),
    )
    completed = await recovered.resume(created.run_id, wait=True)

    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert completed.evaluated_count == 3
    assert completed.budget.used.proposals == 3


def test_old_request_defaults_to_fast_idea_mode(tmp_path: Path) -> None:
    del tmp_path
    spec = discovery_spec().model_dump(mode="json")
    spec.pop("idea_mode")
    assert DiscoveryRunSpec.model_validate(spec).idea_mode == "fast"


@pytest.mark.asyncio
async def test_candidate_hitl_decisions_are_audited_idempotent_and_selectable(
    tmp_path: Path,
) -> None:
    service = build_service(tmp_path, adapter=FakeAdapter(fail_index=None))
    manual_spec = discovery_spec(candidates=3).model_copy(update={"auto_approve": False})
    created = await service.create(manual_spec, idempotency_key="candidate-hitl")

    waiting = await service.start(created.run_id, wait=True)

    assert waiting.lifecycle == DiscoveryLifecycle.WAITING_HITL
    replay = service.replay(created.run_id)
    elite = next(item for item in replay.candidates if item.status == CandidateStatus.ELITE)
    dominated = [
        item for item in replay.candidates if item.status == CandidateStatus.DOMINATED
    ]
    assert len(dominated) == 2

    rejected = await service.decide_candidate(
        created.run_id,
        elite.candidate_id,
        action="reject",
        actor="researcher",
        reason="reject archive leader",
        idempotency_key="decision-reject",
    )
    approved = await service.decide_candidate(
        created.run_id,
        dominated[0].candidate_id,
        action="approve",
        actor="researcher",
        reason="approve alternative",
        idempotency_key="decision-approve",
    )
    promoted = await service.decide_candidate(
        created.run_id,
        dominated[1].candidate_id,
        action="promote",
        actor="researcher",
        reason="promote diverse candidate",
        idempotency_key="decision-promote",
    )
    repeated = await service.decide_candidate(
        created.run_id,
        dominated[1].candidate_id,
        action="promote",
        actor="researcher",
        reason="promote diverse candidate",
        idempotency_key="decision-promote",
    )

    assert rejected.candidate.status == CandidateStatus.REJECTED
    assert approved.candidate.status == CandidateStatus.DOMINATED
    assert promoted.candidate.status == CandidateStatus.PROMOTED
    assert repeated == promoted
    run = service.run_store.get(created.run_id)
    assert run is not None
    assert (run.root / promoted.audit_ref).exists()
    with pytest.raises(DiscoveryServiceError, match="idempotency key"):
        await service.decide_candidate(
            created.run_id,
            dominated[1].candidate_id,
            action="reject",
            actor="researcher",
            reason="promote diverse candidate",
            idempotency_key="decision-promote",
        )

    completed = await service.resume(created.run_id, wait=True)

    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert completed.selected_candidate_id == dominated[1].candidate_id
    events = service.replay(created.run_id).events
    decision_events = [
        item
        for item in events
        if item["name"] == DiscoveryEventName.HITL_RESOLVED.value
        and item["payload"].get("scope") == "candidate"
    ]
    assert [item["payload"]["action"] for item in decision_events] == [
        "reject",
        "approve",
        "promote",
    ]


@pytest.mark.asyncio
async def test_real_evaluations_feed_persistent_bandit_and_parent_signals(
    tmp_path: Path,
) -> None:
    agent = RecordingCandidateAgent()
    service = DiscoveryService(
        run_store=RunStore(tmp_path / "runs"),
        event_bus=InProcessEventBus(),
        candidate_agent=agent,
        adapter=FakeAdapter(fail_index=None),
    )
    created = await service.create(
        discovery_spec(candidates=2, iterations=2),
        idempotency_key="adaptive-signals",
    )

    completed = await service.start(created.run_id, wait=True)

    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert completed.stop_code == "max_iterations"
    assert len(agent.requests) == 4
    second = agent.requests[1]
    assert second.operator_arms[0].arm_id == "sample"
    assert second.operator_arms[0].pulls == 1
    first_next_iteration = agent.requests[2]
    assert first_next_iteration.operator_arms[0].pulls == 2
    assert first_next_iteration.operator_arms[0].total_reward == -1.0
    assert len(first_next_iteration.parent_candidates) == 1
    assert first_next_iteration.parent_candidates[0].quality == -1.0
    assert first_next_iteration.parent_candidates[0].offspring_count == 0
    run = service.run_store.get(created.run_id)
    assert run is not None
    assert (run.root / "discovery" / "search" / "state.json").is_file()


@pytest.mark.asyncio
async def test_patience_stops_early_with_machine_readable_reason(
    tmp_path: Path,
) -> None:
    service = build_service(tmp_path, adapter=FakeAdapter(fail_index=None))
    spec = discovery_spec(candidates=2, iterations=5).model_copy(
        update={
            "stop_policy": {
                "max_without_improvement": 1,
                "min_valid_candidates": 2,
            }
        }
    )
    created = await service.create(spec, idempotency_key="patience-stop")

    completed = await service.start(created.run_id, wait=True)

    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert completed.candidate_count == 2
    assert completed.next_iteration == 1
    assert completed.stop_reason == "completed"
    assert completed.stop_code == "patience_exhausted"
    assert completed.stop_details == ("without_improvement=1",)


@pytest.mark.asyncio
async def test_shared_seeds_do_not_exhaust_patience_with_one_candidate(
    tmp_path: Path,
) -> None:
    service = build_service(tmp_path, adapter=FakeAdapter(fail_index=None))
    spec = discovery_spec(candidates=1, iterations=3).model_copy(
        update={
            "evaluation_seeds": (101, 202, 303),
            "stop_policy": {
                "max_without_improvement": 1,
                "min_valid_candidates": 1,
            },
        }
    )
    created = await service.create(spec, idempotency_key="shared-seed-patience")

    completed = await service.start(created.run_id, wait=True)
    context = service._context(created.run_id)
    state = context.stores.search.load()

    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert completed.stop_code == "patience_exhausted"
    assert completed.candidate_count == 2
    assert completed.evaluated_count == 6
    assert state is not None
    assert state.valid_candidates == 2
    assert state.operator_arms[0].pulls == 2
    assert state.since_last_improvement == 1


@pytest.mark.asyncio
async def test_opt_in_quarantine_safety_stop_is_terminal(tmp_path: Path) -> None:
    service = build_service(tmp_path, adapter=PreflightBlockingAdapter())
    spec = discovery_spec(candidates=2, iterations=5).model_copy(
        update={"stop_policy": {"stop_on_quarantine": True}}
    )
    created = await service.create(spec, idempotency_key="safety-stop")

    stopped = await service.start(created.run_id, wait=True)

    assert stopped.lifecycle == DiscoveryLifecycle.STOPPED
    assert stopped.stop_code == "safety_violation"
    assert stopped.quarantined_count == 1
    assert len(stopped.stop_details) == 1


@pytest.mark.asyncio
async def test_auto_selection_uses_primary_objective_not_candidate_id(
    tmp_path: Path,
) -> None:
    service = build_service(tmp_path, adapter=TradeoffAdapter())
    spec = discovery_spec(candidates=3).model_copy(
        update={
            "objectives": (
                ObjectiveSpec(
                    name="validation_loss",
                    direction=ObjectiveDirection.MINIMIZE,
                ),
                ObjectiveSpec(
                    name="model_size",
                    direction=ObjectiveDirection.MINIMIZE,
                ),
            )
        }
    )
    created = await service.create(spec, idempotency_key="ranked-selection")

    completed = await service.start(created.run_id, wait=True)
    replay = service.replay(created.run_id)
    selected_evaluation = next(
        item
        for item in replay.evaluations
        if item.candidate_id == completed.selected_candidate_id
    )

    assert completed.latest_archive is not None
    assert len(completed.latest_archive.pareto_candidate_ids) == 3
    assert selected_evaluation.canonical_metrics["validation_loss"].value == 1.0
    assert completed.selection_evidence["source"] == "automatic_ranker"
    rankings = completed.selection_evidence["rankings"]
    assert rankings[0]["candidate_id"] == completed.selected_candidate_id

    selected_metric = selected_evaluation.canonical_metrics["validation_loss"]
    unstable_repeat = selected_evaluation.model_copy(
        update={
            "evaluation_id": "unstable-repeat",
            "seed": 999,
            "canonical_metrics": {
                **selected_evaluation.canonical_metrics,
                "validation_loss": selected_metric.model_copy(update={"value": 9.0}),
            },
        }
    )
    conservative = _select_candidate(
        contract=spec.contract(created.run_id),
        candidates={item.candidate_id: item for item in replay.candidates},
        evaluations=(*replay.evaluations, unstable_repeat),
        pareto_candidate_ids=completed.latest_archive.pareto_candidate_ids,
    )
    stable_second = next(
        item.candidate_id
        for item in replay.evaluations
        if item.canonical_metrics["validation_loss"].value == 2.0
    )
    assert conservative.candidate_id == stable_second


@pytest.mark.asyncio
async def test_explicit_promotion_policy_marks_only_threshold_candidate(
    tmp_path: Path,
) -> None:
    service = build_service(tmp_path, adapter=FakeAdapter(fail_index=None))
    spec = discovery_spec(candidates=2).model_copy(
        update={
            "promotion_policy": {
                "enabled": True,
                "mark_promoted": True,
                "thresholds": {"validation_loss": 1.5},
            }
        }
    )
    created = await service.create(spec, idempotency_key="automatic-promotion")

    completed = await service.start(created.run_id, wait=True)
    replay = service.replay(created.run_id)

    promoted = [
        item for item in replay.candidates if item.status == CandidateStatus.PROMOTED
    ]
    assert len(promoted) == 1
    promoted_evaluation = next(
        item for item in replay.evaluations if item.candidate_id == promoted[0].candidate_id
    )
    assert promoted_evaluation.canonical_metrics["validation_loss"].value == 1.0
    assert completed.selected_candidate_id == promoted[0].candidate_id


@pytest.mark.asyncio
async def test_scheduled_promotion_runs_f0_to_f2_with_distinct_evidence(
    tmp_path: Path,
) -> None:
    adapter = FakeAdapter(fail_index=None)
    service = build_service(tmp_path, adapter=adapter)
    spec = discovery_spec(candidates=1).model_copy(
        update={
            "promotion_policy": {
                "enabled": True,
                "schedule_next_fidelity": True,
                "maximum_fidelity": "F2",
                "thresholds": {"validation_loss": 1.5},
            }
        }
    )
    created = await service.create(spec, idempotency_key="scheduled-promotion-f2")

    completed = await service.start(created.run_id, wait=True)
    replay = service.replay(created.run_id)

    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert completed.evaluated_count == 3
    assert completed.promotion_completed_count == 2
    assert completed.promotion_pending_count == 0
    assert [item.fidelity for item in replay.evaluations] == [
        FidelityLevel.F0,
        FidelityLevel.F1,
        FidelityLevel.F2,
    ]
    assert [item.state.value for item in replay.promotions] == [
        "completed",
        "completed",
    ]
    candidate = replay.candidates[0]
    assert candidate.status != CandidateStatus.PROMOTED
    evaluated_calls = [
        request
        for request in adapter.calls
        if request.action == AdapterAction.EVALUATE
    ]
    assert [request.fidelity for request in evaluated_calls] == ["F0", "F1", "F2"]
    assert len({request.output_dir for request in evaluated_calls}) == 3
    context = service._context(created.run_id)
    resource_transactions = [
        transaction
        for transaction in context.stores.budget.transactions()
        if transaction.idempotency_key.startswith("evaluation-resources:")
    ]
    assert len(resource_transactions) == 3
    assert len({item.idempotency_key for item in resource_transactions}) == 3


@pytest.mark.asyncio
async def test_retryable_promotion_failure_reuses_request_and_recovers(
    tmp_path: Path,
) -> None:
    adapter = RetryablePromotionAdapter(failures=1)
    service = build_service(tmp_path, adapter=adapter)
    spec = discovery_spec(candidates=1).model_copy(
        update={
            "promotion_policy": {
                "enabled": True,
                "schedule_next_fidelity": True,
                "maximum_fidelity": "F1",
                "max_attempts": 3,
                "thresholds": {"validation_loss": 1.5},
            }
        }
    )
    created = await service.create(spec, idempotency_key="promotion-retry-success")

    completed = await service.start(created.run_id, wait=True)
    replay = service.replay(created.run_id)
    f1_requests = [
        request
        for request in adapter.calls
        if request.action == AdapterAction.EVALUATE and request.fidelity == "F1"
    ]

    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert len(replay.promotions) == 1
    assert replay.promotions[0].state.value == "completed"
    assert replay.promotions[0].attempts == 2
    assert len(f1_requests) == 2
    assert len({request.request_id for request in f1_requests}) == 1


@pytest.mark.asyncio
async def test_exhausted_promotion_retry_budget_fails_run_explicitly(
    tmp_path: Path,
) -> None:
    adapter = RetryablePromotionAdapter(failures=10)
    service = build_service(tmp_path, adapter=adapter)
    spec = discovery_spec(candidates=1).model_copy(
        update={
            "promotion_policy": {
                "enabled": True,
                "schedule_next_fidelity": True,
                "maximum_fidelity": "F1",
                "max_attempts": 2,
                "thresholds": {"validation_loss": 1.5},
            }
        }
    )
    created = await service.create(spec, idempotency_key="promotion-retry-exhausted")

    failed = await service.start(created.run_id, wait=True)
    replay = service.replay(created.run_id)
    f1_requests = [
        request
        for request in adapter.calls
        if request.action == AdapterAction.EVALUATE and request.fidelity == "F1"
    ]

    assert failed.lifecycle == DiscoveryLifecycle.FAILED
    assert failed.promotion_failed_count == 1
    assert len(replay.promotions) == 1
    assert replay.promotions[0].attempts == 2
    assert len(f1_requests) == 2
    assert len({request.request_id for request in f1_requests}) == 1


@pytest.mark.asyncio
async def test_scheduled_promotion_preserves_all_paired_seed_cohorts(
    tmp_path: Path,
) -> None:
    adapter = FakeAdapter(fail_index=None)
    service = build_service(tmp_path, adapter=adapter)
    shared_seeds = (7, 8, 9)
    spec = discovery_spec(candidates=1).model_copy(
        update={
            "evaluation_seeds": shared_seeds,
            "promotion_policy": {
                "enabled": True,
                "schedule_next_fidelity": True,
                "maximum_fidelity": "F1",
                "thresholds": {"validation_loss": 1.5},
            },
        }
    )
    created = await service.create(spec, idempotency_key="paired-seed-promotion")

    completed = await service.start(created.run_id, wait=True)
    replay = service.replay(created.run_id)

    assert completed.evaluated_count == 6
    assert completed.promotion_completed_count == 3
    assert {(task.from_fidelity, task.to_fidelity, task.seed) for task in replay.promotions} == {
        (FidelityLevel.F0, FidelityLevel.F1, seed) for seed in shared_seeds
    }
    assert {(item.fidelity, item.seed) for item in replay.evaluations} == {
        (fidelity, seed)
        for fidelity in (FidelityLevel.F0, FidelityLevel.F1)
        for seed in shared_seeds
    }


@pytest.mark.asyncio
async def test_committed_seed_is_not_reexecuted_when_batch_resumes(
    tmp_path: Path,
) -> None:
    adapter = FakeAdapter(fail_index=None)
    service = build_service(tmp_path, adapter=adapter)
    shared_seeds = (10, 20, 30)
    spec = discovery_spec(candidates=1).model_copy(
        update={"evaluation_seeds": shared_seeds}
    )
    created = await service.create(spec, idempotency_key="seed-batch-recovery")
    context = service._context(created.run_id)
    candidate = build_candidate_record(
        run_id=created.run_id,
        genome=ModelGenome(
            family="synthetic_regression",
            hyperparameters={"candidate_index": 0},
            mutable_zones=("hyperparameters.candidate_index",),
        ),
        creator="seed-recovery-test",
        operator="sample",
    )
    context.stores.candidates.put(candidate)
    context.stores.candidates.record_evaluation(
        _stat_evaluation(
            created.run_id,
            candidate.candidate_id,
            seed=shared_seeds[0],
            value=1.0,
        )
    )
    node = IterationNode(
        iteration=0,
        child_run_id=created.run_id,
        parent_run_id=created.run_id,
    )

    await service._evaluate_candidate(context, node, candidate, 0, 0)

    evaluations = context.stores.candidates.list_evaluations(
        candidate_id=candidate.candidate_id
    )
    assert tuple(item.seed for item in evaluations) == shared_seeds
    assert [
        request.seed
        for request in adapter.calls
        if request.action == AdapterAction.EVALUATE
    ] == [20, 30]


@pytest.mark.asyncio
async def test_reconcile_backfills_task_after_evaluation_commit_window(
    tmp_path: Path,
) -> None:
    adapter = FakeAdapter(fail_index=None)
    service = build_service(tmp_path, adapter=adapter)
    spec = discovery_spec(candidates=1).model_copy(
        update={
            "promotion_policy": {
                "enabled": True,
                "schedule_next_fidelity": True,
                "maximum_fidelity": "F1",
                "thresholds": {"validation_loss": 1.5},
            }
        }
    )
    created = await service.create(spec, idempotency_key="promotion-reconcile")
    context = service._context(created.run_id)
    candidate = build_candidate_record(
        run_id=created.run_id,
        genome=ModelGenome(
            family="synthetic_regression",
            hyperparameters={"candidate_index": 0},
            mutable_zones=("hyperparameters.candidate_index",),
        ),
        creator="recovery-test",
        operator="sample",
    )
    context.stores.candidates.put(candidate)
    source = context.stores.candidates.record_evaluation(
        _stat_evaluation(
            created.run_id,
            candidate.candidate_id,
            seed=spec.seed,
            value=1.0,
        )
    )
    assert source.fidelity == FidelityLevel.F0
    assert context.stores.promotions.list() == []
    node = IterationNode(
        iteration=0,
        child_run_id=created.run_id,
        parent_run_id=created.run_id,
    )

    await service._reconcile_promotion_tasks(context, node, iteration=0)
    control = service._control(created.run_id)
    control.gate.set()
    await service._drain_promotion_tasks(
        context,
        control,
        node,
        iteration=0,
    )

    tasks = context.stores.promotions.list()
    evaluations = context.stores.candidates.list_evaluations(
        candidate_id=candidate.candidate_id
    )
    assert len(tasks) == 1
    assert tasks[0].state.value == "completed"
    assert [item.fidelity for item in evaluations] == [
        FidelityLevel.F0,
        FidelityLevel.F1,
    ]
    assert [
        request.fidelity
        for request in adapter.calls
        if request.action == AdapterAction.EVALUATE
    ] == ["F1"]


@pytest.mark.asyncio
async def test_statistical_promotion_gate_persists_evidence_without_terminal_mark(
    tmp_path: Path,
) -> None:
    service = build_service(tmp_path, adapter=FakeAdapter(fail_index=None))
    spec = discovery_spec(candidates=1).model_copy(
        update={
            "evaluation_seeds": (1, 2, 3),
            "promotion_policy": {
                "enabled": True,
                "thresholds": {"validation_loss": 9.95},
                "statistical_gate": {
                    "enabled": True,
                    "baseline_candidate_id": "baseline-stat",
                    "objective_name": "validation_loss",
                    "dataset_role": "validation",
                },
            }
        }
    )
    created = await service.create(spec, idempotency_key="statistical-promotion")
    context = service._context(created.run_id)
    baseline = _named_candidate(created.run_id, "baseline-stat", value=100)
    candidate = _named_candidate(created.run_id, "candidate-stat", value=101)
    context.stores.candidates.put(baseline)
    context.stores.candidates.put(candidate)
    for seed, candidate_value in enumerate((9.80, 9.85, 9.90), start=1):
        context.stores.candidates.record_evaluation(
            _stat_evaluation(
                created.run_id,
                baseline.candidate_id,
                seed=seed,
                value=10.0,
            )
        )
        current = context.stores.candidates.record_evaluation(
            _stat_evaluation(
                created.run_id,
                candidate.candidate_id,
                seed=seed,
                value=candidate_value,
            )
        )

    payload = await service._apply_promotion_policy(
        context,
        IterationNode(
            iteration=0,
            child_run_id=created.run_id,
            parent_run_id=created.run_id,
        ),
        current,
        iteration=0,
    )

    assert payload["promote"] is True
    assert payload["eligible_for_next_fidelity"] is True
    assert payload["statistical_gate"]["passed"] is True
    assert payload["statistical_gate"]["dataset_role"] == "validation"
    assert "status_applied" not in payload
    stored_candidate = context.stores.candidates.get(candidate.candidate_id)
    assert stored_candidate is not None
    assert stored_candidate.status == CandidateStatus.EVALUATED
    references = payload["statistical_gate"]["aggregate_refs"]
    assert len(references) == 1
    aggregate_path = context.run.root / references[0]
    assert aggregate_path.is_file()
    aggregate = EvaluationAggregate.model_validate_json(
        aggregate_path.read_text(encoding="utf-8")
    )
    assert aggregate.schema_id == "evaluation_aggregate.v1"
    assert aggregate.dataset_role == "validation"
    assert aggregate.search_feedback_allowed is True


@pytest.mark.asyncio
async def test_holdout_statistical_gate_fails_closed_without_feedback(
    tmp_path: Path,
) -> None:
    service = build_service(tmp_path, adapter=FakeAdapter(fail_index=None))
    spec = discovery_spec(candidates=1).model_copy(
        update={
            "evaluation_seeds": (1, 2, 3),
            "promotion_policy": {
                "enabled": True,
                "statistical_gate": {
                    "enabled": True,
                    "baseline_candidate_id": "baseline-holdout",
                    "objective_name": "validation_loss",
                    "dataset_role": "holdout",
                },
            }
        }
    )
    created = await service.create(spec, idempotency_key="holdout-promotion")
    context = service._context(created.run_id)
    candidate = _named_candidate(created.run_id, "candidate-holdout", value=102)
    context.stores.candidates.put(candidate)
    evaluation = context.stores.candidates.record_evaluation(
        _stat_evaluation(
            created.run_id,
            candidate.candidate_id,
            seed=1,
            value=1.0,
        )
    )

    payload = await service._apply_promotion_policy(
        context,
        IterationNode(
            iteration=0,
            child_run_id=created.run_id,
            parent_run_id=created.run_id,
        ),
        evaluation,
        iteration=0,
    )

    assert payload["promote"] is False
    assert payload["eligible_for_next_fidelity"] is False
    assert payload["statistical_gate"]["reason_codes"] == (
        "holdout_feedback_forbidden",
    )
    assert not (context.run.root / "discovery/evaluation_aggregates").exists()


def _named_candidate(run_id: str, candidate_id: str, *, value: int) -> CandidateRecord:
    generated = build_candidate_record(
        run_id=run_id,
        genome=ModelGenome(
            family="statistical-test",
            hyperparameters={"value": value},
            mutable_zones=("hyperparameters.value",),
        ),
        creator="test",
        operator="test",
    )
    return generated.model_copy(
        update={
            "candidate_id": candidate_id,
            "idempotency_key": f"idempotency:{candidate_id}",
        }
    )


def _stat_evaluation(
    run_id: str,
    candidate_id: str,
    *,
    seed: int,
    value: float,
) -> CandidateEvaluation:
    return CandidateEvaluation(
        evaluation_id=f"eval-{candidate_id}-{seed}",
        candidate_id=candidate_id,
        run_id=run_id,
        fidelity=FidelityLevel.F0,
        seed=seed,
        evaluator_hash="sha256:synthetic-evaluator",
        dataset_hash="sha256:synthetic-dataset",
        canonical_metrics={
            "validation_loss": MetricValue(
                value=value,
                direction=ObjectiveDirection.MINIMIZE,
            )
        },
        hard_constraints_passed=True,
    )
