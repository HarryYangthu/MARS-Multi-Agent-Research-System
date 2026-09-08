"""Pure context-policy tests; these records describe packing, not tool execution."""
import json
import os
from pathlib import Path

import pytest

from app.harness.agent_loop.context import pack_context, token_upper_bound
from app.harness.agent_loop.executor import missing_review_evidence
from app.harness.agent_loop.trace import canonical
from app.harness.llm.provider_base import Message


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


def test_review_pins_exact_required_observations_beyond_leaf_caps() -> None:
    history = [{"tool": "evidence", "ok": True, "reason": "r" * 667, "content": "p" * 1601},
               {"tool": "other", "ok": True, "content": "x" * 8000}]
    messages, manifest = pack_context([Message("system", "rules")], history, "", "candidate",
                                     budget=6000, observation_chars=512, reviewing=True,
                                     required_review_tools=("evidence",))
    assert any(message.content.endswith(canonical(history[0])) for message in messages)
    assert manifest["preserved_review_history"] == [0]
    assert 0 not in manifest["compressed_history"] + manifest["omitted_history"]
    assert not missing_review_evidence(history, manifest, ("evidence",), observation_chars=512)
    assert token_upper_bound(messages) <= 6000


def test_required_review_evidence_cannot_be_truncated_to_fit() -> None:
    history = [{"tool": "evidence", "ok": True, "content": "x" * 8000}]
    with pytest.raises(ValueError, match="nothing was silently dropped"):
        pack_context([], history, "", "candidate", budget=4000, observation_chars=512,
                     reviewing=True, required_review_tools=("evidence",))


def test_review_preservation_does_not_change_author_packing() -> None:
    history = [{"tool": "evidence", "ok": True, "content": "x" * 8000}]
    _, manifest = pack_context([], history, "", "", budget=4000, observation_chars=512,
                               required_review_tools=("evidence",))
    assert manifest["preserved_review_history"] == []
    assert missing_review_evidence(history, manifest, ("evidence",), observation_chars=512)


def test_preserved_marker_cannot_override_recorded_omission() -> None:
    history = [{"tool": "evidence", "ok": True}]
    assert missing_review_evidence(history, {"preserved_review_history": [0], "omitted_history": [0]},
                                   ("evidence",), observation_chars=512)


def test_actual_failed_review_history_is_preserved_without_model_call() -> None:
    raw = os.environ.get("MARS_TEST_REVIEW_CHECKPOINT")
    if not raw:
        pytest.skip("requires an actual failed review checkpoint; no replacement execution")
    path = Path(raw)
    before = path.read_bytes()
    state = json.loads(before)
    assert state["status"] == "review_evidence_unavailable"
    required_tools = ("idea.research_delegate",)
    messages, manifest = pack_context([], state["history"], "", state["candidate"],
                                     budget=96000, observation_chars=16000, native=True, reviewing=True,
                                     required_review_tools=required_tools)
    assert not missing_review_evidence(state["history"], manifest, required_tools, observation_chars=16000)
    for item in state["history"]:
        if item["tool"] in required_tools and item["ok"]:
            assert any(message.content.endswith(canonical(item)) for message in messages)
    assert path.read_bytes() == before
