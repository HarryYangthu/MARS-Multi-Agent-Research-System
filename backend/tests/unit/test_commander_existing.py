"""Current bounded source/configuration admission; no model/process substitutes."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.bridge.commander_existing import (
    MAX_CONFIGURATIONS, MAX_SOURCE_BYTES, existing_execution_stage_limit, existing_model_turn_reply,
    resolve_existing_request, user_requests_additional_changes, validate_existing_user_intent,
)
from app.bridge.commander_session import ChatMessage, CommanderSession
from app.bridge.commander_tools import ToolContext, _existing_user_sources, execute_tool
from app.bridge.orchestrator import Orchestrator
from app.harness.project_workspace import open_folder
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.tools.config import tool_config
from app.harness.tools.project_repo import ProjectRepo
from app.settings import reset_settings_cache
from app.storage.run_store import RunStore


def _repository(root: Path, *, project: str = "bounded") -> ProjectRepo:
    (root / "configs").mkdir(parents=True)
    (root / "configs/first.yaml").write_text("model_name: FirstModel7L\nseed: 2026\n", encoding="utf-8")
    (root / "configs/second.yaml").write_text("model_name: SecondModel7L\nseed: 2027\n", encoding="utf-8")
    (root / "train.py").write_text("def forward(value):\n    return value\n", encoding="utf-8")
    return ProjectRepo(project, root, "local_path", True, ("",), (), (".git/",))


def _registrations(project: str) -> list[dict[str, Any]]:
    return [{"id": "actual-declared-entry", "project_ids": [project],
             "entrypoints": ["train.py"], "config_paths": ["configs/*.yaml"]}]


def _args(**changes: Any) -> dict[str, Any]:
    return {"model_name": "FirstModel7L", "max_iters": 200, "budget_unit": "steps", **changes}


def test_existing_model_reads_exact_current_sources_and_five_field_intent(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    result = resolve_existing_request(repo, _registrations(repo.project), _args())
    assert result["ok"] and result["requested"] == {
        "config_path": "configs/first.yaml", "entrypoint": "train.py", "seed": 2026,
        "max_iters": 200, "budget_unit": "steps"}
    assert result["training_started"] is False and result["preflight_passed"] is False
    assert result["ordinary_coding"] is False
    assert {row["path"] for row in result["evidence"]} == {"configs/first.yaml", "train.py"}
    for row in result["evidence"]:
        assert row["sha256"] == hashlib.sha256((repo.root / row["path"]).read_bytes()).hexdigest()


def test_model_abbreviation_returns_actual_choices_without_guessing(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    result = resolve_existing_request(repo, _registrations(repo.project), _args(model_name="7L"))
    assert result["ok"] is False and result["status"] == "model_selection_required"
    assert {r["config_path"] for r in result["candidates"]} == {"configs/first.yaml", "configs/second.yaml"}
    assert "requested" not in result
    (tmp_path / "README.md").write_text("There is AnotherModel7L here.")
    absent = resolve_existing_request(repo, _registrations(repo.project), _args(model_name="AnotherModel"))
    assert absent["status"] == "model_not_resolved" and not absent["candidates"]
    assert "不证明模型不存在" in absent["error"]


def test_explicit_configuration_avoids_unrelated_tree_and_catalog_limit(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    outside = tmp_path / "unrelated_measurements"
    outside.mkdir()
    for i in range(300):
        (outside / f"measurement-{i}.json").write_text("{}")
    for i in range(MAX_CONFIGURATIONS + 1):
        (tmp_path / f"configs/variant-{i}.yaml").write_text(f"model_name: Variant{i}\nseed: 2026\n")
    with pytest.raises(ValueError, match="明确配置路径"):
        resolve_existing_request(repo, _registrations(repo.project), _args())
    explicit = resolve_existing_request(repo, _registrations(repo.project),
        _args(model_name="", config_path="configs/first.yaml"), user_texts=["configs/first.yaml运行200步"])
    assert explicit["ok"] and len(explicit["evidence"]) == 2


@pytest.mark.parametrize("text,changes", [
    ("运行FirstModel7L 200steps", {"model_name": "SecondModel7L"}),
    ("运行configs/first.yaml 200steps", {"model_name": "", "config_path": "configs/second.yaml"}),
    ("configs/first.yaml仿真200步", {"model_name": "", "config_path": "configs/second.yaml"}),
    ("运行first.yaml 200steps", {"model_name": "", "config_path": "configs/second.yaml"}),
])
def test_explicit_user_model_or_configuration_cannot_be_replaced_by_tool_extraction(
    tmp_path: Path, text: str, changes: dict[str, Any],
) -> None:
    repo = _repository(tmp_path)
    before = {path.name: path.read_bytes() for path in (tmp_path / "configs").iterdir()}
    with pytest.raises(ValueError, match="明确指定"):
        resolve_existing_request(repo, _registrations(repo.project), _args(**changes), user_texts=[text])
    assert {path.name: path.read_bytes() for path in (tmp_path / "configs").iterdir()} == before


def test_semantic_alias_without_literal_conflict_is_not_falsely_rejected(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    result = resolve_existing_request(repo, _registrations(repo.project), _args(), user_texts=["运行七层短核模型200步"])
    assert result["ok"] and result["requested"]["config_path"] == "configs/first.yaml"


@pytest.mark.parametrize("changes", [
    {"config_path": "../outside.yaml"}, {"config_path": "C:\\external\\config.yaml"},
    {"config_path": "/tmp/external.yaml"}, {"max_iters": True}, {"max_iters": "200"},
    {"max_iters": 0}, {"budget_unit": ["steps"]}, {"budget_unit": "batches"},
    {"seed": False}, {"seed": -1}, {"command_id": "guessed"}, {"project": "other"},
    {"output_dir": "old/results"}, {"user_request": "replace the actual user goal"},
    {"start": "true"}, {"parameter_overrides": []},
    {"stop_after_execution": "true"},
])
def test_untrusted_fields_and_budget_types_are_rejected(tmp_path: Path, changes: dict[str, Any]) -> None:
    repo = _repository(tmp_path)
    with pytest.raises(ValueError):
        resolve_existing_request(repo, _registrations(repo.project), _args(**changes))


def test_no_whole_tree_patterns_or_cross_project_capabilities(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    assert not resolve_existing_request(repo, _registrations("another-project"), _args())["ok"]
    registrations = _registrations(repo.project)
    registrations[0]["config_paths"] = ["**/*.yaml"]
    assert resolve_existing_request(repo, registrations, _args())["status"] == "capability_unavailable"


def test_symlink_and_large_source_do_not_enter_quick_admission(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    original = tmp_path / "configs/first.yaml"
    actual = tmp_path / "actual.yaml"
    original.rename(actual)
    original.symlink_to(actual)
    with pytest.raises(ValueError, match="不可"):
        resolve_existing_request(repo, _registrations(repo.project), _args(config_path="configs/first.yaml"))
    original.unlink()
    original.write_bytes(b"#" * (MAX_SOURCE_BYTES + 1))
    with pytest.raises(ValueError, match="不可"):
        resolve_existing_request(repo, _registrations(repo.project), _args(config_path="configs/first.yaml"))


@pytest.mark.parametrize("text,unit,count,seed", [
    ("运行 FirstModel7L，200 步，seed=2026", "steps", 200, 2026),
    ("跑100个完整epoch，种子设为2027", "epochs", 100, 2027),
    ("Run 20 optimizer updates, seed: 2026", "steps", 20, 2026),
    ("max_steps=25", "steps", 25, 2026),
    ("训练3轮", "epochs", 3, 2026),
])
def test_budget_and_seed_are_bound_to_actual_user_text(
    tmp_path: Path, text: str, unit: str, count: int, seed: int,
) -> None:
    repo = _repository(tmp_path)
    result = resolve_existing_request(repo, _registrations(repo.project),
        _args(budget_unit=unit, max_iters=count, seed=seed))
    validate_existing_user_intent(result, [text])


@pytest.mark.parametrize("text,args", [
    ("运行200 epochs", _args()),
    ("运行200步", _args(max_iters=20)),
    ("运行200步，seed2027", _args(seed=2026)),
    ("运行200步", _args(seed=2027)),
    ("仿真200次", _args()),
    ("运行模型", _args()),
    ("200steps还是200epochs？", _args()),
    ("运行-200步", _args()),
])
def test_rewritten_or_ambiguous_budget_cannot_create_an_execution_intent(
    tmp_path: Path, text: str, args: dict[str, Any],
) -> None:
    repo = _repository(tmp_path)
    result = resolve_existing_request(repo, _registrations(repo.project), args)
    with pytest.raises(ValueError):
        validate_existing_user_intent(result, [text])


def test_explicit_current_defaults_are_available_without_old_run_budget(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (tmp_path / "configs/first.yaml").write_text(
        "model_name: FirstModel7L\nseed: 2026\nbudget_unit: steps\nmax_iters: 200\n")
    result = resolve_existing_request(repo, _registrations(repo.project), _args())
    validate_existing_user_intent(result, ["运行FirstModel7L，沿用当前配置的明确预算"])
    validate_existing_user_intent(result, ["200步7L", "FirstModel7L"])
    changed = resolve_existing_request(repo, _registrations(repo.project), _args(budget_unit="epochs", max_iters=100))
    validate_existing_user_intent(changed, ["200步7L", "FirstModel7L，这次改跑100epochs"])


def test_unhandled_parameters_and_source_changes_retain_ordinary_coding(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    result = resolve_existing_request(repo, _registrations(repo.project), _args(parameter_overrides={"lr_init": 0.001}))
    assert result["ordinary_coding"] and result["parameter_overrides"] == {"lr_init": 0.001}
    assert (tmp_path / "configs/first.yaml").read_text() == "model_name: FirstModel7L\nseed: 2026\n"
    assert user_requests_additional_changes(["把第二层抽头增加5，跑200步"])
    assert user_requests_additional_changes(["把学习率改为0.001，跑200步"])
    assert not user_requests_additional_changes(["跑200步，不改模型、科学配置和训练策略"])
    assert not user_requests_additional_changes(["FirstModel7L 跑100epochs"])


@pytest.mark.parametrize("text,ordinary", [
    ("lr_init=1e-4，跑20steps", True), ("batch_size=64，跑20steps", True),
    ("optimizer=SGD，跑20steps", True), ("loss=L1，跑20steps", True),
    ("model.inner_kernel: 11，跑20steps", True), ("学习率不变，seed=2026，跑20steps", False),
    ("model_name=FirstModel7L，max_steps=20，seed=2026", False),
])
def test_explicit_scientific_assignments_cannot_disappear_when_model_omits_overrides(
    text: str, ordinary: bool,
) -> None:
    assert user_requests_additional_changes([text]) is ordinary


@pytest.mark.parametrize("text", [
    "仿真后停在结果，不生成报告", "运行后不需要报告", "停在执行结果", "不要写报告", "no report",
])
def test_execution_only_boundary_requires_and_honors_actual_user_request(text: str) -> None:
    assert existing_execution_stage_limit({"stop_after_execution": True}, [text])
    # An omitted model field must not erase an explicit user's output boundary.
    assert existing_execution_stage_limit({}, [text])
    with pytest.raises(ValueError, match="停止阶段"):
        existing_execution_stage_limit({"stop_after_execution": False}, [text])


@pytest.mark.parametrize("text", ["运行200步", "运行并生成报告", "不要停在结果，需要报告"])
def test_report_flow_remains_default_unless_user_requests_execution_boundary(text: str) -> None:
    assert existing_execution_stage_limit({}, [text]) is False
    assert existing_execution_stage_limit({"stop_after_execution": False}, [text]) is False
    with pytest.raises(ValueError, match="停止阶段"):
        existing_execution_stage_limit({"stop_after_execution": True}, [text])


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
async def test_actual_tool_creates_coding_graph_and_receipt_without_starting_training(tmp_path: Path) -> None:
    # Exercise the shipped registry policy, not the unconfigured direct-handler
    # compatibility branch. A missing capability must fail this acceptance.
    assert tool_config("run.existing_model").bridge_only
    with _environment({"MARS_FOLDER_PROJECTS_REGISTRY": str(tmp_path / "registry.json"),
                       "MARS_EXECUTION_CONFIG_PATH": str(tmp_path / "execution.yaml"),
                       "MARS_EXECUTION_BACKEND": "local_command", "MARS_RUNTIME_MODE": "development"}):
        project = open_folder(str(tmp_path / "project"), create=True)
        repo = _repository(project.root / "code/existing", project=project.name)
        (project.metadata_root / "repo_link.yaml").write_text(yaml.safe_dump({
            "repo_path": str(repo.root), "allowed_paths": ["configs/*.yaml", "train.py"], "read_only": True}))
        (tmp_path / "execution.yaml").write_text(yaml.safe_dump({"execution": {
            "backend": "local_command", "local_commands": _registrations(project.name)}}))
        store = RunStore(tmp_path / "runs", project_storage=True)
        orchestrator = Orchestrator(run_store=store, bus=InProcessEventBus())
        session = CommanderSession(conv_id="actual-current-request", project=project.name)
        user = ChatMessage(role="user", content="运行7L，200步；先只准备，不启动仿真")
        session.active_turn_id = user.id
        session.add(user)
        context = ToolContext(orchestrator, session, store)
        choice_args = _args(model_name="7L", start=False)
        choice = await execute_tool("run.existing_model", choice_args, context)
        assert choice.get("status") == "model_selection_required", choice
        session.add(ChatMessage(role="tool", content=json.dumps(choice), tool_name="run.existing_model",
                               tool_args=choice_args, tool_result=choice))
        selected = ChatMessage(role="user", content="FirstModel7L")
        session.active_turn_id = selected.id
        session.add(selected)
        result = await execute_tool("run.existing_model", _args(start=False), context)
        assert result["ok"], result
        run_id = str(result["run_id"])
        actual = orchestrator.session(run_id)
        assert result["status"] == "created" and actual.request.entrypoint == "coding"
        assert {key: state.value for key, state in actual.graph.all_states().items()} == {
            "idea": "skipped", "experiment": "skipped", "coding": "pending", "execution": "pending", "writing": "pending"}
        assert actual.request.extra["existing_execution_job"]["max_iters"] == 200
        assert actual.request.extra.get("stop_after") is None
        assert result["stop_after_execution"] is False
        record = json.loads((actual.run.root / "context/existing_model_request.json").read_text())
        assert record["source_messages"] == [{"id": user.id, "content": user.content},
                                             {"id": selected.id, "content": selected.content}]
        assert record["preflight_passed"] is False and record["training_started"] is False
        assert existing_model_turn_reply("run.existing_model", result)
        assert existing_model_turn_reply("run.create", result) is None
        assert existing_model_turn_reply("run.existing_model", {**result, "ok": False}) is None
        assert not list(actual.run.subdir("execution").iterdir())

        mismatch = ChatMessage(role="user", content="FirstModel7L运行200epochs")
        session.active_turn_id = mismatch.id
        session.add(mismatch)
        before = len(store.list())
        rejected = await execute_tool("run.existing_model", _args(start=False), context)
        assert not rejected["ok"] and rejected["status"] == "user_intent_confirmation_required"
        assert len(store.list()) == before

        source_hash = hashlib.sha256((repo.root / "configs/first.yaml").read_bytes()).hexdigest()
        for message, extra in (
            ("把学习率改为0.001，FirstModel7L运行200步", {"parameter_overrides": {"lr_init": 0.001}}),
            ("把第二层抽头增加5，FirstModel7L运行200步", {}),
            ("FirstModel7L lr_init=1e-4，运行200steps", {}),
            ("FirstModel7L batch_size=64，运行200steps", {}),
            ("FirstModel7L optimizer=SGD，运行200steps", {}),
            ("FirstModel7L loss=L1，运行200steps", {}),
        ):
            change = ChatMessage(role="user", content=message)
            session.active_turn_id = change.id
            session.add(change)
            ordinary = await execute_tool("run.existing_model", _args(start=False, **extra), context)
            assert ordinary["ok"] and ordinary["ordinary_coding"] is True
            changed_run = orchestrator.session(str(ordinary["run_id"]))
            assert changed_run.request.entrypoint == "coding"
            assert "existing_execution_job" not in changed_run.request.extra
            assert message in changed_run.request.user_request
        assert hashlib.sha256((repo.root / "configs/first.yaml").read_bytes()).hexdigest() == source_hash

        for supplied in ({"stop_after_execution": True}, {}):
            stop_user = ChatMessage(role="user", content="FirstModel7L运行200步，仿真后停在结果，不生成报告")
            session.active_turn_id = stop_user.id
            session.add(stop_user)
            stopped = await execute_tool("run.existing_model", _args(start=False, **supplied), context)
            assert stopped["ok"], stopped
            stopped_run = orchestrator.session(str(stopped["run_id"]))
            assert stopped_run.request.extra["stop_after"] == "execution"
            assert stopped["stop_after_execution"] is True
            assert "不生成报告" in str(existing_model_turn_reply("run.existing_model", stopped))
            persisted = json.loads((stopped_run.run.root / "run_state.json").read_text())
            assert persisted["request"]["extra"]["stop_after"] == "execution"
            assert not list(stopped_run.run.subdir("execution").iterdir())

        for message, wrong in (
            ("运行FirstModel7L 200steps", {"model_name": "SecondModel7L"}),
            ("运行configs/first.yaml 200steps", {"model_name": "", "config_path": "configs/second.yaml"}),
            ("运行first.yaml 200steps", {"model_name": "", "config_path": "configs/second.yaml"}),
        ):
            exact_user = ChatMessage(role="user", content=message)
            session.active_turn_id = exact_user.id
            session.add(exact_user)
            before = len(store.list())
            wrong_run = await execute_tool("run.existing_model", _args(start=False, **wrong), context)
            assert not wrong_run["ok"] and "明确指定" in wrong_run["error"]
            assert len(store.list()) == before


def test_only_actual_immediately_pending_selection_can_supply_previous_user_budget() -> None:
    session = CommanderSession(conv_id="pending-source", project="pimc")
    first = ChatMessage(role="user", content="200步7L")
    session.active_turn_id = first.id
    session.add(first)
    second = ChatMessage(role="user", content="FirstModel7L")
    session.active_turn_id = second.id
    session.add(second)
    assert _existing_user_sources(session) == [{"id": second.id, "content": second.content}]
