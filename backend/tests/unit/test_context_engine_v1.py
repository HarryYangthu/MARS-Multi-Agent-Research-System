from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.harness.context import engine as context_engine
from app.harness.context.engine import (
    CompileContextInput,
    compile_context,
    summarize_handoff_artifact,
)
from app.harness.context.manifest_v2 import ContextSegment, estimate_tokens
from app.harness.context.raw_store import read_raw_context, write_raw_context


def test_context_segment_hash_and_token_estimate() -> None:
    segment = ContextSegment(
        id="task:1",
        kind="task",
        title="Task",
        source_ref="input/user_request.md",
        text="abcd" * 20,
        priority="critical",
        selection_reason="test",
    )
    payload = segment.to_manifest_dict()
    assert payload["content_hash"] == segment.content_hash
    assert payload["tokens_estimated"] == estimate_tokens(segment.text)
    assert payload["priority"] == "critical"
    assert "text" not in payload


def test_compile_context_writes_precall_manifest_and_keeps_critical_segments(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    result = compile_context(
        CompileContextInput(
            agent="coding",
            node_key="coding",
            project="moe-pimc",
            output_schema="code_spec.v1",
            system="system rules",
            project_context="project rules",
            task="Implement a safe router patch.",
            upstream={"idea.approved.md": "---\nschema: proposal.v1\n---\n" + ("body " * 1200)},
            metadata={},
            run_id="run-1",
            run_root=run_root,
            purpose="draft",
            tool_names=("code.repo_reader", "code.patch_generator"),
        )
    )
    assert result.manifest_path is not None
    assert result.manifest_path.exists()
    index = run_root / "context" / "context_manifest.v2.json"
    assert index.exists()
    data = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert data["schema"] == "context_manifest.v2"
    assert data["messages_preview"]
    assert data["diagnostics"]["compression_counts"]["summary"] >= 1
    assert "packing" in data["diagnostics"]
    kinds = {segment["kind"] for segment in data["segments"]}
    assert {"system", "schema", "task"}.issubset(kinds)
    critical = [item for item in data["segments"] if item["priority"] == "critical"]
    assert critical


def test_compile_context_records_dropped_low_priority_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def small_budget(name: str, default: int) -> int:
        if name == "mars_context_target_tokens":
            return 80
        if name == "mars_context_max_tokens":
            return 140
        return default

    monkeypatch.setattr(context_engine, "_settings_int", small_budget)
    result = compile_context(
        CompileContextInput(
            agent="idea",
            node_key="idea",
            project="moe-pimc",
            output_schema="proposal.v1",
            system="system rules",
            project_context="project rules",
            task="Keep the critical current task.",
            upstream={"research_sites.catalog": "optional source\n" * 600},
            metadata={},
            purpose="preview",
        ),
        write=False,
    )
    packing = result.manifest.diagnostics["packing"]
    assert packing["dropped"] >= 1
    assert any(
        item["action"] == "drop_low_priority_over_target"
        for item in packing["decisions"]
    )
    kinds = {segment.kind for segment in result.manifest.segments}
    assert {"system", "schema", "task"}.issubset(kinds)


def test_raw_context_roundtrip(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    raw_ref = write_raw_context(
        run_root=run_root,
        agent="coding",
        label="tool.output",
        payload={"rows": list(range(20))},
    )
    payload = read_raw_context(run_root=run_root, raw_ref=raw_ref, max_chars=200)
    assert payload["raw_ref"] == raw_ref
    assert "rows" in payload["content"]


def test_diagnostics_flags_confusing_tool_sets() -> None:
    result = compile_context(
        CompileContextInput(
            agent="coding",
            node_key="coding",
            project="moe-pimc",
            output_schema="code_spec.v1",
            system="system",
            project_context="project",
            task="read repository and patch router",
            upstream={},
            metadata={},
            purpose="preview",
            tool_names=tuple(f"tool.{index}" for index in range(16)),
        ),
        write=False,
    )
    assert result.manifest.diagnostics["warnings"]
    assert "tool_count_high" in result.manifest.diagnostics["warnings"]


def test_handoff_summary_preserves_schema_and_source() -> None:
    summary = summarize_handoff_artifact(
        text="---\nschema: proposal.v1\nresearch_question: q\n---\nLong body",
        source_ref="idea/idea_proposal.approved.md",
    )
    assert "proposal.v1" in summary
    assert "idea/idea_proposal.approved.md" in summary
    assert "Approved: yes" in summary
