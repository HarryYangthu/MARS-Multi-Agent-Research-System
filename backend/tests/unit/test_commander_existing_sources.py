"""Pure provenance fixtures and real repository reading/task creation."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml

from app.bridge.commander_existing_sources import existing_user_sources, pending_existing_request
from app.bridge.commander_session import ChatMessage, CommanderSession
from app.bridge.commander_tools import ToolContext, execute_tool
from app.bridge.orchestrator import Orchestrator
from app.harness.project_workspace import open_folder
from app.harness.runtime.event_bus import InProcessEventBus
from app.settings import reset_settings_cache
from app.storage.run_store import RunStore


def _user(session: CommanderSession, content: str) -> ChatMessage:
    message = ChatMessage(role="user", content=content)
    session.active_turn_id = message.id
    session.add(message)
    return message


def _observe(session: CommanderSession, tool: str, args: dict[str, Any], result: dict[str, Any]) -> None:
    session.add(ChatMessage(role="tool", content=json.dumps(result, ensure_ascii=False),
                           tool_name=tool, tool_args=args, tool_result=result))


def _archived_failed_shape() -> tuple[CommanderSession, ChatMessage, dict[str, Any]]:
    # Reduced protocol shape of the user's retained 1-epoch incident. These
    # are pure provenance inputs, not a replacement executor/model response.
    session = CommanderSession(conv_id="unfinished-existing", project="fixture")
    original = _user(session, "启动一次基线的仿真，你只跑一个 epoch 就行，其他全部正常。")
    args = {"model_name": "ExistingModel", "config_path": "configs/model.yaml",
            "max_iters": 1, "budget_unit": "epochs", "start": False}
    _observe(session, "run.existing_model", args, {"ok": False, "status": "model_not_resolved", "candidates": []})
    return session, original, args


def test_immediate_retry_uses_exact_failed_args_and_source_without_mutating_history() -> None:
    session, original, args = _archived_failed_shape()
    retry = _user(session, "继续")
    before = [message.to_dict() for message in session.messages]
    pending = pending_existing_request(session)
    assert pending == {"args": args, "source_turn_id": original.id,
        "source_messages": [{"id": original.id, "content": original.content}, {"id": retry.id, "content": retry.content}]}
    assert existing_user_sources(session) == pending["source_messages"]
    pending["args"]["max_iters"] = 999
    assert session.messages[1].tool_args == args and [message.to_dict() for message in session.messages] == before


@pytest.mark.parametrize("boundary", ["independent", "candidate", "retained_run", "linked_run", "other_tool", "empty_turn"])
def test_unsafe_or_unrelated_turns_cannot_supply_old_budget(boundary: str) -> None:
    session, _, args = _archived_failed_shape()
    middle = _user(session, "继续")
    if boundary == "independent":
        middle.content = "换个任务，模型运行十步"
    elif boundary == "candidate":
        _observe(session, "run.existing_model", args,
            {"ok": False, "status": "model_selection_required", "candidates": [{"config_path": "configs/model.yaml"}]})
    elif boundary == "retained_run":
        _observe(session, "run.create", {}, {"ok": False, "run_id": "retained-original"})
    elif boundary == "linked_run":
        session.linked_run_id = "existing-linked"
    elif boundary == "other_tool":
        _observe(session, "routing.repository_check", {}, {"ok": True})
    latest = _user(session, "重试")
    assert pending_existing_request(session) is None
    assert existing_user_sources(session) == [{"id": latest.id, "content": latest.content}]


@pytest.mark.parametrize("change", [
    {"max_iters": 2}, {"budget_unit": "steps"}, {"seed": 2026},
    {"config_path": "configs/another.yaml"}, {"model_name": "DifferentModel"}, {"max_iters": True},
])
def test_unexecuted_generic_creation_cannot_replace_original_parameters(change: dict[str, Any]) -> None:
    session, _, args = _archived_failed_shape()
    _user(session, "继续")
    job = {"config_path": args["config_path"], "budget_unit": args["budget_unit"], "max_iters": args["max_iters"], **change}
    _observe(session, "create_and_start_run", {"entrypoint": "coding", "existing_execution_job": job},
        {"ok": False, "status": "repository_inspection_required"})
    latest = _user(session, "继续")
    assert pending_existing_request(session) is None
    assert existing_user_sources(session) == [{"id": latest.id, "content": latest.content}]


@contextmanager
def _environment(values: dict[str, str]) -> Iterator[None]:
    saved = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    reset_settings_cache()
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_settings_cache()


@pytest.mark.asyncio
@pytest.mark.parametrize("generic_tool", [None, "run.create", "create_and_start_run"])
async def test_actual_failed_admission_then_read_only_continue_then_retry_creates_original_one_epoch(
    tmp_path: Path, generic_tool: str | None,
) -> None:
    with _environment({"MARS_FOLDER_PROJECTS_REGISTRY": str(tmp_path / "registry.json"),
        "MARS_EXECUTION_CONFIG_PATH": str(tmp_path / "execution.yaml"),
        "MARS_EXECUTION_BACKEND": "local_command", "MARS_RUNTIME_MODE": "development"}):
        project = open_folder(str(tmp_path / "project"), create=True)
        source = project.root / "code/existing"
        (source / "configs").mkdir(parents=True)
        (source / "configs/model.yaml").write_text("model_name: ExistingModel\n")
        (source / "configs/base.yaml").write_text("batch_size: 32\n")
        (source / "train.py").write_text("def current_entrypoint():\n    return None\n")
        (project.metadata_root / "repo_link.yaml").write_text(yaml.safe_dump({
            "repo_path": str(source), "allowed_paths": ["configs/", "train.py"], "read_only": True}))
        registration = {"id": "current-entry", "project_ids": [project.name], "entrypoints": ["train.py"],
            "config_paths": ["configs/unrelated.yaml"], "seed_policy": "generate_if_unset", "seed_defaults": ["configs/base.yaml"]}
        execution = tmp_path / "execution.yaml"
        execution.write_text(yaml.safe_dump({"execution": {"backend": "local_command", "local_commands": [registration]}}))
        store = RunStore(tmp_path / "runs", project_storage=True)
        session = CommanderSession(conv_id="actual-continue-chain", project=project.name)
        ctx = ToolContext(Orchestrator(run_store=store, bus=InProcessEventBus()), session, store)
        original = _user(session, "启动一次基线的仿真，你只跑一个 epoch 就行，其他全部正常。")
        args = {"config_path": "configs/model.yaml", "model_name": "ExistingModel",
                "max_iters": 1, "budget_unit": "epochs", "start": False}
        failed = await execute_tool("run.existing_model", args, ctx)
        assert failed["status"] == "execution_registration_required", failed
        _observe(session, "run.existing_model", args, failed)
        assert not store.list() and session.linked_run_id is None

        continuation = _user(session, "继续")
        for tool, tool_args in (("code.repo_list", {"path": "configs"}),
                                ("code.repo_read_lines", {"path": "configs/model.yaml"})):
            read = await execute_tool(tool, tool_args, ctx)
            assert read["ok"], read
            _observe(session, tool, tool_args, read)
        assert read["output"]["sha256"] == hashlib.sha256((source / "configs/model.yaml").read_bytes()).hexdigest()
        session.add(ChatMessage(role="assistant", content="配置已核实，现在按普通 Coding→Execution 路径创建任务。"))
        retry = _user(session, "重试")
        source_users = [original, continuation, retry]
        if generic_tool is not None:
            generic_args = {"entrypoint": "coding", "user_request": original.content,
                "existing_execution_job": {key: args[key] for key in ("config_path", "max_iters", "budget_unit")}}
            generic_failed = await execute_tool(generic_tool, generic_args, ctx)
            assert generic_failed["status"] == "repository_inspection_required" and generic_failed["ok"] is False
            assert not generic_failed.get("run_id") and not store.list()
            _observe(session, generic_tool, generic_args, generic_failed)
            source_users.append(_user(session, "继续"))
        pending = pending_existing_request(session)
        assert pending is not None and pending["args"] == args
        assert [item["id"] for item in pending["source_messages"]] == [item.id for item in source_users]

        # New registration is explicit; the resumed admission rereads actual
        # current files and resolves a current policy, rather than reusing the
        # prior inspection as validation or relaxing the registration scope.
        registration["config_paths"] = ["configs/model.yaml"]
        execution.write_text(yaml.safe_dump({"execution": {"backend": "local_command", "local_commands": [registration]}}))
        reset_settings_cache()
        (source / "configs/base.yaml").write_text("seed: 71\n")
        created = await execute_tool("run.existing_model", pending["args"], ctx)
        assert created["ok"] and created["status"] == "created", created
        run = store.get(created["run_id"])
        assert run is not None
        record = json.loads((run.root / "context/existing_model_request.json").read_text())
        assert record["source_messages"] == pending["source_messages"]
        assert record["requested"] == {"config_path": "configs/model.yaml", "entrypoint": "train.py",
            "max_iters": 1, "budget_unit": "epochs", "seed": 71}
        assert record["seed_source"] == "config_default"
        assert record["default_seed"] == 71 and not record["training_started"]
        default_read = next(item for item in record["evidence"] if item["path"] == "configs/base.yaml")
        assert default_read["sha256"] == hashlib.sha256((source / "configs/base.yaml").read_bytes()).hexdigest()
        assert len(store.list()) == 1 and not list(run.subdir("execution").iterdir())
        assert pending_existing_request(session) is None
