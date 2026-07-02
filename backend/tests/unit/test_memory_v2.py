from __future__ import annotations

from pathlib import Path

import pytest
from pytest import MonkeyPatch

from app.harness.kb.backends import UnsupportedMemoryBackend
from app.harness.kb.config import reset_config_cache_for_tests
from app.harness.kb.consolidate import consolidate
from app.harness.kb.ingester import ingest_memory
from app.harness.kb.models import EvalStatus, memory_from_kb_record
from app.harness.kb.selector import select_memory
from app.harness.kb.stores import KBStores, reset_for_tests
from app.harness.sedimentation.hooks import sediment_approved_artifact
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunStore


PROPOSAL_TEXT = """---
schema: proposal.v1
project: pimc
agent: idea
research_question: "How can routing be simplified while preserving RES?"
hypothesis: "Hard top-2 routing keeps RES degradation below the threshold."
novelty: "Stream-aware hard routing is compared against the current baseline."
---

# Proposal

This mock_provider artifact should be isolated from the four main KB zones.
"""


REAL_PROPOSAL_TEXT = """---
schema: proposal.v1
project: pimc
agent: idea
research_question: "How can routing be simplified while preserving RES?"
hypothesis: "Hard top-2 routing keeps RES degradation below the threshold."
novelty: "Stream-aware hard routing is compared against the current baseline."
---

# Proposal

This approved artifact represents a real reviewed proposal.
"""


def test_legacy_kb_record_loads_as_memory_v2_defaults() -> None:
    record = memory_from_kb_record(
        record_id="legacy-1",
        zone="run_archive",
        text="historic run result",
        metadata={"schema": "run_log.v1", "run_id": "run-legacy"},
    )

    assert record.record_id == "legacy-1"
    assert record.memory_type == "episodic"
    assert record.confidence == 0.45
    assert not record.eval_status.passed


def test_quarantine_is_not_a_default_query_zone(tmp_path: Path) -> None:
    stores = reset_for_tests(base=tmp_path)

    assert "quarantine" not in list(stores.all_zones())
    assert "quarantine" in list(stores.all_zones(include_quarantine=True))


def test_file_backend_adapter_keeps_legacy_store_surface(tmp_path: Path) -> None:
    stores = KBStores(base=tmp_path / "knowledge", store="json")

    assert stores.backend_name == "file"
    written = ingest_memory(
        zone="methodology",
        text="adapter boundary keeps selector and sedimentation stable",
        metadata={"project": "pimc", "kind": "methodology"},
        source_path="tests/adapter.md",
        memory_type="procedural",
        approved=True,
        stores=stores,
    )

    assert len(written) == 1
    assert stores.zone("methodology").all(exclude_mock=False)[0].id == written[0].id
    assert (tmp_path / "knowledge" / "methodology" / "_index.json").exists()


def test_chroma_backend_adapter_round_trip(tmp_path: Path) -> None:
    pytest.importorskip("chromadb")
    stores = KBStores(base=tmp_path / "knowledge", store="chroma")

    written = ingest_memory(
        zone="methodology",
        text="chroma adapter stores governed procedural memory",
        metadata={"project": "pimc", "kind": "methodology"},
        source_path="tests/chroma.md",
        memory_type="procedural",
        approved=True,
        stores=stores,
    )[0]

    records = stores.zone("methodology").all(exclude_mock=False)
    assert stores.backend_name == "chroma"
    assert records[0].id == written.id
    assert records[0].metadata["source_path"] == "tests/chroma.md"
    hits = select_memory(
        query="governed procedural memory",
        zones=["methodology"],
        project="pimc",
        stores=stores,
    )
    assert [hit.record.id for hit in hits] == [written.id]
    assert stores.delete_by_source("tests/chroma.md") == 1
    assert stores.zone("methodology").all(exclude_mock=False) == []


@pytest.mark.parametrize("backend_name", ["sqlite_vss"])
def test_future_backend_adapters_fail_closed_until_enabled(
    tmp_path: Path,
    backend_name: str,
) -> None:
    with pytest.raises(UnsupportedMemoryBackend, match=backend_name):
        KBStores(base=tmp_path / "knowledge", store=backend_name)


