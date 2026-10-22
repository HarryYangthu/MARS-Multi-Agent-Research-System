"""Actual registered metadata and live policy files, without tool doubles."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.bridge.commander_tools import TOOLS, ToolSpec, commander_tool_is_read_only, commander_tool_mutates


@pytest.mark.parametrize("name", [
    "routing.repository_check", "code.repo_list", "code.repo_search", "code.repo_read_lines", "context.read_material",
    "run.recovery_status", "run.status", "artifact.read", "metrics.evaluate", "diagnosis.failure_analysis",
    "get_run_status", "get_diagnosis", "list_runs", "discovery.status", "discovery.replay",
    "knowledge.research_memory", "knowledge.research_memory_read", "query_kb", "tools.describe",
])
def test_actual_read_handlers_have_explicit_metadata(name: str) -> None:
    assert TOOLS[name].mutation_level == "read"


@pytest.mark.parametrize("name", [
    "run.create", "run.start", "run.recover", "run.existing_model", "create_and_start_run", "run.correct_scope",
    "approve_node", "reject_node", "set_metric_targets", "artifact.review", "user.approval",
    "discovery.create", "discovery.start", "discovery.pause", "discovery.resume", "discovery.stop",
])
def test_actual_state_changes_default_to_non_repeatable_metadata(name: str) -> None:
    assert TOOLS[name].mutation_level == "write"


def test_missing_policy_uses_declaration_and_unknown_names_are_conservative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy = (tmp_path / "tools.yaml").resolve()
    policy.write_text("tools: {}\n")
    monkeypatch.setenv("MARS_TOOLS_CONFIG_PATH", str(policy))
    assert not commander_tool_mutates("list_runs")
    assert commander_tool_mutates("approve_node") and commander_tool_mutates("reject_node")
    assert commander_tool_mutates("completely.unknown")
    spec = ToolSpec(name="new.tool", description="Metadata only", parameters={}, handler=TOOLS["list_runs"].handler)
    assert spec.mutation_level == "write"


def test_current_real_policy_overrides_metadata_and_is_reloaded_after_edit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy = (tmp_path / "tools.yaml").resolve()
    policy.write_text("tools:\n  list_runs: {mutation_level: write}\n  approve_node: {mutation_level: read}\n"
                      "  configured.only: {mutation_level: read}\n")
    monkeypatch.setenv("MARS_TOOLS_CONFIG_PATH", str(policy))
    assert commander_tool_mutates("list_runs")
    assert not commander_tool_mutates("approve_node")
    assert not commander_tool_mutates("configured.only")
    assert not commander_tool_is_read_only("configured.only")
    assert commander_tool_is_read_only("approve_node")
    assert not commander_tool_is_read_only("list_runs")
    policy.write_text("tools:\n  list_runs: {mutation_level: read}\n  approve_node: {mutation_level: write}\n"
                      "  configured.only: {mutation_level: unclassified}\n")
    assert not commander_tool_mutates("list_runs")
    assert commander_tool_mutates("approve_node") and commander_tool_mutates("configured.only")
    assert commander_tool_is_read_only("list_runs")
    assert not commander_tool_is_read_only("approve_node")
