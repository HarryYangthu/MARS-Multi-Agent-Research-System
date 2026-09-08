"""Source address preservation under real context compression, never invented findings."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.harness.agent_loop.context import pack_context, source_receipt_index
from app.harness.llm.provider_base import Message


def test_empty_or_failed_observations_have_no_source_receipts() -> None:
    assert source_receipt_index([]) == []
    assert source_receipt_index([{"ok": False, "output": None}]) == []


def test_real_archived_receipts_remain_pinned_when_full_history_is_compressed() -> None:
    archive = os.environ.get("MARS_TEST_SOURCE_RECEIPT_CHECKPOINT")
    if not archive:
        pytest.skip("requires explicit actual source-read checkpoint")
    state = json.loads(Path(archive).read_text())
    history = state["history"]
    receipts = source_receipt_index(history)
    assert len(receipts) >= 2
    messages, manifest = pack_context([Message("system", "Read actual sources.")], history, "", "",
                                      budget=22000, observation_chars=16000, native=True)
    assert manifest["compressed_history"] or manifest["omitted_history"]
    index = next(message.content for message in messages if "[untrusted source receipt index;" in message.content)
    for receipt in receipts:
        assert receipt["read_receipt"] in index
        assert receipt["sha256"] in index
        assert "paper_finding" not in receipt
        assert "quote" not in receipt
    assert manifest["estimated_upper_bound_tokens"] <= 22000


def test_invalid_untrusted_metadata_is_bounded_without_fabricating_receipts() -> None:
    value = {"ok": True, "output": {"sources": [{"ok": True, "read_receipt": "x" * 5000}]}}
    assert source_receipt_index([value]) == [{"omitted_receipt_entries": 1,
        "reason": "Metadata exceeds bounded receipt index; consult actual tool history."}]
