"""Pure contract/refusal tests; never substitute providers or tool outcomes."""
from dataclasses import replace
from pathlib import Path

import pytest

from app.agents.base import ContextPack, RunRequest
from app.agents.idea.research_delegate import (
    TOOL, load_delegated_research, make_research_registry,
    verified_delegated_evidence, verified_delegated_reports,
)
from app.harness.llm.model_registry import get_agent_config
from app.harness.tools.config import tool_config
from app.harness.tools.registry import get_registry


def test_registry_fork_preserves_real_tools_and_gates_without_sharing_maps() -> None:
    registry = get_registry()
    child = registry.fork()
    assert child.names() == registry.names()
    assert child._gates == registry._gates
    assert child._gates is not registry._gates
    assert child._tools is not registry._tools
    assert child._specs is not registry._specs
    child._tools.clear()
    assert registry.names()


def test_research_config_and_explicit_tool_permissions() -> None:
    config = get_agent_config("idea_research")
    assert config.output_schema == "research_report.v1"
    assert not config.thinking_enabled
    assert TOOL not in config.tools
    for name in config.tools:
        assert "idea_research" in tool_config(name).allowed_agents
    assert tool_config(TOOL).allowed_agents == ("idea",)


def test_run_local_delegates_do_not_pollute_global_registry(tmp_path: Path) -> None:
    context = ContextPack(system="", project="constraints", task="question")
    a = RunRequest(project="p", user_request="task", extra={"run_root": str(tmp_path / "a")})
    b = RunRequest(project="p", user_request="task", extra={"run_root": str(tmp_path / "b")})
    first = make_research_registry(get_agent_config("idea"), a, context)
    second = make_research_registry(get_agent_config("idea"), b, context)
    assert first is not second
    assert first.has(TOOL) and second.has(TOOL)
    assert not get_registry().has(TOOL)
    assert make_research_registry(get_agent_config("idea"), a, context) is first
    assert verified_delegated_reports(a) == []
    assert verified_delegated_evidence(a) == []
    assert a.runtime["idea_research_session"] is not b.runtime["idea_research_session"]


@pytest.mark.parametrize("limit", [0, 9, True, "2"])
def test_invalid_delegation_budget_is_rejected(tmp_path: Path, limit: object) -> None:
    config = get_agent_config("idea")
    config = replace(config, raw={**config.raw, "research": {"max_delegations": limit}})
    with pytest.raises(ValueError, match="max_delegations"):
        make_research_registry(config, RunRequest("p", "t", extra={"run_root": str(tmp_path)}), ContextPack("", "", ""))


def test_parent_configuration_cannot_be_mistaken_for_researcher(tmp_path: Path) -> None:
    request = RunRequest("p", "t", extra={"run_root": str(tmp_path)},
                         runtime={"idea_research_config": get_agent_config("idea")})
    with pytest.raises(ValueError, match="independent"):
        make_research_registry(get_agent_config("idea"), request, ContextPack("", "", ""))


@pytest.mark.parametrize("path", ["../../outside.json", "/etc/passwd", "idea/research_delegations/missing.json"])
def test_untrusted_manifest_paths_are_rejected(tmp_path: Path, path: str) -> None:
    with pytest.raises(ValueError):
        load_delegated_research(tmp_path, [{"tool": TOOL, "ok": True, "output": {"manifest_ref": path}}])


def test_no_receipts_does_not_count_as_research(tmp_path: Path) -> None:
    assert load_delegated_research(tmp_path, []) == ([], [])
    assert verified_delegated_evidence(RunRequest("p", "t")) == []


@pytest.mark.asyncio
async def test_unknown_context_ref_reports_available_inputs_without_starting_child(tmp_path: Path) -> None:
    from app.harness.tools.registry import ToolContext
    context = ContextPack("", "", "", upstream={"baseline_code": "actual supplied code"})
    request = RunRequest("p", "task", extra={"run_root": str(tmp_path), "run_id": "refs"})
    registry = make_research_registry(get_agent_config("idea"), request, context)
    result = await registry.dispatch(TOOL, {"gap": "Find evidence for the proposed method", "context_refs": ["guessed"],
                                          "success_criteria": "Read a relevant method description"},
                                    ToolContext(run_id="refs", project="p", agent="idea", extra={"run_root": str(tmp_path)}))
    assert not result.ok
    assert result.output == {"available_context_refs": ["baseline_code"]}
    assert request.runtime["idea_research_session"].attempted == 0
    assert not (tmp_path / "agent_traces" / "idea_research").exists()
