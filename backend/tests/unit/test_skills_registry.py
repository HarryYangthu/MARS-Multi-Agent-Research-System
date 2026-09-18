from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from app.harness.skills import load_selected_skills, skill_acceptance_errors
from app.storage.agent_context_store import load_agent_runtime_resources, _write_context_atomic


def test_selected_skill_binds_real_resource_version_and_requires_granted_tools(tmp_path: Path) -> None:
    instructions = tmp_path / "procedure.md"
    instructions.write_text("Inspect the baseline and preserve protected interfaces.")
    registry = tmp_path / "skills.yaml"
    registry.write_text(yaml.safe_dump({"version": 1, "skills": {"review": {
        "version": "2.0.0", "instructions": "procedure.md", "required_tools": ["code.repo_reader"],
        "acceptance": {"required_tool_successes": ["code.repo_reader"], "output_schemas": ["proposal.v1"]}}}}))
    with pytest.raises(ValueError, match="ungranted"):
        load_selected_skills(["review"], granted_tools=[], project="pimc", registry_path=registry)
    selected = load_selected_skills(["review@2.0.0"], granted_tools=["code.repo_reader"], project="pimc", registry_path=registry)
    assert selected.manifest["skills"][0]["content_sha256"] == hashlib.sha256(instructions.read_bytes()).hexdigest()
    assert "protected interfaces" in selected.context
    assert skill_acceptance_errors(selected, output_schema="proposal.v1", observations=[])
    with pytest.raises(ValueError, match="version unavailable"):
        load_selected_skills(["review@1.0.0"], granted_tools=["code.repo_reader"], project="pimc", registry_path=registry)
    instructions.write_text("A changed real procedure.")
    changed = load_selected_skills(["review"], granted_tools=["code.repo_reader"], project="pimc", registry_path=registry)
    assert changed.manifest["sha256"] != selected.manifest["sha256"]


def test_skill_cannot_escape_registry_or_add_unknown_checks(tmp_path: Path) -> None:
    registry = tmp_path / "skills.yaml"
    for entry in ({"instructions": "../external.md"}, {"instructions": "procedure.md", "acceptance": {"auto_approve": True}}):
        (tmp_path / "procedure.md").write_text("Local resource")
        registry.write_text(yaml.safe_dump({"version": 1, "skills": {"review": {"version": "1", **entry}}}))
        with pytest.raises(ValueError):
            load_selected_skills(["review"], granted_tools=[], project="pimc", registry_path=registry)


def test_runtime_resource_override_is_frozen_without_changing_source(tmp_path: Path) -> None:
    prompt = tmp_path / "prompts" / "method.md"
    prompt.parent.mkdir()
    prompt.write_text("Original procedure.")
    baseline = load_agent_runtime_resources("idea", resource_root=tmp_path)
    candidate = load_agent_runtime_resources("idea", resource_root=tmp_path,
        overrides={"prompts/method.md": "Candidate procedure."})
    assert "Candidate procedure." in candidate.context
    assert prompt.read_text() == "Original procedure."
    assert baseline.manifest["sha256"] != candidate.manifest["sha256"]
    with pytest.raises(ValueError, match="existing"):
        load_agent_runtime_resources("idea", resource_root=tmp_path, overrides={"../../other": "x"})


def test_context_compare_and_swap_does_not_overwrite_a_newer_edit(tmp_path: Path) -> None:
    path = tmp_path / "prompt.md"
    _write_context_atomic(path, "before")
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    _write_context_atomic(path, "after", expected_sha256="sha256:" + original_hash)
    with pytest.raises(ValueError, match="changed after evaluation"):
        _write_context_atomic(path, "stale writer", expected_sha256=original_hash)
    assert path.read_text() == "after"
    _write_context_atomic(path, "before", expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    assert path.read_text() == "before"


@pytest.mark.asyncio
async def test_real_agent_messages_use_trusted_resources_and_reject_resume_drift(tmp_path: Path) -> None:
    from app.agents.base import RunRequest
    from app.agents.experiment.agent import ExperimentAgent
    agent = ExperimentAgent()
    marker = "Verified evaluator resource: keep the held-out split frozen."
    request = RunRequest(project="synthetic_regression", user_request="Define a bounded comparison.",
        extra={"run_root": str(tmp_path / "trusted"), "invocation_id": "versioncheck"},
        runtime={"agent_resource_overrides": {"prompts/experiment_plan.md": marker}})
    context = await agent.build_context(request)
    messages = agent._messages_for_context(request, context, purpose="loop")
    assert any(marker in (message.content or "") for message in messages)
    assert agent.agent_brief in messages[0].content
    assert context.metadata["agent_resources"]["files"]
    request.runtime["agent_resource_overrides"] = {"prompts/experiment_plan.md": marker + " changed"}
    with pytest.raises(ValueError, match="resource.*changed"):
        await agent.build_context(request)

    untrusted = RunRequest(project="synthetic_regression", user_request="Define a bounded comparison.",
        extra={"run_root": str(tmp_path / "untrusted"),
               "agent_resource_overrides": {"prompts/experiment_plan.md": "Untrusted override must not apply."}})
    user_context = await agent.build_context(untrusted)
    user_messages = agent._messages_for_context(untrusted, user_context, purpose="loop")
    assert all("Untrusted override must not apply." not in (message.content or "") for message in user_messages)
