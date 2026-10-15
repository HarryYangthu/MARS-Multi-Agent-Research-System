"""Real checkout paths cannot be confused with their baseline name prefix."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
import json
from pathlib import Path
import sys

import pytest
import yaml

from app.harness.tools.execution.local_command import LocalCommandJob, _command
from app.harness.project_workspace import open_folder
from app.harness.tools.git_branch import GitBranch, bind_git_branch, git, prepare
from app.settings import repo_root, reset_settings_cache


@contextmanager
def _host_command(root: Path, argv: list[str]) -> Iterator[None]:
    tools = yaml.safe_load((repo_root() / "configs/tools.yaml").read_text(encoding="utf-8"))
    tools["tools"]["execution.simulation_runner"]["command_allowlist"] = [[sys.executable]]
    tools_path = root / "host-tools.yaml"
    tools_path.write_text(yaml.safe_dump(tools), encoding="utf-8")
    execution = root / "host-execution.yaml"
    execution.write_text(yaml.safe_dump({"execution": {
        "backend": "local_command", "local_commands": [{"id": "prefix-bound", "argv": argv}],
    }}), encoding="utf-8")
    configured = {"MARS_TOOLS_CONFIG_PATH": str(tools_path), "MARS_EXECUTION_CONFIG_PATH": str(execution),
                  "MARS_EXECUTION_BACKEND": "local_command"}
    saved = {name: os.environ.get(name) for name in configured}
    os.environ.update(configured)
    reset_settings_cache()
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        reset_settings_cache()


@pytest.fixture
def checkout(tmp_path: Path) -> GitBranch:
    source = tmp_path / "code"
    source.mkdir()
    git(source, "init", "-b", "main")
    (source / "runner.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(source, "add", ".")
    git(source, "-c", "user.name=Test", "-c", "user.email=test@localhost",
        "-c", "commit.gpgsign=false", "commit", "-m", "Real command input")
    run_root = tmp_path / "run"
    run_root.mkdir()
    return prepare(source, run_root, "prefix-binding", "actual-run")


def _resolve(root: Path, checkout: GitBranch, argument: str, *, run_root: Path | None = None) -> tuple[str, ...]:
    job = LocalCommandJob(run_id=checkout.run_id, experiment_id="actual-input", project=checkout.project,
                          run_root=run_root or root / "run", command_id="prefix-bound")
    with _host_command(root, [sys.executable, argument]), bind_git_branch(checkout):
        argv, required, _ = _command(job, "execution.simulation_runner")
    assert required == ()
    return argv


@pytest.mark.parametrize("kind", ["placeholder", "absolute-current", "absolute-source"])
def test_real_candidate_filename_prefix_routes_to_own_checkout(
    tmp_path: Path, checkout: GitBranch, kind: str,
) -> None:
    assert str(checkout.source_root) in str(checkout.root)
    path = {"placeholder": "{code_root}/runner.py", "absolute-current": str(checkout.root / "runner.py"),
            "absolute-source": str(checkout.source_root / "runner.py")}[kind]
    argv = _resolve(tmp_path, checkout, path)
    assert argv == (sys.executable, str(checkout.root / "runner.py"))
    assert git(checkout.source_root, "branch", "--show-current") == "main"
    assert git(checkout.source_root, "status", "--porcelain") == ""


@pytest.mark.parametrize("kind", ["inline-source", "flag-source", "other-checkout", "traversal"])
def test_shared_source_literals_and_unowned_prefixes_remain_rejected(
    tmp_path: Path, checkout: GitBranch, kind: str,
) -> None:
    outside = tmp_path / "code-research-unowned"
    outside.mkdir()
    (outside / "runner.py").write_text("VALUE = 2\n", encoding="utf-8")
    argument = {
        "inline-source": f"import runpy; runpy.run_path('{checkout.source_root}/runner.py')",
        "flag-source": f"--script={checkout.source_root}/runner.py",
        "other-checkout": str(outside / "runner.py"),
        "traversal": str(checkout.root / ".." / outside.name / "runner.py"),
    }[kind]
    with pytest.raises(ValueError):
        _resolve(tmp_path, checkout, argument)


@pytest.mark.parametrize("link", ["symbolic", "hard"])
def test_candidate_links_cannot_escape_path_verification(tmp_path: Path, checkout: GitBranch, link: str) -> None:
    target = tmp_path / "outside.py"
    target.write_text("VALUE = 3\n", encoding="utf-8")
    linked = checkout.root / "linked.py"
    try:
        if link == "symbolic":
            linked.symlink_to(target)
        else:
            os.link(target, linked)
    except OSError:
        pytest.skip("Host filesystem does not support this link type")
    with pytest.raises(ValueError, match="link"):
        _resolve(tmp_path, checkout, str(linked))


def test_distinct_project_directory_retains_unrelated_output_argument(
    tmp_path: Path, checkout: GitBranch,
) -> None:
    name = "MARS_FOLDER_PROJECTS_REGISTRY"
    saved = os.environ.get(name)
    os.environ[name] = str(tmp_path / "folder-registry.json")
    reset_settings_cache()
    try:
        project = open_folder(str(tmp_path / "project"), create=True)
        run_root = tmp_path / "distinct-run"
        run_root.mkdir()
        branch = prepare(checkout.source_root, run_root, project.name, "distinct-prefix-run")
        output = branch.root / "new-output-directory"
        assert str(branch.source_root) not in str(output) and not output.exists()
        argv = _resolve(tmp_path, branch, str(output), run_root=run_root)
        assert argv == (sys.executable, str(output))
    finally:
        if saved is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = saved
        reset_settings_cache()


def test_handoff_preview_resolves_saved_checkout_without_live_agent_context(
    tmp_path: Path, checkout: GitBranch, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.harness.tools.execution.handoff import coding_handoff_errors
    from app.harness.tools.git_branch import current_git_branch, receipt_path
    monkeypatch.setenv('MARS_FOLDER_PROJECTS_REGISTRY', str(tmp_path / 'preview-registry.json'))
    reset_settings_cache()
    try:
        project = open_folder(str(tmp_path / 'preview-project'), create=True)
        (project.metadata_root / 'repo_link.yaml').write_text(
            yaml.safe_dump({'local_path': str(checkout.source_root)}))
        run_root = tmp_path / 'preview-run'
        run_root.mkdir()
        branch = prepare(checkout.source_root, run_root, project.name, 'preview-run')
        coding = {'schema': 'code_spec.v1', 'project': project.name, 'agent': 'coding',
                  'target_lang': 'python', 'baseline_compat': {'preserved': True}, 'files_changed': [],
                  'execution_jobs': [{'name': 'candidate', 'config': {
                      'command_id': 'prefix-bound', 'seed': 42, 'budget_steps': 1}}]}
        before = receipt_path(run_root).read_bytes()
        with _host_command(tmp_path, [sys.executable, '{code_root}/runner.py']):
            assert current_git_branch(project.name) is None
            assert any('bound research checkout' in error for error in
                       coding_handoff_errors('', coding, project=project.name))
            assert not coding_handoff_errors('', coding, project=project.name,
                                             run_id=branch.run_id, run_root=run_root)
            assert coding_handoff_errors('', coding, project=project.name,
                                         run_id='another-run', run_root=run_root)
            assert current_git_branch(project.name) is None
        assert receipt_path(run_root).read_bytes() == before
        assert json.loads(before)['run_id'] == branch.run_id
        assert git(branch.root, 'status', '--porcelain') == ''
    finally:
        reset_settings_cache()
