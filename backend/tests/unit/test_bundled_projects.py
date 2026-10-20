"""Real archive/filesystem checks: no model, tool substitute or agent execution."""
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from app.bridge.bundled_projects import bootstrap_bundled_projects, restore_bundled_project
from app.bridge.commander_errors import CommanderModelResponseError, conversation_failure, require_native_decision
from app.harness.project_workspace import list_folder_projects
from app.bridge.commander_session import CommanderSessionStore

IDENTITY = "folder_1234567890abcdef1234567890abcdef"


def bundle(root: Path, *, unsafe: str | None = None) -> dict[str, str]:
    directory = root / "bundled-projects"
    directory.mkdir(parents=True)
    archive = directory / "test7.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("test7/.mars/project.yaml", "schema: folder_project.v1\nname: " + IDENTITY + "\ndisplay_name: test7\n")
        z.writestr("test7/conversations/retained.txt", "真实文件系统往返校验")
        if unsafe:
            z.writestr(unsafe, "must not write outside the project")
    entry = {"folder": "test7", "archive": "test7.zip", "project_id": IDENTITY,
             "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
    (directory / "manifest.json").write_text(json.dumps({"schema": "mars.bundled_projects.v1", "projects": [entry]}))
    return entry


def test_bootstrap_registers_history_and_restart_preserves_user_changes(tmp_path: Path) -> None:
    bundle(tmp_path)
    registry = tmp_path / "workspace/folder_projects.json"
    restored = bootstrap_bundled_projects(tmp_path, registry=registry)
    assert len(restored) == 1
    retained = restored[0] / "conversations/retained.txt"
    assert retained.read_text() == "真实文件系统往返校验"
    retained.write_text("用户后续写入")
    assert bootstrap_bundled_projects(tmp_path, registry=registry) == restored
    assert retained.read_text() == "用户后续写入"
    assert list_folder_projects(registry=registry)[0].name == IDENTITY


def test_changed_archive_is_not_extracted_or_registered(tmp_path: Path) -> None:
    entry = bundle(tmp_path)
    (tmp_path / "bundled-projects/test7.zip").write_bytes(b"corrupted archive")
    with pytest.raises(ValueError, match="checksum"):
        restore_bundled_project(tmp_path, entry, registry=tmp_path / "registry.json")
    assert not (tmp_path / "workspace/projects/test7").exists()
    assert not (tmp_path / "registry.json").exists()


@pytest.mark.parametrize("unsafe", ["../outside.txt", "test7/../../outside.txt", "test7/C:/outside.txt"])
def test_unsafe_archive_never_becomes_a_project(tmp_path: Path, unsafe: str) -> None:
    entry = bundle(tmp_path, unsafe=unsafe)
    with pytest.raises(ValueError, match="Unsafe"):
        restore_bundled_project(tmp_path, entry, registry=tmp_path / "registry.json")
    assert not (tmp_path / "workspace/projects/test7").exists()
    assert not (tmp_path / "outside.txt").exists()


def test_no_manifest_does_not_change_normal_installations(tmp_path: Path) -> None:
    assert bootstrap_bundled_projects(tmp_path) == []
    assert list(tmp_path.iterdir()) == []


def test_conversation_delete_and_restore_keep_actual_files(tmp_path: Path) -> None:
    store = CommanderSessionStore(tmp_path / "conversations")
    session = store.create(project="pimc")
    original = (tmp_path / "conversations" / session.conv_id / "session.json").read_bytes()
    store.trash(session.conv_id)
    assert store.get(session.conv_id) is None
    assert store.list() == []
    assert CommanderSessionStore(tmp_path / "conversations").list() == []
    restored = store.restore(session.conv_id)
    assert restored.conv_id == session.conv_id
    assert (tmp_path / "conversations" / session.conv_id / "session.json").read_bytes() == original


def test_model_failure_is_specific_without_claiming_an_action() -> None:
    with pytest.raises(CommanderModelResponseError) as caught:
        require_native_decision({"native_status": "unknown", "reason": "native_tool_call_missing"})
    failure = conversation_failure(caught.value)
    assert failure and failure[0] == "model_response_unavailable"
    assert "接口支持工具调用" in failure[1]
    assert conversation_failure(ValueError("zhipu provider requires API key"))[0] == "model_credentials_missing"
    assert conversation_failure(RuntimeError("provider 'zhipu' is not configured for commander"))[0] == "model_credentials_missing"
    assert conversation_failure(TimeoutError("private upstream detail"))[0] == "model_timeout"
    assert "private upstream detail" not in conversation_failure(TimeoutError("private upstream detail"))[1]
    require_native_decision({"native_status": "text", "reply": "普通回答"})
    require_native_decision({"native_status": "action", "actions": [{"tool": "run.status"}]})
