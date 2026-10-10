"""Actual file reads across the absolute/relative input boundary; no substitutes."""
from pathlib import Path

import pytest

from app.harness.tools.code import repo_reader_tool, write_file_tool
from app.harness.tools.registry import ToolContext


@pytest.mark.asyncio
async def test_absolute_and_relative_reads_have_the_same_evidence(tmp_path: Path) -> None:
    source = tmp_path / "libs" / "model.py"
    source.parent.mkdir()
    source.write_text("def forward(x):\n    return x * 2\n")
    ctx = ToolContext("absolute-read", "pimc", "idea", project_repo_root=str(tmp_path))
    relative = await repo_reader_tool({"path": "libs/model.py"}, ctx)
    absolute = await repo_reader_tool({"path": str(source)}, ctx)
    assert relative.ok and absolute.ok
    assert absolute.output == relative.output
    assert absolute.evidence_refs == ["libs/model.py"]
    # The read convenience does not expand write permissions.
    rejected = await write_file_tool({"path": str(source), "content": "changed"}, ctx)
    assert not rejected.ok
    assert source.read_text() == "def forward(x):\n    return x * 2\n"


@pytest.mark.asyncio
async def test_absolute_reader_rejects_external_and_symlink_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "libs").mkdir(parents=True)
    outside = tmp_path / "private.py"
    outside.write_text("private = True\n")
    link = repo / "libs" / "link.py"
    link.symlink_to(outside)
    ctx = ToolContext("absolute-read", "pimc", "idea", project_repo_root=str(repo))
    for name in (str(outside), str(link), str(repo / ".." / "private.py"), "../private.py"):
        rejected = await repo_reader_tool({"path": name}, ctx)
        assert not rejected.ok, name
        assert rejected.output is None


@pytest.mark.asyncio
async def test_absolute_reader_keeps_repository_path_policy(tmp_path: Path) -> None:
    source = tmp_path / "data" / "ignored.py"
    source.parent.mkdir()
    source.write_text("value = 4\n")
    ctx = ToolContext("absolute-read", "pimc", "idea", project_repo_root=str(tmp_path))
    # The real PIMC connection excludes data/, even for a caller-bound root.
    rejected = await repo_reader_tool({"path": str(source)}, ctx)
    assert not rejected.ok
    assert "ignored" in str(rejected.error)
