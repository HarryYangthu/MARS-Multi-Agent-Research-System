"""Actual configuration and task creation checks; no model/process substitute."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml

from app.bridge.commander_existing import (
    materialize_existing_seed, resolve_existing_request, validate_existing_user_intent,
)
from app.bridge.commander_session import ChatMessage, CommanderSession
from app.bridge.commander_tools import ToolContext, _existing_user_sources, execute_tool
from app.bridge.orchestrator import Orchestrator
from app.harness.project_workspace import open_folder
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.tools.project_repo import ProjectRepo
from app.settings import reset_settings_cache
from app.storage.run_store import RunStore


def _repository(root: Path, project: str = "seed-policy") -> ProjectRepo:
    (root / "configs").mkdir(parents=True)
    (root / "configs/model.yaml").write_text("model_name: ExistingModel\n", encoding="utf-8")
    (root / "configs/base.yaml").write_text("batch_size: 32\n", encoding="utf-8")
    (root / "train.py").write_text("def declared_entrypoint():\n    return None\n", encoding="utf-8")
    return ProjectRepo(project, root, "local_path", True, ("",), (), (".git/",))


def _registrations(project: str, **extra: Any) -> list[dict[str, Any]]:
    return [{"id": "current-single-entry", "project_ids": [project], "entrypoints": ["train.py"],
             "config_paths": ["configs/model.yaml"], **extra}]


def _args(**extra: Any) -> dict[str, Any]:
    return {"config_path": "configs/model.yaml", "budget_unit": "epochs", "max_iters": 1, **extra}


def _policy(project: str) -> list[dict[str, Any]]:
    return _registrations(project, seed_policy="generate_if_unset", seed_defaults=["configs/base.yaml"])


@pytest.mark.parametrize("no_profiles", [False, True])
def test_explicit_existing_unregistered_configuration_reports_registration_not_missing_model(
    tmp_path: Path, no_profiles: bool,
) -> None:
    repo = _repository(tmp_path)
    registrations = [] if no_profiles else _registrations(repo.project, config_paths=["configs/other.yaml"])
    result = resolve_existing_request(repo, registrations, _args())
    assert result["ok"] is False and result["status"] == "execution_registration_required"
    assert result["config_path"] == "configs/model.yaml" and result["model_name"] == "ExistingModel"
    assert result["training_started"] is False and "requested" not in result
    evidence = result["evidence"]
    assert len(evidence) == 1 and evidence[0]["path"] == "configs/model.yaml"
    assert evidence[0]["sha256"] == hashlib.sha256((tmp_path / "configs/model.yaml").read_bytes()).hexdigest()
    with pytest.raises((OSError, ValueError)):
        resolve_existing_request(repo, registrations, _args(config_path="configs/missing.yaml"))


def test_declared_actual_defaults_are_read_in_loader_order_then_scenario_wins(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (tmp_path / "configs/base.yaml").write_text("seed: 11\n")
    (tmp_path / "configs/intermediate.yaml").write_text("seed: 22\n")
    registrations = _registrations(repo.project, seed_policy="generate_if_unset",
        seed_defaults=["configs/base.yaml", "configs/intermediate.yaml"])
    inherited = resolve_existing_request(repo, registrations, _args())
    assert inherited["requested"]["seed"] == inherited["default_seed"] == 22
    assert inherited["seed_source"] == "config_default" and not inherited["seed_generation_required"]
    assert inherited["seed_provenance"]["merge_order"] == [
        "configs/base.yaml", "configs/intermediate.yaml", "configs/model.yaml"]
    defaults = [row for row in inherited["evidence"] if row["purpose"] == "seed_defaults"]
    assert [row["path"] for row in defaults] == ["configs/base.yaml", "configs/intermediate.yaml"]
    for row in defaults:
        assert row["sha256"] == hashlib.sha256((tmp_path / row["path"]).read_bytes()).hexdigest()
    validate_existing_user_intent(inherited, ["ExistingModel跑一个epoch就行"])
    (tmp_path / "configs/model.yaml").write_text("model_name: ExistingModel\nseed: 33\n")
    scenario = resolve_existing_request(repo, registrations, _args())
    assert scenario["requested"]["seed"] == scenario["default_seed"] == 33
    assert scenario["seed_source"] == "config_default"
    explicit = resolve_existing_request(repo, registrations, _args(seed=44))
    validate_existing_user_intent(explicit, ["ExistingModel跑一个epoch，seed=44"])
    assert explicit["seed_source"] == "user_request" and explicit["requested"]["seed"] == 44
    with pytest.raises(ValueError, match="种子"):
        validate_existing_user_intent(explicit, ["ExistingModel跑一个epoch"])


def test_policy_reads_defaults_before_allocating_only_a_new_tasks_seed(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    resolved = resolve_existing_request(repo, _policy(repo.project), _args())
    assert resolved["requested"]["seed"] is None and resolved["seed_generation_required"] is True
    assert resolved["ordinary_coding"] is False and resolved["default_seed"] is None
    validate_existing_user_intent(resolved, ["你只跑一个 epoch 就行"])
    generated = materialize_existing_seed(resolved)
    assert type(generated["requested"]["seed"]) is int
    assert 0 <= generated["requested"]["seed"] <= 0xFFFFFFFF
    assert generated["seed_source"] == "generated_for_run" and not generated["seed_generation_required"]
    assert resolved["requested"]["seed"] is None  # No allocation/mutation in the resolver.
    assert materialize_existing_seed(generated) == generated  # Never regenerate frozen values.
    validate_existing_user_intent(generated, ["你只跑一个 epoch 就行"])
    invented = resolve_existing_request(repo, _policy(repo.project), _args(seed=2026))
    with pytest.raises(ValueError, match="seed|种子"):
        validate_existing_user_intent(invented, ["你只跑一个 epoch 就行"])
    with pytest.raises(ValueError, match="seed"):
        validate_existing_user_intent(resolved, ["只跑一个epoch，seed=29"])


@pytest.mark.parametrize("configured_seed", [None, 73])
def test_null_optional_seed_equals_omission_without_overriding_actual_user_seed(
    tmp_path: Path, configured_seed: int | None,
) -> None:
    repo = _repository(tmp_path)
    (tmp_path / "configs/base.yaml").write_text(yaml.safe_dump({"seed": configured_seed}))
    omitted = resolve_existing_request(repo, _policy(repo.project), _args())
    nullable = resolve_existing_request(repo, _policy(repo.project), _args(seed=None))
    assert nullable == omitted
    validate_existing_user_intent(nullable, ["你只跑一个epoch就行"])
    if configured_seed is not None:
        validate_existing_user_intent(nullable, ["跑一个epoch，seed=73"])
    with pytest.raises(ValueError, match="seed"):
        validate_existing_user_intent(nullable, ["跑一个epoch，seed=29"])


@pytest.mark.parametrize("extra", [{}, {"seed_policy": "generate_if_unset"},
    {"seed_defaults": ["configs/base.yaml"]}])
def test_no_seed_and_no_complete_generation_policy_preserves_ordinary_coding(
    tmp_path: Path, extra: dict[str, Any],
) -> None:
    repo = _repository(tmp_path)
    result = resolve_existing_request(repo, _registrations(repo.project, **extra), _args())
    validate_existing_user_intent(result, ["ExistingModel跑一轮"])
    assert result["ordinary_coding"] and result["requested"]["seed"] is None
    assert result["seed_source"] == "not_configured" and not result["seed_generation_required"]
    assert result["ordinary_coding_reason"] == "seed_default_unresolved"


def test_scenario_null_overrides_inherited_seed_and_empty_default_chain_is_explicit(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (tmp_path / "configs/base.yaml").write_text("seed: 9\n")
    (tmp_path / "configs/model.yaml").write_text("model_name: ExistingModel\nseed: null\n")
    result = resolve_existing_request(repo, _policy(repo.project), _args())
    assert result["default_seed"] is None and result["seed_generation_required"]
    empty = resolve_existing_request(repo,
        _registrations(repo.project, seed_policy="generate_if_unset", seed_defaults=[]), _args())
    assert empty["seed_generation_required"] and empty["seed_provenance"]["merge_order"] == ["configs/model.yaml"]


def test_declared_inherited_seed_needs_no_random_generation_policy(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (tmp_path / "configs/base.yaml").write_text("seed: 73\n")
    result = resolve_existing_request(repo,
        _registrations(repo.project, seed_defaults=["configs/base.yaml"]), _args())
    validate_existing_user_intent(result, ["你只跑一个epoch就行"])
    assert result["requested"]["seed"] == 73 and result["seed_source"] == "config_default"
    assert not result["ordinary_coding"] and not result["seed_generation_required"]


@pytest.mark.parametrize("path", ["configs/base.yaml", "configs/model.yaml"])
def test_malformed_actual_config_reports_invalid_not_generated_or_missing_model(tmp_path: Path, path: str) -> None:
    repo = _repository(tmp_path)
    (tmp_path / path).write_text("seed: [invalid\n")
    registrations = _policy(repo.project) if path.endswith("base.yaml") else []
    with pytest.raises(ValueError, match="解析"):
        resolve_existing_request(repo, registrations, _args())


@pytest.mark.parametrize("seed", [True, -1, "old-run-seed"])
def test_invalid_actual_seed_is_not_silently_replaced_by_generation(tmp_path: Path, seed: Any) -> None:
    repo = _repository(tmp_path)
    (tmp_path / "configs/base.yaml").write_text(yaml.safe_dump({"seed": seed}))
    with pytest.raises(ValueError, match="seed"):
        resolve_existing_request(repo, _policy(repo.project), _args())


@pytest.mark.parametrize("defaults", [["../base.yaml"], ["configs/*.yaml"], "configs/base.yaml", [None]])
def test_seed_defaults_cannot_expand_or_escape_declared_real_files(tmp_path: Path, defaults: Any) -> None:
    repo = _repository(tmp_path)
    with pytest.raises(ValueError):
        resolve_existing_request(repo, _registrations(repo.project,
            seed_policy="generate_if_unset", seed_defaults=defaults), _args())


def test_missing_symlink_or_disagreeing_defaults_never_generate_a_seed(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    base = tmp_path / "configs/base.yaml"
    base.unlink()
    with pytest.raises((OSError, ValueError)):
        resolve_existing_request(repo, _policy(repo.project), _args())
    other = tmp_path / "elsewhere.yaml"
    other.write_text("seed: 9\n")
    base.symlink_to(other)
    with pytest.raises(ValueError, match="符号链接"):
        resolve_existing_request(repo, _policy(repo.project), _args())
    base.unlink()
    base.write_text("seed: 9\n")
    disagreeing = _policy(repo.project) + _registrations(repo.project, seed_defaults=[])
    disagreeing[1]["id"] = "different-loader-policy"
    result = resolve_existing_request(repo, disagreeing, _args())
    assert result["ordinary_coding"] and result["seed_source"] == "not_configured"
    assert not result["seed_generation_required"] and result["seed_provenance"]["reason"] == "seed_registration_ambiguous"


@pytest.mark.parametrize("text,unit,count", [
    ("你只跑一个 epoch 就行", "epochs", 1), ("跑两轮训练", "epochs", 2),
    ("跑十步", "steps", 10), ("跑二十个完整epoch", "epochs", 20),
    ("一百epochs", "epochs", 100), ("一百零二步", "steps", 102),
    ("跑一千轮", "epochs", 1000), ("一万步", "steps", 10000),
])
def test_explicit_chinese_budget_counts_preserve_exact_unit(
    tmp_path: Path, text: str, unit: str, count: int,
) -> None:
    repo = _repository(tmp_path)
    resolved = resolve_existing_request(repo, _policy(repo.project), _args(budget_unit=unit, max_iters=count))
    validate_existing_user_intent(resolved, [text])
    wrong = resolve_existing_request(repo, _policy(repo.project), _args(
        budget_unit="steps" if unit == "epochs" else "epochs", max_iters=count))
    with pytest.raises(ValueError, match="单位"):
        validate_existing_user_intent(wrong, [text])


@pytest.mark.parametrize("text", ["跑一百二epochs", "跑一两轮", "跑负一轮", "跑1一轮", "一百二还是一百三轮？"])
def test_ambiguous_or_invalid_chinese_counts_do_not_synthesize_budget(tmp_path: Path, text: str) -> None:
    repo = _repository(tmp_path)
    result = resolve_existing_request(repo, _policy(repo.project), _args())
    with pytest.raises(ValueError, match="预算|单位"):
        validate_existing_user_intent(result, [text])


@pytest.mark.parametrize("status", ["model_not_resolved", "capability_unavailable", "execution_registration_required"])
@pytest.mark.parametrize("retry", ["继续", "重试", "好的，再试一次。"])
def test_only_explicit_retry_of_uncreated_request_retains_its_budget(status: str, retry: str) -> None:
    session = CommanderSession(conv_id="retry-uncreated", project="existing")
    first = ChatMessage(role="user", content="基线模型只跑一个epoch")
    session.active_turn_id = first.id
    session.add(first)
    session.add(ChatMessage(role="tool", content=status, tool_name="run.existing_model",
        tool_result={"ok": False, "status": status}))
    second = ChatMessage(role="user", content=retry)
    session.active_turn_id = second.id
    session.add(second)
    assert _existing_user_sources(session) == [{"id": first.id, "content": first.content},
                                               {"id": second.id, "content": second.content}]
    session.linked_run_id = "already-created"
    assert _existing_user_sources(session) == [{"id": second.id, "content": second.content}]
    session.linked_run_id = None
    independent = ChatMessage(role="user", content="换个模型，跑十步")
    session.active_turn_id = independent.id
    session.add(independent)
    assert _existing_user_sources(session) == [{"id": independent.id, "content": independent.content}]


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
@pytest.mark.parametrize("seed_args", [{}, {"seed": None}])
async def test_actual_creation_freezes_generated_seed_and_real_defaults_provenance(
    tmp_path: Path, seed_args: dict[str, Any],
) -> None:
    with _environment({"MARS_FOLDER_PROJECTS_REGISTRY": str(tmp_path / "registry.json"),
        "MARS_EXECUTION_CONFIG_PATH": str(tmp_path / "execution.yaml"),
        "MARS_EXECUTION_BACKEND": "local_command", "MARS_RUNTIME_MODE": "development"}):
        project = open_folder(str(tmp_path / "project"), create=True)
        repo = _repository(project.root / "code/existing", project.name)
        (project.metadata_root / "repo_link.yaml").write_text(yaml.safe_dump({
            "repo_path": str(repo.root), "allowed_paths": ["configs/*.yaml", "train.py"], "read_only": True}))
        execution_path = tmp_path / "execution.yaml"
        execution_path.write_text(yaml.safe_dump({"execution": {
            "backend": "local_command", "local_commands": _policy(project.name)}}))
        before = {path: path.read_bytes() for path in repo.root.rglob("*") if path.is_file()}
        store = RunStore(tmp_path / "runs", project_storage=True)
        orchestrator = Orchestrator(run_store=store, bus=InProcessEventBus())
        session = CommanderSession(conv_id="actual-unseeded-epoch", project=project.name)
        user = ChatMessage(role="user", content="ExistingModel，你只跑一个 epoch 就行")
        session.active_turn_id = user.id
        session.add(user)
        context = ToolContext(orchestrator, session, store)
        created = await execute_tool("run.existing_model", _args(start=False, **seed_args), context)
        assert created["ok"] and created["status"] == "created", created
        actual = orchestrator.session(str(created["run_id"]))
        record_path = actual.run.root / "context/existing_model_request.json"
        record_bytes = record_path.read_bytes()
        record = json.loads(record_bytes)
        seed = created["requested"]["seed"]
        assert type(seed) is int and 0 <= seed <= 0xFFFFFFFF
        assert created["seed_source"] == record["seed_source"] == "generated_for_run"
        assert actual.request.extra["existing_execution_job"] == {
            "entrypoint": "train.py", "config_path": "configs/model.yaml",
            "seed": seed, "budget_unit": "epochs", "max_iters": 1}
        assert record["requested"]["seed"] == actual.request.extra["existing_model_request"]["requested"]["seed"] == seed
        assert record["seed_generation_required"] is False and record["default_seed"] is None
        default_evidence = next(row for row in record["evidence"] if row["purpose"] == "seed_defaults")
        assert default_evidence["path"] == "configs/base.yaml"
        assert default_evidence["sha256"] == hashlib.sha256(before[repo.root / "configs/base.yaml"]).hexdigest()
        assert record["source_messages"] == [{"id": user.id, "content": user.content}]
        persisted = json.loads((actual.run.root / "run_state.json").read_text())
        assert persisted["request"]["extra"]["existing_execution_job"]["seed"] == seed
        assert actual.request.entrypoint == "coding" and actual.graph.state("idea").value == "skipped"
        assert not list(actual.run.subdir("execution").iterdir())
        assert {path: path.read_bytes() for path in before} == before

        retry = ChatMessage(role="user", content="继续")
        session.active_turn_id = retry.id
        session.add(retry)
        count_before_retry = len(store.list())
        recovery = await execute_tool("run.existing_model", _args(start=False), context)
        assert recovery["status"] == "existing_run_recovery_required" and recovery["run_id"] == actual.run.run_id
        assert len(store.list()) == count_before_retry

        # A new effective default affects only the next task, never old input.
        (repo.root / "configs/base.yaml").write_text("seed: 91\n")
        new_user = ChatMessage(role="user", content="ExistingModel跑一轮")
        session.active_turn_id = new_user.id
        session.add(new_user)
        newer = await execute_tool("run.existing_model", _args(start=False), context)
        assert newer["ok"] and newer["seed_source"] == "config_default", newer
        assert newer["requested"]["seed"] == 91
        assert record_path.read_bytes() == record_bytes
        assert actual.request.extra["existing_execution_job"]["seed"] == seed

        # Incomplete host policy retains ordinary Coding, with no five-field
        # shortcut capable of claiming an unknown inherited seed was checked.
        (repo.root / "configs/base.yaml").write_text("batch_size: 32\n")
        execution_path.write_text(yaml.safe_dump({"execution": {
            "backend": "local_command", "local_commands": _registrations(project.name)}}))
        reset_settings_cache()
        next_user = ChatMessage(role="user", content="ExistingModel再跑一个epoch")
        session.active_turn_id = next_user.id
        session.add(next_user)
        ordinary = await execute_tool("run.existing_model", _args(start=False), context)
        assert ordinary["ok"] and ordinary["ordinary_coding"], ordinary
        assert ordinary["seed_source"] == "not_configured" and ordinary["requested"]["seed"] is None
        ordinary_run = orchestrator.session(str(ordinary["run_id"]))
        assert "existing_execution_job" not in ordinary_run.request.extra
        assert ordinary_run.request.entrypoint == "coding"

        before_count = len(store.list())
        unregistered = await execute_tool("run.existing_model", _args(config_path="configs/base.yaml", start=False), context)
        assert unregistered["status"] == "execution_registration_required", unregistered
        assert len(store.list()) == before_count
