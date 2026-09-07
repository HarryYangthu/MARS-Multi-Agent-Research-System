"""Local provenance is a receipt of bytes, not proof that a hypothesis is true."""
from pathlib import Path

from app.harness.kb.provenance import record_artifact, verified_memory
from app.harness.kb.stores import reset_for_tests


def test_receipt_binds_text_run_project_and_source_snapshot(tmp_path: Path) -> None:
    reset_for_tests(tmp_path / "knowledge")
    source = tmp_path / "authored.md"
    source.write_text("Human hypothesis: movable knots may improve approximation.")
    metadata = record_artifact(path=source, run_id="authored-note", project="lut")
    assert verified_memory("movable knots", metadata)
    assert not verified_memory("measured improvement of 2 dB", metadata)
    assert not verified_memory("movable knots", {**metadata, "run_id": "other"})
    assert not verified_memory("movable knots", {**metadata, "project": "other"})
    assert not verified_memory("movable knots", {**metadata, "is_mock": True})
    source.write_text("Changed note")
    assert verified_memory("movable knots", metadata)  # receipt snapshots the original bytes
    receipt = Path(metadata["artifact_receipt"])
    receipt.write_text(receipt.read_text().replace("movable", "fixed"))
    assert not verified_memory("fixed knots", metadata)


def test_claimed_origin_without_host_receipt_is_rejected(tmp_path: Path) -> None:
    reset_for_tests(tmp_path / "knowledge")
    assert not verified_memory("claimed result", {"origin": "local_artifact", "approved": True})


def test_two_stores_verify_their_own_receipts_without_global_state(tmp_path: Path) -> None:
    from app.harness.kb.stores import KBRecord, KBStores
    from app.harness.kb.embedder import embed
    left, right = KBStores(tmp_path / "left"), KBStores(tmp_path / "right")
    source = tmp_path / "note.md"
    source.write_text("Human research note: test interpolation boundaries.")
    text = source.read_text()
    metadata = record_artifact(path=source, run_id="note", project="lut", base=left.base)
    record = KBRecord(id="note", zone="methodology", text=text, metadata=metadata, embedding=embed(text))
    left.zone("methodology").add(record)
    right.zone("methodology").add(record)
    assert len(left.zone("methodology").all(exclude_mock=True)) == 1
    assert right.zone("methodology").all(exclude_mock=True) == []
