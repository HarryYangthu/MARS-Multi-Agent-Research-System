from __future__ import annotations

from pathlib import Path

import pytest

from app.execution.adapters.base import AdapterAction, AdapterRequest, AdapterResponse
from app.execution.adapters.registry import AdapterRegistry
from app.harness.discovery.models import (
    ArchiveSnapshot,
    CandidateRecord,
    CandidateStatus,
    ModelGenome,
)
from app.harness.project_packs.registry import (
    ProjectPackError,
    ProjectPackRegistry,
)


def test_discovery_records_round_trip() -> None:
    candidate = CandidateRecord(
        candidate_id="c1",
        run_id="r1",
        creator="coding",
        operator="config_mutation",
        genome=ModelGenome(family="demo"),
        idempotency_key="r1:0:c1",
        status=CandidateStatus.VALIDATED,
    )
    restored = CandidateRecord.model_validate_json(candidate.model_dump_json())
    assert restored == candidate

    snapshot = ArchiveSnapshot(
        snapshot_id="s1",
        run_id="r1",
        iteration=0,
        pareto_candidate_ids=(candidate.candidate_id,),
        snapshot_hash="sha256:abc",
    )
    assert ArchiveSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


def test_project_pack_registry_loads_and_rejects_duplicate(tmp_path: Path) -> None:
    root = tmp_path / "packs"
    pack = root / "demo"
    pack.mkdir(parents=True)
    (pack / "project_pack.yaml").write_text(
        "\n".join(
            [
                "schema_id: project_pack.v1",
                "project_id: demo",
                "display_name: Demo",
                "pack_version: 1.0.0",
                'requires_core: \">=3.0.0,<3.1.0\"',
            ]
        ),
        encoding="utf-8",
    )
    registry = ProjectPackRegistry(core_version="3.0.0-dev")
    registry.load_paths([root])
    assert registry.get("demo").manifest.display_name == "Demo"
    with pytest.raises(ProjectPackError, match="duplicate"):
        registry.load_paths([root])


def test_project_pack_registry_rejects_incompatible_core(tmp_path: Path) -> None:
    pack = tmp_path / "project_pack.yaml"
    pack.write_text(
        "\n".join(
            [
                "schema_id: project_pack.v1",
                "project_id: future",
                "display_name: Future",
                "pack_version: 1.0.0",
                'requires_core: \">=4.0.0,<5.0.0\"',
            ]
        ),
        encoding="utf-8",
    )
    registry = ProjectPackRegistry(core_version="3.0.0")
    with pytest.raises(ProjectPackError, match="requires core"):
        registry.load_paths([tmp_path])


class _Adapter:
    name = "demo"

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(request_id=request.request_id, status="ok")


@pytest.mark.asyncio
async def test_adapter_contract_and_duplicate_registration() -> None:
    registry = AdapterRegistry()
    registry.register("demo", _Adapter())
    with pytest.raises(ValueError, match="duplicate"):
        registry.register("demo", _Adapter())
    result = await registry.get("demo").invoke(
        AdapterRequest(
            action=AdapterAction.READINESS,
            request_id="req-1",
            project="demo",
        )
    )
    assert result.status == "ok"
