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
