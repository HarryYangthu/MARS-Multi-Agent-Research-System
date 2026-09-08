"""Pure context-policy tests; these records describe packing, not tool execution."""
from app.harness.agent_loop.executor import missing_review_evidence


def test_required_evidence_survives_unrelated_omissions() -> None:
    history = [{"tool": "evidence", "ok": True}, {"tool": "other", "ok": True}]
    assert missing_review_evidence(history, {"omitted_history": [1]}, ("evidence",), observation_chars=512) == []


def test_required_evidence_compression_prevents_review() -> None:
    history = [{"tool": "evidence", "ok": True}]
    assert missing_review_evidence(history, {"compressed_history": [0]}, ("evidence",), observation_chars=512)


def test_batch_group_indices_are_used() -> None:
    history = [{"tool": "other", "ok": True, "native_batch_id": 1},
               {"tool": "evidence", "ok": True, "native_batch_id": 1},
               {"tool": "other", "ok": True}]
    assert missing_review_evidence(history, {"omitted_history": [0]}, ("evidence",), observation_chars=512)
    assert missing_review_evidence(history, {"omitted_history": [1]}, ("evidence",), observation_chars=512) == []


def test_leaf_string_truncation_prevents_review() -> None:
    history = [{"tool": "evidence", "ok": True, "content": "x" * 513}]
    assert missing_review_evidence(history, {}, ("evidence",), observation_chars=512)


def test_failed_evidence_does_not_claim_review_material() -> None:
    history = [{"tool": "evidence", "ok": False}]
    assert missing_review_evidence(history, {"omitted_history": [0]}, ("evidence",), observation_chars=512) == []
