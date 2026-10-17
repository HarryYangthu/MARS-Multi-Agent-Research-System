"""Project storage uses real folders, real Git and real dataset files."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from collections.abc import Iterator

import numpy as np
import pytest
import yaml

from app.bridge.commander_session import CommanderSessionStore, ChatMessage
from app.bridge.project_onboarding import bind_code_folder, save_model_references
from app.harness.project_workspace import open_folder
from app.harness.project_storage import storage_roots
from app.settings import reset_settings_cache
from app.storage.data_source_store import DataSourceStore
from app.storage.run_store import RunStore


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    # Environment selects a real isolated registry, not a substitute executor.
    target = tmp_path / "registry.json"
    monkeypatch.setenv("MARS_FOLDER_PROJECTS_REGISTRY", str(target))
    reset_settings_cache()
    yield target
    reset_settings_cache()


def test_project_run_files_reload_trash_restore_and_legacy(tmp_path: Path, registry: Path) -> None:
    folder = open_folder(str(tmp_path / "Test7"), create=True)
    assert all((folder.root / name).is_dir() for name in ("context", "code", "data", "runs", "conversations"))
    store = RunStore(tmp_path / "global-runs", project_storage=True)
    run = store.create(task="same task", project=folder.name, user_request="actual request")
    assert run.root.parent == folder.root / "runs"
    assert (run.root / "input/user_request.md").read_text() == "actual request"
    assert not (store.runs_root / run.run_id).exists()
    # A real command's output stays under the actual run, with no model substitute.
    import subprocess
    import sys
    target = run.root / "execution/result.txt"
    subprocess.run([sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).write_text(str(sum(range(100))))", str(target)], check=True)
    assert target.read_text() == "4950"
    legacy = RunStore(store.runs_root).create(task="legacy", project=folder.name)
    reloaded = RunStore(store.runs_root, project_storage=True)
    retained = reloaded.get(run.run_id)
    assert retained is not None and retained.root == run.root
    assert {row.run_id for row in reloaded.list()} == {run.run_id, legacy.run_id}
    trashed = reloaded.trash(run.run_id)
    assert trashed.root == folder.root / "runs/.trash" / run.run_id
    assert reloaded.get(run.run_id) is None
    retained_trash = reloaded.get_trashed(run.run_id)
    assert retained_trash is not None and retained_trash.root == trashed.root
    restored = reloaded.restore(run.run_id)
    assert restored.root == run.root and (restored.root / "execution/result.txt").read_text() == "4950"


def test_two_project_runs_collisions_and_reopen_after_move(tmp_path: Path, registry: Path) -> None:
    a = open_folder(str(tmp_path / "A"), create=True)
    b = open_folder(str(tmp_path / "B"), create=True)
    store = RunStore(tmp_path / "global", project_storage=True)
    stamp = datetime(2026, 10, 11, tzinfo=timezone.utc)
    ra = store.create(task="same", project=a.name, now=stamp)
    rb = store.create(task="same", project=b.name, now=stamp)
    assert ra.run_id != rb.run_id and ra.root.parent != rb.root.parent
    moved = tmp_path / "A moved"
    a.root.rename(moved)
    assert open_folder(str(moved)).name == a.name
    relocated = RunStore(store.runs_root, project_storage=True).get(ra.run_id)
    assert relocated is not None and relocated.root == moved / "runs" / ra.run_id


def test_actual_dataset_and_default_survive_project_move(tmp_path: Path, registry: Path) -> None:
    folder = open_folder(str(tmp_path / "data-project"), create=True)
    store = DataSourceStore(tmp_path / "global-data", project_storage=True)
    identifier, target = store.allocate(original_name="signal.npz", project=folder.name)
    target = target.parent / "nested" / target.name
    target.parent.mkdir()
    np.savez(target, iq=np.arange(1024, dtype=np.float64))
    store.profile_uploaded_file(source_id=identifier, path=target, project=folder.name, original_name=target.name, fs_mhz=100.0)
    store.set_default(project=folder.name, source_id=identifier)
    assert target.is_relative_to(folder.root / "data")
    loaded = store.load(identifier)
    assert loaded is not None and loaded["stored_path"] == str(target)
    default = store.default_profile(folder.name)
    assert default is not None and default["id"] == identifier
    profile = json.loads(store.profile_path(identifier).read_text())
    assert profile["stored_path"] == "nested/" + target.name
    moved = tmp_path / "data moved"
    folder.root.rename(moved)
    open_folder(str(moved))
    reopened = DataSourceStore(store.base, project_storage=True)
    reopened_default = reopened.default_profile(folder.name)
    assert reopened_default is not None and Path(reopened_default["stored_path"]).is_file()
    assert len(reopened.list(project=folder.name)) == 1


def test_conversation_reload_trace_directory_and_legacy(tmp_path: Path, registry: Path) -> None:
    folder = open_folder(str(tmp_path / "dialogue"), create=True)
    # Select a real storage root while retaining project routing.
    sessions = CommanderSessionStore(tmp_path / "legacy", project_storage=True)
    current = sessions.create(project=folder.name)
    current.add(ChatMessage(role="user", content="research input"))
    sessions.persist(current)
    directory = sessions.session_directory(current)
    assert directory.parent == folder.root / "conversations"
    (directory / "trace.txt").write_text("real trace storage")
    legacy = CommanderSessionStore(tmp_path / "legacy").create(project=folder.name)
    fresh = CommanderSessionStore(tmp_path / "legacy", project_storage=True)
    restored_conversation = fresh.get(current.conv_id)
    assert restored_conversation is not None and restored_conversation.messages[0].content == "research input"
    assert fresh.session_directory(restored_conversation) == directory
    assert {item.conv_id for item in fresh.list()} == {current.conv_id, legacy.conv_id}
    with pytest.raises(ValueError):
        fresh.get("../outside")


def test_code_import_preserves_source_and_rules_and_omits_credentials(tmp_path: Path, registry: Path) -> None:
    folder = open_folder(str(tmp_path / "research"), create=True)
    source = tmp_path / "original"
    source.mkdir()
    (source / "model.py").write_text("VALUE = 7\n")
    (source / ".env").write_text("SECRET=excluded")
    (source / "runs").mkdir()
    (source / "runs/old.txt").write_text("old generated result")
    imported = bind_code_folder(folder, str(source))
    assert imported.is_relative_to(folder.root / "code") and (imported / ".git").is_dir()
    assert not (imported / ".env").exists() and not (imported / "runs").exists()
    assert (source / "model.py").read_bytes() == (imported / "model.py").read_bytes()
    assert not (source / ".git").exists()
    link = yaml.safe_load((folder.metadata_root / "repo_link.yaml").read_text())
    assert not Path(link["repo_path"]).is_absolute() and link["read_only"] is True
    save_model_references(folder, [{"provider": "configured", "model": "configured", "api_key_env": "MY_API_KEY", "api_key": "must-not-write"}])
    saved = (folder.metadata_root / "model_connections.yaml").read_text()
    assert "MY_API_KEY" in saved and "must-not-write" not in saved
    assert list((folder.metadata_root / "code-imports").glob("*.yaml"))


def test_symbolic_directories_cannot_redirect_project_writes(tmp_path: Path, registry: Path) -> None:
    folder = open_folder(str(tmp_path / "protected"), create=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (folder.root / "runs").rmdir()
    (folder.root / "runs").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        RunStore(tmp_path / "global", project_storage=True).create(task="denied", project=folder.name)
    assert not list(outside.iterdir())


def test_repository_input_data_is_imported_and_config_rebased(tmp_path: Path, registry: Path) -> None:
    folder = open_folder(str(tmp_path / "configured-project"), create=True)
    source = tmp_path / "scientific-code"
    (source / "data").mkdir(parents=True)
    (source / "configs").mkdir()
    np.save(source / "data/signal.npy", np.arange(10))
    (source / "data/datasets").mkdir()
    (source / "data/datasets/nested.txt").write_text("nested input data")
    (source / "model.py").write_text("VALUE = 1")
    (source / "configs/train.yaml").write_text("data:\n  path: data/signal.npy\nlr: 0.0001\n")
    before = (source / "configs/train.yaml").read_bytes()
    imported = bind_code_folder(folder, str(source))
    configuration = yaml.safe_load((imported / "configs/train.yaml").read_text())
    actual_data = (imported / configuration["data"]["path"]).resolve()
    assert actual_data.is_relative_to(folder.root / "data")
    assert actual_data.read_bytes() == (source / "data/signal.npy").read_bytes()
    assert (actual_data.parent / "datasets/nested.txt").read_text() == "nested input data"
    receipt = yaml.safe_load(next((folder.metadata_root / "code-imports").glob("*.yaml")).read_text())
    assert len(receipt["data_files"]) == 2
    assert configuration["lr"] == 0.0001
    assert (source / "configs/train.yaml").read_bytes() == before


def test_history_import_is_idempotent_with_no_duplicate_entries(tmp_path: Path, registry: Path) -> None:
    from app.bridge.project_onboarding import import_existing_workspace
    # Select real application workspace resources via existing settings root.
    folder = open_folder(str(tmp_path / "existing-project"), create=True)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    legacy = RunStore(runtime / "runs")
    run = legacy.create(task="recorded-result", project=folder.name)
    (run.root / "run_state.json").write_text('{"status":"failed"}')
    (run.root / "execution/measured.txt").write_text("recorded data")
    session = CommanderSessionStore(runtime / "conversations").create(project=folder.name)
    result = import_existing_workspace(folder, legacy_root=runtime)
    assert result["runs"] == result["conversations"] == 1
    assert run.root.is_dir() and (runtime / "conversations" / session.conv_id).is_dir()
    assert (folder.root / "runs" / run.run_id / "execution/measured.txt").read_text() == "recorded data"
    routed = RunStore(legacy.runs_root, project_storage=True)
    routed_run = routed.get(run.run_id)
    assert routed_run is not None and routed_run.root == folder.root / "runs" / run.run_id
    assert len(routed.list()) == 1
    routed.trash(run.run_id)
    assert routed.get(run.run_id) is None and not routed.list()
    assert len(routed.list_trashed()) == 1
    routed.restore(run.run_id)
    assert len(routed.list()) == 1 and not routed.list_trashed()
    # Re-importing history must not replace a newer project-local selection.
    local_default = folder.root / "data/_defaults.json"
    local_default.write_text(json.dumps({folder.name: "newer_selection"}))
    assert import_existing_workspace(folder, legacy_root=runtime)["runs"] == 0
    assert json.loads(local_default.read_text())[folder.name] == "newer_selection"
    routed.trash(run.run_id)
    routed.delete_trashed(run.run_id)
    assert routed.get(run.run_id) is None and not routed.list()
    assert routed.get_trashed(run.run_id) is None and not routed.list_trashed()
    assert import_existing_workspace(folder, legacy_root=runtime)["runs"] == 0
    assert run.root.exists()  # Original evidence is retained without reviving deleted tasks.


def test_external_declared_initialization_checkpoint_is_retained(tmp_path: Path, registry: Path) -> None:
    folder = open_folder(str(tmp_path / "model-project"), create=True)
    source = tmp_path / "model-source"
    (source / "configs").mkdir(parents=True)
    (source / "model.py").write_text("VALUE = 1")
    checkpoint = tmp_path / "initialization.pth"
    checkpoint.write_bytes(b"real owned binary fixture, not a claimed training result")
    (source / "configs/train.yaml").write_text(yaml.safe_dump({"init_checkpoint": str(checkpoint), "lr": 0.0001}))
    imported = bind_code_folder(folder, str(source))
    config = yaml.safe_load((imported / "configs/train.yaml").read_text())
    retained = (imported / config["init_checkpoint"]).resolve()
    assert retained.is_relative_to(folder.root / "data/checkpoints")
    assert retained.read_bytes() == checkpoint.read_bytes()
    assert checkpoint.is_file() and config["lr"] == 0.0001


def test_execution_adapters_use_persisted_project_run_paths(tmp_path: Path, registry: Path) -> None:
    from app.bridge.code_workspace_resolver import PersistedCodeWorkspaceResolver, PersistedWorkspaceResolutionError
    from app.bridge.extension_runtime import _project_run_artifact_root
    from app.execution.adapters.base import AdapterRequest, AdapterAction
    from app.execution.remote import RemoteExecutor, RemoteExecutorConfig, RemoteProjectAdapter
    folder = open_folder(str(tmp_path / "execution-project"), create=True)
    store = RunStore(tmp_path / "global-runs", project_storage=True)
    run = store.create(task="artifact storage", project=folder.name)
    request = AdapterRequest(action=AdapterAction.EXECUTE, request_id="path-check", project=folder.name,
                             run_id=run.run_id, candidate_id="not_materialized", output_dir=str(run.root / "execution"))
    resolver = PersistedCodeWorkspaceResolver(store.runs_root, project_storage=True)
    assert resolver._resolve_sync(request) is None
    assert _project_run_artifact_root(request) == run.root
    # Exercise path binding with the actual client object; do not contact SSH
    # or replace a real executor with a simulated response.
    adapter = RemoteProjectAdapter(name="path-check", client=RemoteExecutor(RemoteExecutorConfig()),
        trusted_adapter_argv=("python3", "-m", "approved_adapter"), artifact_root=store.runs_root,
        artifact_root_resolver=_project_run_artifact_root)
    assert adapter._fetch_destination(request, "job-path-check") == run.root / "execution/remote/job-path-check"
    with pytest.raises(ValueError):
        resolver._resolve_sync(request.model_copy(update={"project": "another_project"}))
    with pytest.raises(ValueError):
        _project_run_artifact_root(request.model_copy(update={"project": "another_project"}))
    with pytest.raises(RuntimeError):
        adapter._fetch_destination(request.model_copy(update={"output_dir": str(tmp_path / "outside")}), "job-path-check")
