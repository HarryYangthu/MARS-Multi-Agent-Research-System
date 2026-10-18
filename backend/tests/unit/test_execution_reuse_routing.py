"""Explicit execution intent uses real source reads without a model substitute."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.bridge.commander_code import routing_source_evidence
from app.bridge.commander_session import ChatMessage, CommanderSession
from app.bridge.commander_tools import TOOLS, existing_execution_job_for_route
from app.harness.tools.code_inspection import read_code_fragment
from app.harness.tools.project_repo import ProjectRepo
from app.harness.tools.registry import ToolContext


def _intent(**updates: Any) -> dict[str, Any]:
    return {"config_path": "configs/existing.yaml", "entrypoint": "train.py",
            "max_iters": 200, "budget_unit": "steps", "seed": 2026, **updates}


def test_legacy_creation_keeps_ordinary_routing() -> None:
    assert existing_execution_job_for_route({"entrypoint": "coding"}, {}) is None
    assert existing_execution_job_for_route({"existing_execution_job": None}, {}) is None


@pytest.mark.parametrize("updates", [
    {"max_iters": True}, {"max_iters": 0}, {"max_iters": "200"},
    {"seed": False}, {"seed": -1}, {"seed": "2026"},
    {"budget_unit": "batches"}, {"budget_unit": ["steps"]},
    {"config_path": "../outside.yaml"}, {"config_path": "/tmp/input.yaml"},
    {"config_path": "C:\\project\\input.yaml"}, {"config_path": " configs/existing.yaml"},
    {"config_path": "."}, {"config_path": "configs/"},
    {"entrypoint": "..\\train.py"}, {"entrypoint": ""},
    {"entrypoint": "train.py\x00"},
    {"command_id": "guessed-command"}, {"data_path": "/some/old/data"},
])
def test_execution_intent_rejects_guessed_or_unsafe_fields(updates: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="existing_execution_job"):
        existing_execution_job_for_route({"entrypoint": "coding", "existing_execution_job": _intent(**updates)}, {})


@pytest.mark.parametrize("raw", ["configs/existing.yaml", [], {}, {"max_iters": 200}])
def test_incomplete_optional_shortcut_keeps_ordinary_coding(raw: Any) -> None:
    assert existing_execution_job_for_route({"entrypoint": "coding", "existing_execution_job": raw}, {}) is None


def test_unseeded_baseline_request_is_not_blocked_by_shortcut_shape() -> None:
    raw = _intent()
    del raw['seed']
    raw.update(config_path='configs/static.yaml', max_iters=1, budget_unit='epochs')
    assert existing_execution_job_for_route({'entrypoint': 'coding', 'existing_execution_job': raw},
        {'ok': True, 'repository_scope': 'required', 'evidence': [{'path': 'configs/static.yaml'}]}) is None
    assert 'seed' not in raw  # No seed or budget is invented for the shortcut.


@pytest.mark.asyncio
async def test_exact_configuration_read_is_current_and_bound(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    config = tmp_path / "configs/existing.yaml"
    config.write_text("model_name: ExistingModel\nseed: 2026\n", encoding="utf-8")
    repo = ProjectRepo("pimc", tmp_path, "local_path", True, ("",), (), ())
    session = CommanderSession(conv_id="reuse-route", project="pimc", active_turn_id="first")
    read = await read_code_fragment({"path": "configs/existing.yaml"},
        ToolContext("reuse-route", "pimc", "commander", project_repo_root=str(tmp_path)))
    assert read.ok
    session.add(ChatMessage(role="tool", content="Configuration inspected", tool_name="code.repo_read_lines",
                           tool_result={"ok": read.ok, "output": read.output}))

    def routing() -> dict[str, Any]:
        return {"ok": True, "repository_scope": "required", "evidence": routing_source_evidence(repo, session)}

    args = {"entrypoint": "coding", "existing_execution_job": _intent()}
    assert existing_execution_job_for_route(args, routing()) == _intent()
    windows_args = {"entrypoint": "coding", "existing_execution_job": _intent(config_path="configs\\existing.yaml")}
    assert existing_execution_job_for_route(windows_args, routing()) == _intent()
    assert existing_execution_job_for_route(
        {"entrypoint": "coding", "existing_execution_job": _intent(config_path="configs/other.yaml")}, routing()) is None
    for entry in ("pipeline", "idea", "experiment", "execution", "writing"):
        assert existing_execution_job_for_route({**args, "entrypoint": entry}, routing()) is None
    assert existing_execution_job_for_route({**args, "standalone": True}, routing()) is None
    assert existing_execution_job_for_route({**args, "seed_artifact": "existing seed"}, routing()) is None
    assert existing_execution_job_for_route(args, {**routing(), "ok": False}) is None
    assert existing_execution_job_for_route(args, {**routing(), "repository_scope": "not_applicable"}) is None
    config.write_text("model_name: ChangedModel\nseed: 2026\n", encoding="utf-8")
    assert existing_execution_job_for_route(args, routing()) is None
    config.write_text("model_name: ExistingModel\nseed: 2026\n", encoding="utf-8")
    session.active_turn_id = "second"
    assert existing_execution_job_for_route(args, routing()) is None


def test_both_creation_tools_expose_explicit_execution_intent() -> None:
    for name in ("run.create", "create_and_start_run"):
        tool = TOOLS[name]
        description = tool.parameters["existing_execution_job"]
        for field in ("config_path", "entrypoint", "max_iters", "budget_unit", "seed"):
            assert field in description
        assert "command_id" in description
        assert "Coding" in tool.description
