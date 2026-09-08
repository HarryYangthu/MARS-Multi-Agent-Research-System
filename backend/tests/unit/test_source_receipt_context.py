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


def test_reason_leaf_is_bounded_and_explicitly_truncated() -> None:
    from app.harness.agent_loop.context import compact
    authored = {"reason": "x" * 10000, "url": "https://example.org/schema-only"}
    short = compact(authored, 16000)
    assert short["reason"]["excerpt"] == "x" * 512
    assert short["reason"]["truncated"] is True
    assert short["url"] == authored["url"]


def test_actual_late_child_keeps_true_page_prefixes_within_original_budget() -> None:
    from app.harness.agent_loop.executor import missing_review_evidence
    checkpoint = os.environ.get("MARS_TEST_PROGRESSIVE_CONTEXT_CHECKPOINT")
    if not checkpoint:
        pytest.skip("requires actual late research checkpoint and original events")
    path = Path(checkpoint)
    state = json.loads(path.read_text())
    events = [json.loads(line) for line in path.with_name("events.jsonl").read_text().splitlines()]
    first = next(event for event in events if event["kind"] == "model_request")
    budget = next(event["budget"] for event in reversed(events) if event["kind"] == "context_packed")
    pinned = [Message(role=message["role"], content=message["content"]) for message in first["visible"]]
    history_before = json.dumps(state["history"], sort_keys=True)
    messages, manifest = pack_context(pinned, state["history"], state["feedback"], state["candidate"],
                                      budget=budget, observation_chars=16000, native=True,
                                      validation_issues=state["validation_issues"])
    assert json.dumps(state["history"], sort_keys=True) == history_before
    calls = {item["native_call"]["id"]: item["native_call"] for item in state["history"] if item.get("native_call")}
    for position, message in enumerate(messages):
        if message.role == "assistant" and message.tool_calls:
            assert len(message.content) <= 512 + len(" [reason truncated; full text in raw trace]")
            for offset, call in enumerate(message.tool_calls, 1):
                assert call.arguments == calls[call.id]["arguments"]
                assert call.name == calls[call.id]["name"]
                assert messages[position + offset].role == "tool"
                assert messages[position + offset].tool_call_id == call.id
    originals = [page["text"] for item in state["history"] if item.get("tool") == "search.fetch_sources"
                 and isinstance(item.get("output"), dict)
                 for row in item["output"].get("sources", []) for page in row.get("visible_pages", [])]
    preserved = []
    for message in messages:
        if not message.content.startswith("[shortened actual Observation;"):
            continue
        item = json.loads(message.content.split("\n", 1)[1])
        for row in item.get("output", {}).get("sources", []):
            for page in row.get("visible_pages", []):
                text = page["text"]
                if isinstance(text, dict):
                    assert text["truncated"] is True
                    assert any(original.startswith(text["excerpt"]) for original in originals)
                    preserved.append(text["excerpt"])
    assert preserved  # Some genuine prefixes remain; no claim every required passage is visible.
    assert manifest["estimated_upper_bound_tokens"] <= budget
    assert missing_review_evidence(state["history"], manifest, ("search.fetch_sources",), observation_chars=16000)
