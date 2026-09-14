"""Real project folders, snapshots, context messages and repository isolation."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from app.agents.base import RunRequest
from app.agents.idea.focused_agent import FocusedIdeaAgent
from app.harness.agent_loop.trace import digest
from app.harness.context.folder_context import discover_folder_context, load_folder_context
from app.harness.project_workspace import open_folder, folder_project, project_root
from app.harness.tools.code import repo_reader_tool
from app.harness.tools.project_repo import load_project_repo, resolve_allowed_path
from app.harness.tools.registry import ToolContext
from app.harness.tools.search import local_docs_tool
from app.settings import reset_settings_cache


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    # Only selects an isolated real on-disk registry; no provider/tool substitutes.
    path = tmp_path / "state/projects.json"
    monkeypatch.setenv("MARS_FOLDER_PROJECTS_REGISTRY", str(path))
    reset_settings_cache()
    yield path
    reset_settings_cache()


def test_create_and_reopen_folder_has_stable_identity(tmp_path: Path, registry: Path) -> None:
    project = open_folder(str(tmp_path / "新项目"), create=True)
    assert project.root.is_dir() and (project.root / "README.md").is_file()
    assert (project.root / "AGENTS.md").is_file()
    assert project_root(project.name) == project.root / ".mars"
    assert open_folder(str(project.root)).name == project.name
    assert len(json.loads(registry.read_text())) == 1
    assert load_project_repo(project.name).root == project.root
    with pytest.raises(FileExistsError):
        open_folder(str(project.root), create=True)


def test_open_existing_does_not_overwrite_background_or_code(tmp_path: Path, registry: Path) -> None:
    root = tmp_path / "existing"
    root.mkdir()
    (root / "README.md").write_text("original notes")
    (root / "model.py").write_text("x = 1\n")
    before = (root / "README.md").read_bytes(), (root / "model.py").read_bytes()
    open_folder(str(root))
    assert before == ((root / "README.md").read_bytes(), (root / "model.py").read_bytes())
    assert not (root / "AGENTS.md").exists()


def test_context_auto_discovers_nested_markdown_excludes_secrets_and_escapes(tmp_path: Path, registry: Path) -> None:
    project = open_folder(str(tmp_path / "project"), create=True)
    (project.root / "context/notes").mkdir()
    (project.root / "context/notes/method.md").write_text("domain knowledge")
    (project.root / "context/.secret.md").write_text("do not load")
    (project.root / ".env").write_text("SECRET=value")
    outside = tmp_path / "outside.md"
    outside.write_text("outside private notes")
    (project.root / "context/link.md").symlink_to(outside)
    record = discover_folder_context(project)
    assert {f["path"] for f in record["files"]} == {"README.md", "AGENTS.md", "context/notes/method.md"}
    assert record["warnings"] and "outside private notes" not in str(record)
    with pytest.raises(ValueError, match="escapes"):
        resolve_allowed_path(load_project_repo(project.name), "context/link.md", require_exists=True)
    with pytest.raises(ValueError, match="ignored"):
        resolve_allowed_path(load_project_repo(project.name), ".env", require_exists=True)


def test_same_run_is_frozen_new_run_gets_updated_context(tmp_path: Path, registry: Path) -> None:
    project = open_folder(str(tmp_path / "project"), create=True)
    readme = project.root / "README.md"
    readme.write_text("version one\n" * 2000)
    first = load_folder_context(project.name, tmp_path / "run1")
    readme.write_text("version two")
    assert load_folder_context(project.name, tmp_path / "run1") == first
    second = load_folder_context(project.name, tmp_path / "run2")
    assert first and second and first["sha256"] != second["sha256"]
    assert next(f for f in first["files"] if f["path"] == "README.md")["content"].endswith("version one\n")
    snapshot = tmp_path / "run1/input/folder_context.v1.json"
    corrupt = json.loads(snapshot.read_text()); corrupt["files"][0]["content"] = "changed"
    snapshot.write_text(json.dumps(corrupt))
    with pytest.raises(ValueError, match="snapshot is invalid"):
        load_folder_context(project.name, tmp_path / "run1")


def test_folder_move_keeps_identity_and_duplicate_copy_is_rejected(tmp_path: Path, registry: Path) -> None:
    project = open_folder(str(tmp_path / "original"), create=True)
    moved = tmp_path / "moved"
    project.root.rename(moved)
    assert open_folder(str(moved)).name == project.name
    reopened = folder_project(project.name)
    assert reopened is not None and reopened.root == moved
    copied = tmp_path / "copied/.mars"
    copied.mkdir(parents=True)
    (copied / "project.yaml").write_bytes((moved / ".mars/project.yaml").read_bytes())
    with pytest.raises(ValueError, match="另一个文件夹"):
        open_folder(str(copied.parent))


def test_invalid_paths_and_metadata_links_fail_closed(tmp_path: Path, registry: Path) -> None:
    with pytest.raises(ValueError, match="完整路径"):
        open_folder("relative")
    with pytest.raises(ValueError, match="invalid project"):
        project_root("../outside")
    root = tmp_path / "linked"; root.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    (root / ".mars").symlink_to(outside)
    with pytest.raises(ValueError, match="真实目录"):
        open_folder(str(root))
    assert not (outside / "project.yaml").exists()


def test_oversize_context_is_rejected_instead_of_truncated(tmp_path: Path, registry: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = open_folder(str(tmp_path / "project"), create=True)
    (project.root / "context/long.md").write_text("x" * 1500)
    monkeypatch.setenv("MARS_FOLDER_CONTEXT_MAX_CHARS", "1000")
    reset_settings_cache()
    with pytest.raises(ValueError, match="不会静默截断"):
        discover_folder_context(project)
    assert not (tmp_path / "run/input/folder_context.v1.json").exists()


@pytest.mark.asyncio
async def test_author_reviewer_and_code_tools_use_only_selected_folder(tmp_path: Path, registry: Path) -> None:
    a = open_folder(str(tmp_path / "alpha"), create=True)
    b = open_folder(str(tmp_path / "beta"), create=True)
    (a.root / "README.md").write_text("ALPHA_CONTEXT_END" * 1400)
    (b.root / "README.md").write_text("BETA_ONLY_CONTEXT")
    (a.root / "baseline.py").write_text("alpha_value = 17\n")
    (b.root / "baseline.py").write_text("beta_value = 29\n")
    agent = FocusedIdeaAgent()
    run_root = tmp_path / "run"
    request = RunRequest(project=a.name, user_request="Use this project", extra={"run_root": str(run_root)})
    context = await agent.build_context(request)
    messages = agent._messages_for_context(request, context, purpose="draft")
    rendered = "\n".join(message.content for message in messages)
    assert rendered.count("ALPHA_CONTEXT_END") == 1400 and "BETA_ONLY_CONTEXT" not in rendered
    review = "\n".join(message.content for message in agent.review_messages(request, context))
    assert review.count("ALPHA_CONTEXT_END") == 1400 and "BETA_ONLY_CONTEXT" not in review
    assert "PIMC知识" not in review
    from app.bridge.commander import _system_prompt
    from app.bridge.commander_session import CommanderSession
    commander_prompt = _system_prompt(CommanderSession(conv_id="folder-context", project=a.name))
    assert commander_prompt.count("ALPHA_CONTEXT_END") == 1400 and "BETA_ONLY_CONTEXT" not in commander_prompt
    ctx = ToolContext("actual-files", a.name, "idea", extra={"run_root": str(run_root)})
    code = await repo_reader_tool({"path": "baseline.py"}, ctx)
    assert code.ok and code.output["content"] == "alpha_value = 17\n"
    docs = await local_docs_tool({"query": "ALPHA"}, ctx)
    assert docs.ok and len(docs.output["hits"]) == 1
    assert "BETA_ONLY_CONTEXT" not in str(docs.output)
    snapshot = load_folder_context(a.name, run_root)
    assert snapshot and snapshot["sha256"] == digest({k: v for k, v in snapshot.items() if k != "sha256"})


def test_context_config_cannot_select_outside_files(tmp_path: Path, registry: Path) -> None:
    project = open_folder(str(tmp_path / "project"), create=True)
    path = project.metadata_root / "project.yaml"
    cfg = yaml.safe_load(path.read_text()); cfg["context_files"] = ["../outside.md"]
    path.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError, match="项目文件夹内"):
        discover_folder_context(project)


@pytest.mark.asyncio
async def test_task_creation_freezes_context_before_agent_starts(tmp_path: Path, registry: Path) -> None:
    from app.bridge.orchestrator import Orchestrator, RunRequest as SessionRequest
    from app.storage.run_store import RunStore

    project = open_folder(str(tmp_path / "project"), create=True)
    (project.root / "README.md").write_text("CONTEXT_AT_CREATION")
    orchestrator = Orchestrator(run_store=RunStore(tmp_path / "runs"))
    session = orchestrator.create_session(SessionRequest(task="freeze", project=project.name, entrypoint="idea", standalone=True))
    (project.root / "README.md").write_text("CHANGED_AFTER_CREATION")
    recovered = Orchestrator(run_store=RunStore(tmp_path / "runs")).session(session.run.run_id)
    context = await FocusedIdeaAgent().build_context(RunRequest(
        project=recovered.request.project, user_request="Inspect this project", extra={"run_root": str(recovered.run.root)}))
    assert "CONTEXT_AT_CREATION" in context.project
    assert "CHANGED_AFTER_CREATION" not in context.project
    from app.harness.context.project_layer import build_project_layer
    manifest_context = build_project_layer(project=project.name, run_root=session.run.root).render()
    assert "CONTEXT_AT_CREATION" in manifest_context and "CHANGED_AFTER_CREATION" not in manifest_context


def test_same_names_are_distinct_and_missing_folder_does_not_break_list(tmp_path: Path, registry: Path) -> None:
    from app.harness.project_workspace import list_folder_projects

    (tmp_path / "one").mkdir(); (tmp_path / "two").mkdir()
    one = open_folder(str(tmp_path / "one/research"), create=True)
    two = open_folder(str(tmp_path / "two/research"), create=True)
    assert one.name != two.name and one.display_name == two.display_name
    one.root.rename(tmp_path / "moved")
    projects = list_folder_projects()
    assert len(projects) == 2
    assert next(p for p in projects if p.name == one.name).error
    assert not next(p for p in projects if p.name == two.name).error


def test_new_projects_keep_baseline_protection_without_pimc_signature(tmp_path: Path, registry: Path) -> None:
    from app.harness.gates.baseline_compatibility import static_check

    project = open_folder(str(tmp_path / "project"), create=True)
    result = static_check(project=project.name, tool_name="code.write_file",
                          args={"path": "model.py", "content": "def forward(self, x, y): pass"})
    assert not result.blocking
    result = static_check(project=project.name, tool_name="code.write_file",
                          args={"path": "baseline/model.py", "content": "x = 1"})
    assert result.blocking
