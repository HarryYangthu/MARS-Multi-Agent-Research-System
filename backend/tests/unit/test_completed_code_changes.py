"""Completed changes from real local tool calls, compared with actual files."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.bridge.code_changes import parse_diff
from app.bridge.code_repository import BrowsePolicy, CodeRepository
from app.bridge.completed_code_changes import _net_diff, _patched, completed_code_changes
from app.harness.tools.project_repo import ProjectRepo
from app.harness.tools.registry import ToolContext, get_registry
from app.storage.run_store import RunHandle, RunStore


def setup(tmp_path: Path) -> tuple[RunHandle, Path, ToolContext, CodeRepository]:
    run = RunStore(tmp_path / "runs").create(task="completed-code", project="code-review-local-test")
    root = tmp_path / "repo"
    root.mkdir()
    ctx = ToolContext(run_id=run.run_id, project=run.project, agent="coding",
                      project_repo_root=str(root), extra={"run_root": str(run.root)})
    browser = CodeRepository(ProjectRepo(run.project, root, "local_path", True, ("",), (), ()),
                             BrowsePolicy(200, 500, 8_388_608))
    return run, root, ctx, browser


@pytest.mark.asyncio
async def test_multiple_writes_show_net_changes_and_new_file(tmp_path: Path) -> None:
    run, root, ctx, browser = setup(tmp_path)
    (root / "existing.py").write_text("value = 1\nkeep = True\n")
    for path, content in [("existing.py", "value = 2\nkeep = True\n"),
                          ("new.py", "first = 1\n"), ("new.py", "first = 2\nsecond = 3\n"),
                          ("existing.py", "value = 3\nkeep = True\nextra = 4\n")]:
        result = await get_registry().dispatch("code.write_file", {"path": path, "content": content}, ctx)
        assert result.ok, result.error
        assert (root / path).read_text() == content
    view = completed_code_changes(run, project=run.project, repository=browser)
    assert not view["warnings"] and len(view["items"]) == 2
    existing, new = view["items"]
    assert (existing["change"], existing["additions"], existing["deletions"]) == ("modified", 2, 1)
    assert (new["change"], new["additions"], new["deletions"]) == ("added", 2, 0)
    detail = completed_code_changes(run, project=run.project, repository=browser, change_id=existing["id"])
    assert [row["text"] for row in detail["lines"] if row["kind"] == "delete"] == ["value = 1"]
    assert [row["text"] for row in detail["lines"] if row["kind"] == "add"] == ["value = 3", "extra = 4"]
    assert "value = 2" not in json.dumps(detail)
    assert all(item["status"] == "applied" for item in view["items"])


@pytest.mark.asyncio
async def test_hundreds_of_real_reads_do_not_hide_earlier_writes(tmp_path: Path) -> None:
    run, root, ctx, browser = setup(tmp_path)
    write = await get_registry().dispatch("code.write_file", {"path": "new.py", "content": "value = 7\n"}, ctx)
    assert write.ok
    for _ in range(301):
        read = await get_registry().dispatch("code.repo_reader", {"path": "new.py"}, ctx)
        assert read.ok
    view = completed_code_changes(run, project=run.project, repository=browser)
    assert not view["warnings"] and len(view["items"]) == 1
    assert view["items"][0]["additions"] == 1
    assert (root / "new.py").read_text() == "value = 7\n"


@pytest.mark.asyncio
async def test_proposals_and_failed_writes_are_excluded(tmp_path: Path) -> None:
    run, root, ctx, browser = setup(tmp_path)
    (root / "value.py").write_text("value = 1\n")
    generated = await get_registry().dispatch("code.patch_generator", {"path": "value.py", "content": "value = 2\n"}, ctx)
    assert generated.ok and (root / "value.py").read_text() == "value = 1\n"
    (run.root / "coding/patch.v1.diff").write_text(str(generated.output["diff"]))
    refused = await get_registry().dispatch("code.write_file", {"path": "../outside.py", "content": "value = 3\n"}, ctx)
    assert not refused.ok
    view = completed_code_changes(run, project=run.project, repository=browser)
    assert view["items"] == [] and not view["warnings"]


@pytest.mark.asyncio
async def test_external_edit_cannot_be_labelled_as_verified(tmp_path: Path) -> None:
    run, root, ctx, browser = setup(tmp_path)
    result = await get_registry().dispatch("code.write_file", {"path": "value.py", "content": "value = 1\n"}, ctx)
    assert result.ok
    first = completed_code_changes(run, project=run.project, repository=browser)["items"][0]
    (root / "value.py").write_text("value = 9\n")
    view = completed_code_changes(run, project=run.project, repository=browser)
    assert view["items"] == [] and view["warnings"]
    with pytest.raises(KeyError):
        completed_code_changes(run, project=run.project, repository=browser, change_id=first["id"])
    result = await get_registry().dispatch("code.write_file", {"path": "value.py", "content": "value = 2\n"}, ctx)
    assert result.ok
    view = completed_code_changes(run, project=run.project, repository=browser)
    assert view["items"] == [] and view["warnings"], "intervening external changes cannot be attributed to this Agent"


@pytest.mark.asyncio
async def test_reverted_edits_are_not_changes(tmp_path: Path) -> None:
    run, root, ctx, browser = setup(tmp_path)
    (root / "value.py").write_text("value = 1\n")
    for content in ["value = 2\n", "value = 1\n"]:
        assert (await get_registry().dispatch("code.write_file", {"path": "value.py", "content": content}, ctx)).ok
    view = completed_code_changes(run, project=run.project, repository=browser)
    assert not view["items"] and not view["warnings"]


@pytest.mark.asyncio
async def test_actual_patch_is_reconstructed_from_snapshot(tmp_path: Path) -> None:
    run, root, ctx, browser = setup(tmp_path)
    (root / "value.py").write_text("value = 1\nkeep = True\n")
    diff = "--- a/value.py\n+++ b/value.py\n@@ -1,2 +1,3 @@\n value = 1\n+inserted = 2\n keep = True\n"
    result = await get_registry().dispatch("code.apply_patch", {"diff": diff}, ctx)
    assert result.ok, result.error
    assert (root / "value.py").read_text() == "value = 1\ninserted = 2\nkeep = True\n"
    view = completed_code_changes(run, project=run.project, repository=browser)
    assert not view["warnings"] and len(view["items"]) == 1
    assert (view["items"][0]["additions"], view["items"][0]["deletions"]) == (1, 0)


@pytest.mark.asyncio
async def test_invalid_snapshot_never_downgrades_to_a_later_partial_diff(tmp_path: Path) -> None:
    run, root, ctx, browser = setup(tmp_path)
    first = await get_registry().dispatch("code.write_file", {"path": "value.py", "content": "value = 1\n"}, ctx)
    assert first.ok and first.rollback_ref
    rollback = Path(first.rollback_ref)
    rollback.write_text("{}")  # Real receipt corruption, not a substitute tool success.
    second = await get_registry().dispatch("code.write_file", {"path": "value.py", "content": "value = 2\n"}, ctx)
    assert second.ok and (root / "value.py").read_text() == "value = 2\n"
    view = completed_code_changes(run, project=run.project, repository=browser)
    assert not view["items"] and view["warnings"]
    with pytest.raises(ValueError, match="项目"):
        completed_code_changes(run, project="different", repository=browser)


def test_empty_files_no_newline_and_zero_context_insertions() -> None:
    item = _net_diff("new.py", None, "", 3)
    assert (item["change"], item["additions"], item["deletions"]) == ("added", 0, 0)
    item = _net_diff("old.py", "", None, 3)
    assert (item["change"], item["additions"], item["deletions"]) == ("deleted", 0, 0)
    item = _net_diff("value.py", "before", "after", 3)
    assert (item["additions"], item["deletions"]) == (1, 1)
    assert _patched("before", item) == "after"
    insertion = parse_diff("--- a/value.py\n+++ b/value.py\n@@ -1,0 +2 @@\n+inserted\n")[0]
    assert _patched("first\nlast\n", insertion) == "first\ninserted\nlast\n"
