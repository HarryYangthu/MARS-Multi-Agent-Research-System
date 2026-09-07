"""Real files and pure role-runner contracts; no model or service substitutes."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.harness.llm.model_registry import get_agent_config
from scripts.run_idea_roles_live import (
    ROLE_NAMES, RoleScenario, exception_record, require_clean_source,
    role_config, role_messages, run, source_snapshot,
)


def test_role_inputs_are_isolated_and_original_prompt_is_preserved() -> None:
    prompt = 'Existing prompt\r\n{"hypotheses": "array of objects"}\n原始字段'
    messages = role_messages("reflection", prompt)
    assert [(message.role, message.content) for message in messages] == [
        ("system", "reflection"), ("user", prompt),
    ]
    assert all(not message.tool_calls and message.tool_call_id is None for message in messages)
    scenario = RoleScenario.model_validate({
        "project": "pimc", "question": "A caller-authored component test request",
        "model": {"name": "shared-model-label", "thinking": False, "max_retries": 0,
                  "reasoning_effort": "high"},
        "roles": {"reflection": {"name": "review-model-label", "thinking": True,
                                 "reasoning_effort": None}},
        "discovery": {"max_pairwise_matches": 2},
    })
    original = get_agent_config("idea")
    generation = role_config(original, scenario, "generation")
    reflection = role_config(original, scenario, "reflection")
    assert generation.model_name == "shared-model-label" and not generation.thinking_enabled
    assert reflection.model_name == "review-model-label" and reflection.thinking_enabled
    assert generation.reasoning_effort == "high" and reflection.reasoning_effort is None
    assert generation.max_retries == reflection.max_retries == 0
    assert not generation.tools and not reflection.tools
    assert original == get_agent_config("idea")
    assert scenario.discovery.initial_hypotheses == 3 and scenario.discovery.evolution_rounds == 1
    assert scenario.discovery.max_pairwise_matches == 2 and scenario.discovery.top_k == 1


def test_role_configuration_rejects_secret_fields_and_unknown_roles() -> None:
    for roles in ({"reflection": {"api_key": "DO_NOT_PERSIST_REJECTED_VALUE"}},
                  {"not_a_role": {"name": "model"}}):
        with pytest.raises(ValidationError) as caught:
            RoleScenario.model_validate({"project": "pimc", "question": "Input validation", "roles": roles})
        assert "DO_NOT_PERSIST_REJECTED_VALUE" not in json.dumps(exception_record(caught.value))


def test_live_preflight_detects_both_tracked_and_untracked_source(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    git("init")
    source_file = tmp_path / "component.py"
    source_file.write_text("# Human-authored source for a git preflight check\n", encoding="utf-8")
    git("add", "component.py")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "source contract")
    initial = source_snapshot(tmp_path)
    require_clean_source(initial)
    assert len(initial["source_commit"]) == len(initial["source_tree"]) == 40
    source_file.write_text("# Uncommitted source change\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="no model request"):
        require_clean_source(source_snapshot(tmp_path))
    source_file.write_text("# Human-authored source for a git preflight check\n", encoding="utf-8")
    (tmp_path / "untracked.py").write_text("# Untracked source\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="no model request"):
        require_clean_source(source_snapshot(tmp_path))


@pytest.mark.asyncio
async def test_prepare_only_saves_inputs_without_provider_or_candidate(tmp_path: Path) -> None:
    source = tmp_path / "scenario.yaml"
    raw = ('schema: idea_roles_scenario.v1\r\nproject: pimc\r\n'
           'question: "调用者撰写的组件测试任务"\r\n'
           'evidence_refs: ["caller-supplied-reference-label"]\r\n'
           'constraints: ["No measured improvement is supplied"]\r\n'
           'model: {provider: local_vllm, name: unavailable-component-test-model}\r\n')
    source.write_bytes(raw.encode("utf-8"))
    runs_root = tmp_path / "runs"
    result = await run(argparse.Namespace(scenario=source, runs_root=runs_root,
                                         prepare_only=True, max_seconds=1.0))
    assert result == 0
    root = next(runs_root.iterdir())
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    request = json.loads((root / "input" / "request.json").read_text(encoding="utf-8"))
    assert summary["status"] == "prepared" and summary["component_only"] and summary["no_new_research"]
    assert summary["model_requests"] == summary["model_responses"] == summary["research_tools_executed"] == 0
    assert not any(summary[key] for key in ("scientific_validated", "end_to_end_passed",
                                           "downstream_delivered", "simulation_executed", "automatic_selection"))
    assert request["context"]["evidence_refs"] == ["caller-supplied-reference-label"]
    assert set(request["roles"]) == set(ROLE_NAMES)
    assert (root / "input" / "scenario.yaml").read_bytes() == source.read_bytes()
    assert not (root / "role_calls").exists() and not (root / "idea").exists()


@pytest.mark.asyncio
async def test_invalid_scenario_leaves_failure_summary_without_rejected_values(tmp_path: Path) -> None:
    source = tmp_path / "scenario.yaml"
    source.write_text('project: pimc\nquestion: Input validation\n'
                      'model: {api_key: DO_NOT_PERSIST_REJECTED_VALUE}\n', encoding="utf-8")
    runs_root = tmp_path / "runs"
    result = await run(argparse.Namespace(scenario=source, runs_root=runs_root,
                                         prepare_only=True, max_seconds=1.0))
    assert result == 2
    root = next(runs_root.iterdir())
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "failed" and summary["exception"]["error_type"] == "ValidationError"
    assert summary["model_requests"] == 0
    assert all("DO_NOT_PERSIST_REJECTED_VALUE" not in path.read_text(encoding="utf-8")
               for path in root.rglob("*") if path.is_file())


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [0.0, float("inf"), float("nan")])
async def test_nonfinite_or_unbounded_deadline_is_rejected(tmp_path: Path, seconds: float) -> None:
    with pytest.raises(ValueError, match="positive and finite"):
        await run(argparse.Namespace(scenario=tmp_path / "missing.yaml", runs_root=tmp_path / "runs",
                                     prepare_only=True, max_seconds=seconds))
    assert not (tmp_path / "runs").exists()
