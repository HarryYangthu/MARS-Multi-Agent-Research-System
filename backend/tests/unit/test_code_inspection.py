"""Real temporary source files and the real registry; no provider/tool substitutes."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.harness.tools.code_inspection import inspection_policy, read_code_fragment, repo_list_tool, repo_search_tool
from app.harness.tools.registry import ToolContext, get_registry


@pytest.fixture
def source_context(tmp_path: Path) -> ToolContext:
    (tmp_path / "libs").mkdir()
    (tmp_path / "configs").mkdir()
    (tmp_path / "audit").mkdir()
    (tmp_path / "libs/model.py").write_text("class Filter:\n    taps = 17\n    def forward(self, x):\n        return x\n")
    (tmp_path / "configs/baseline.yaml").write_text("model:\n  taps: 17\nepochs: 20\n")
    (tmp_path / "configs/other.yaml").write_text("optimizer: Adam\n")
    return ToolContext("inspect", "pimc", "commander", project_repo_root=str(tmp_path),
                       extra={"run_root": str(tmp_path / "audit")})


@pytest.mark.asyncio
async def test_list_is_names_only_and_supports_scoped_pagination(source_context: ToolContext) -> None:
    root = await repo_list_tool({}, source_context)
    assert root.ok
    assert root.output["entries"] == [{"path": "configs", "type": "directory"}, {"path": "libs", "type": "directory"}]
    assert "class Filter" not in str(root.output)
    first = await repo_list_tool({"path": "configs", "glob": "*.yaml", "limit": 1}, source_context)
    assert first.ok and first.output["truncated"]
    second = await repo_list_tool({"path": "configs", "glob": "*.yaml", "limit": 1,
                                   "offset": first.output["next_offset"]}, source_context)
    assert second.ok and not second.output["truncated"]
    assert first.output["entries"] != second.output["entries"]


@pytest.mark.asyncio
async def test_search_provenance_then_read_only_relevant_lines(source_context: ToolContext) -> None:
    search = await repo_search_tool({"query": "TAPS", "path": "libs", "glob": "*.py"}, source_context)
    assert search.ok
    assert len(search.output["matches"]) == 1
    hit = search.output["matches"][0]
    assert hit["path"] == "libs/model.py" and hit["line"] == 2
    assert "epochs" not in str(search.output)
    fragment = await read_code_fragment({"path": hit["path"], "start_line": 2, "end_line": 2}, source_context)
    assert fragment.ok
    assert fragment.output["content"] == "    taps = 17\n"
    assert fragment.output["sha256"] == hit["sha256"]
    assert fragment.output["line_start"] == fragment.output["line_end"] == 2
    assert fragment.output["next_line"] == 3
    source = Path(source_context.project_repo_root) / hit["path"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == hit["sha256"]


@pytest.mark.asyncio
async def test_search_is_literal_and_pages_matches(source_context: ToolContext) -> None:
    absent = await repo_search_tool({"query": "taps|epochs"}, source_context)
    assert absent.ok and absent.output["matches"] == []
    first = await repo_search_tool({"query": "taps", "limit": 1}, source_context)
    second = await repo_search_tool({"query": "taps", "limit": 1, "offset": first.output["next_offset"]}, source_context)
    assert first.ok and second.ok
    assert first.output["matches"][0]["path"] != second.output["matches"][0]["path"]


@pytest.mark.asyncio
async def test_no_credentials_ignored_data_or_symbolic_links(source_context: ToolContext, tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data/private.py").write_text("taps = 'private'\n")
    (tmp_path / "configs/.env.local").write_text("taps = secret\n")
    external = tmp_path.parent / (tmp_path.name + "-external.py")
    external.write_text("taps = external\n")
    try:
        (tmp_path / "libs/link.py").symlink_to(external)
        (tmp_path / "libs/alias.py").symlink_to(tmp_path / "libs/model.py")
        search = await repo_search_tool({"query": "taps"}, source_context)
        assert search.ok
        assert {m["path"] for m in search.output["matches"]} == {"libs/model.py", "configs/baseline.yaml"}
        for path in ("../private.py", "configs/.env.local", "data/private.py", "libs/link.py", "libs/alias.py"):
            result = await read_code_fragment({"path": path}, source_context)
            assert not result.ok, path
        assert not (await repo_list_tool({"path": "../"}, source_context)).ok
        assert not (await repo_list_tool({"path": str(tmp_path)}, source_context)).ok
        assert not (await repo_list_tool({"path": "data"}, source_context)).ok
    finally:
        external.unlink()


@pytest.mark.asyncio
async def test_output_budgets_and_windows_relative_paths(source_context: ToolContext, tmp_path: Path) -> None:
    policy = inspection_policy()
    (tmp_path / "libs/long.py").write_text("value = 1\n" * (policy.max_read_lines + 4))
    excerpt = await read_code_fragment({"path": "libs\\long.py"}, source_context)
    assert excerpt.ok
    assert excerpt.output["line_end"] == policy.max_read_lines
    assert excerpt.output["next_line"] == policy.max_read_lines + 1
    assert not (await read_code_fragment({"path": "libs/long.py", "end_line": policy.max_read_lines + 1}, source_context)).ok
    (tmp_path / "libs/oversize.py").write_text("x" * (policy.max_file_bytes + 1))
    search = await repo_search_tool({"query": "value", "path": "libs", "glob": "oversize.py"}, source_context)
    assert search.ok and search.output["skipped_large_files"] == 1
    assert not (await read_code_fragment({"path": "libs/oversize.py"}, source_context)).ok
    for args in ({"limit": True}, {"depth": 0}, {"limit": policy.max_results + 1}):
        assert not (await repo_list_tool(args, source_context)).ok


@pytest.mark.asyncio
async def test_registry_grants_only_reads_and_writes_real_audit(source_context: ToolContext, tmp_path: Path) -> None:
    registry = get_registry()
    for tool, args in (("code.repo_list", {"path": "libs"}),
                       ("code.repo_search", {"query": "taps", "path": "libs"}),
                       ("code.repo_read_lines", {"path": "libs/model.py", "start_line": 2, "end_line": 2})):
        result = await registry.dispatch(tool, args, source_context)
        assert result.ok, result.error
        assert result.evidence_refs
    write = await registry.dispatch("code.write_file", {"path": "libs/model.py", "content": "changed"}, source_context)
    assert not write.ok and write.status == "not_allowed"
    override = await registry.dispatch("code.repo_list", {"project_repo_root": "/"}, source_context)
    assert not override.ok
    assert "taps = 17" in (tmp_path / "libs/model.py").read_text()
    assert list((tmp_path / "audit").rglob("*.jsonl"))


@pytest.mark.asyncio
async def test_search_byte_budget_reports_incomplete_coverage(source_context: ToolContext, tmp_path: Path) -> None:
    policy = inspection_policy()
    for index in range(policy.max_search_bytes // policy.max_file_bytes + 2):
        (tmp_path / "libs" / f"large_{index}.py").write_text("#" * policy.max_file_bytes)
    result = await repo_search_tool({"query": "absent-marker", "path": "libs"}, source_context)
    assert result.ok and result.output["matches"] == []
    assert result.output["scan_limited"] and result.output["truncated"]


@pytest.mark.asyncio
async def test_other_source_languages_are_readable(source_context: ToolContext, tmp_path: Path) -> None:
    for suffix in (".tsx", ".rs", ".go", ".ps1"):
        name = f"libs/feature{suffix}"
        (tmp_path / name).write_text("source_marker = 17\n")
        result = await read_code_fragment({"path": name, "start_line": 1, "end_line": 1}, source_context)
        assert result.ok and result.output["content"] == "source_marker = 17\n"