def test_approved_mock_artifact_writes_only_quarantine(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_MEMORY_PROFILE", "dev_e2e")
    reset_config_cache_for_tests()
    stores = reset_for_tests(base=tmp_path / "knowledge")
    run = RunStore(tmp_path / "runs").create(
        task="memory",
        project="pimc",
        user_request="test memory",
    )
    art_store = ArtifactStore(run)
    ref = art_store.write(text=PROPOSAL_TEXT)
    approved = art_store.approve(ref)

    result = sediment_approved_artifact(run=run, agent="idea", artifact_ref=approved)

    assert result["is_mock"] is True
    assert result["chunks_written"] >= 1
    assert not stores.zone("literature").all(exclude_mock=False)
    assert not stores.zone("methodology").all(exclude_mock=False)
    quarantined = stores.zone("quarantine").all(exclude_mock=False)
    assert quarantined
    assert all(item.metadata.get("is_mock") is True for item in quarantined)


def test_evaluation_reports_are_sedimented_as_memory(tmp_path: Path) -> None:
    stores = reset_for_tests(base=tmp_path / "knowledge")
    run = RunStore(tmp_path / "runs").create(
        task="eval-memory",
        project="pimc",
        user_request="test eval memory",
    )
    art_store = ArtifactStore(run)
    ref = art_store.write(text=REAL_PROPOSAL_TEXT)
    approved = art_store.approve(ref)

    result = sediment_approved_artifact(run=run, agent="idea", artifact_ref=approved)

    assert result["is_mock"] is False
    assert result["evaluation_chunks_written"] >= 1
    records = stores.zone("methodology").all(exclude_mock=False)
    eval_records = [
        record
        for record in records
        if record.metadata.get("artifact_schema") == "evaluation_report.v1"
    ]
    assert eval_records
    assert {record.metadata.get("evaluator") for record in eval_records} >= {
        "contract.schema_validity",
        "contract.provenance",
    }


def test_selector_filters_mock_and_superseded_records(tmp_path: Path) -> None:
    stores = reset_for_tests(base=tmp_path)
    eval_status = EvalStatus(passed=True, decision="pass")
    good = ingest_memory(
        zone="methodology",
        text="stable router methodology with verified RES comparison",
        metadata={"project": "pimc", "kind": "methodology"},
        memory_type="procedural",
        confidence=0.9,
        eval_status=eval_status,
        salience=0.8,
        approved=True,
        stores=stores,
    )[0]
    mock = ingest_memory(
        zone="methodology",
        text="mock router methodology placeholder",
        metadata={"project": "pimc", "kind": "methodology"},
        memory_type="procedural",
        is_mock=True,
        approved=True,
        stores=stores,
    )[0]
    old = ingest_memory(
        zone="methodology",
        text="obsolete router methodology",
        metadata={"project": "pimc", "kind": "methodology"},
        memory_type="procedural",
        approved=True,
        stores=stores,
    )[0]
    stores.update_metadata("methodology", old.id, {"superseded_by": good.id})

    hits = select_memory(
        query="router methodology RES",
        zones=["methodology"],
        project="pimc",
        memory_type="procedural",
        stores=stores,
    )

    assert [hit.record.id for hit in hits] == [good.id]
    assert mock.id not in [hit.record.id for hit in hits]
    assert old.id not in [hit.record.id for hit in hits]


def test_source_upsert_keeps_all_chunks(tmp_path: Path) -> None:
    stores = reset_for_tests(base=tmp_path)
    text = "\n".join(f"unique memory chunk line {index}" for index in range(30))

    first = ingest_memory(
        zone="methodology",
        text=text,
        metadata={"project": "pimc", "kind": "methodology"},
        source_path="agent/prompts/router.md",
        memory_type="procedural",
        chunk_size=120,
        overlap=0,
        approved=True,
        stores=stores,
    )
    second = ingest_memory(
        zone="methodology",
        text=text + "\nupdated line",
        metadata={"project": "pimc", "kind": "methodology"},
        source_path="agent/prompts/router.md",
        memory_type="procedural",
        chunk_size=120,
        overlap=0,
        approved=True,
        stores=stores,
    )

    stored = stores.zone("methodology").all(exclude_mock=False)
    assert len(first) > 1
    assert len(second) > 1
    assert len(stored) == len(second)


def test_consolidation_archives_main_and_deletes_expired_mock(
    tmp_path: Path,
) -> None:
    stores = reset_for_tests(base=tmp_path)
    main = ingest_memory(
        zone="methodology",
        text="old but real methodology",
        metadata={"project": "pimc", "kind": "methodology"},
        memory_type="procedural",
        source_path="memory/main.md",
        approved=True,
        stores=stores,
    )[0]
    mock = ingest_memory(
        zone="quarantine",
        text="old mock methodology",
        metadata={"project": "pimc", "kind": "methodology"},
        memory_type="procedural",
        source_path="memory/mock.md",
        is_mock=True,
        approved=True,
        stores=stores,
    )[0]
    old_date = "2020-01-01T00:00:00+00:00"
    stores.update_metadata("methodology", main.id, {"valid_from": old_date, "ttl_days": 1})
    stores.update_metadata("quarantine", mock.id, {"valid_from": old_date, "ttl_days": 1})

    report = consolidate(stores=stores)

    assert report.expired == 1
    assert report.mock_deleted == 1
    assert not stores.zone("quarantine").all(exclude_mock=False)
    main_record = stores.zone("methodology").all(exclude_mock=False)[0]
    assert main_record.metadata["expired"] is True
    assert main_record.metadata["archived"] is True
