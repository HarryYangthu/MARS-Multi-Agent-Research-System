"""Real temporary source files and the real registry; no provider/tool substitutes."""
from __future__ import annotations

import hashlib
import os
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
async def test_missed_alias_returns_separate_actual_user_identifier_candidates(source_context: ToolContext, tmp_path: Path) -> None:
    (tmp_path / 'libs/model.py').write_text('class Existing7LFilter:\n    taps = 17\n')
    source_context.extra['original_request'] = '仿真一下 7L 模型'
    result = await repo_search_tool({'query': 'num_layers', 'path': 'libs'}, source_context)
    assert result.ok and result.output['matches'] == []  # Literal semantics are unchanged.
    assert result.output['fallback_queries'] == ['7L']
    assert result.output['fallback_matches'][0]['matched_query'] == '7L'
    assert result.output['fallback_matches'][0]['path'] == 'libs/model.py'
    assert result.output['fallback_matches'][0]['sha256'] == hashlib.sha256((tmp_path / 'libs/model.py').read_bytes()).hexdigest()
    assert result.evidence_refs == ['libs/model.py']


@pytest.mark.asyncio
async def test_search_accepts_an_exact_file_without_scanning_other_files(source_context: ToolContext) -> None:
    result = await repo_search_tool({'query': 'taps', 'path': 'libs/model.py'}, source_context)
    assert result.ok and len(result.output['matches']) == 1
    assert result.output['matches'][0]['path'] == 'libs/model.py'
    assert result.output['matches'][0]['line'] == 2
    for path in ('../outside.py', 'configs/.env.local', 'data/private.py'):
        assert not (await repo_search_tool({'query': 'secret', 'path': path}, source_context)).ok


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
    oversized_range = await read_code_fragment({"path": "libs/long.py", "end_line": policy.max_read_lines + 1}, source_context)
    assert oversized_range.ok and oversized_range.output['range_limited']
    assert oversized_range.output['line_end'] == policy.max_read_lines
    assert oversized_range.output['next_line'] == policy.max_read_lines + 1
    assert not (await read_code_fragment({"path": "libs/long.py", "start_line": 2, "end_line": 1}, source_context)).ok
    assert not (await read_code_fragment({"path": "libs/long.py", "end_line": True}, source_context)).ok
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


@pytest.fixture
def unrestricted_source_context(source_context: ToolContext) -> ToolContext:
    return ToolContext(source_context.run_id, "search_scope_fixture", "commander",
                       project_repo_root=source_context.project_repo_root,
                       extra={"run_root": str(Path(source_context.project_repo_root) / "runs/inspection")})


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["src", "libs/missing/deeper", "src\\missing"])
async def test_missing_search_scope_recovers_to_actual_repository_root(
    unrestricted_source_context: ToolContext, path: str,
) -> None:
    result = await get_registry().dispatch("code.repo_search", {"query": "class Filter", "path": path},
                                          unrestricted_source_context)
    assert result.ok, result.error
    assert result.output["requested_path"] == path.replace("\\", "/")
    assert result.output["effective_path"] == "."
    assert "does not exist" in result.output["warning"]
    assert [hit["path"] for hit in result.output["matches"]] == ["libs/model.py"]
    actual = Path(unrestricted_source_context.project_repo_root) / "libs/model.py"
    assert result.output["matches"][0]["sha256"] == hashlib.sha256(actual.read_bytes()).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [".", "libs", "libs/model.py"])
async def test_existing_search_scope_does_not_broaden(unrestricted_source_context: ToolContext, path: str) -> None:
    result = await repo_search_tool({"query": "class Filter", "path": path}, unrestricted_source_context)
    assert result.ok, result.error
    assert result.output["requested_path"] == result.output["effective_path"] == path
    assert result.output["warning"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["../missing", "/missing", "C:\\missing", "libs/../../missing",
                                  ".git/missing", "data/missing", "configs/.env.missing",
                                  "libs/model.py/missing"])
async def test_invalid_excluded_and_nondirectory_search_scopes_never_recover(
    unrestricted_source_context: ToolContext, path: str,
) -> None:
    result = await repo_search_tool({"query": "class Filter", "path": path}, unrestricted_source_context)
    assert not result.ok
    assert result.output is None


@pytest.mark.asyncio
async def test_missing_search_scope_preserves_project_path_policy(source_context: ToolContext, tmp_path: Path) -> None:
    # This real project's policy permits libs/configs but not arbitrary root files.
    (tmp_path / "ungranted.py").write_text("taps = 'not permitted'\n")
    denied = await repo_search_tool({"query": "taps", "path": "src"}, source_context)
    assert not denied.ok and "read policy" in (denied.error or "")
    recovered = await repo_search_tool({"query": "taps", "path": "libs/missing"}, source_context)
    assert recovered.ok, recovered.error
    assert recovered.output["effective_path"] == "."
    assert {hit["path"] for hit in recovered.output["matches"]} == {"libs/model.py", "configs/baseline.yaml"}


@pytest.mark.asyncio
async def test_missing_child_of_symbolic_scope_never_recovers(
    unrestricted_source_context: ToolContext, tmp_path: Path,
) -> None:
    (tmp_path / "alias").symlink_to(tmp_path / "libs", target_is_directory=True)
    (tmp_path / "broken").symlink_to(tmp_path / "absent", target_is_directory=True)
    for path in ("alias/missing", "broken/missing"):
        result = await repo_search_tool({"query": "class Filter", "path": path}, unrestricted_source_context)
        assert not result.ok and "symbolic links" in (result.error or "")


@pytest.mark.asyncio
async def test_missing_child_of_unreadable_scope_never_recovers(
    unrestricted_source_context: ToolContext, tmp_path: Path,
) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("Root bypasses real filesystem permission denial")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o100)  # Child lookup is possible, directory enumeration is denied.
    try:
        result = await repo_search_tool({"query": "class Filter", "path": "locked/missing"},
                                        unrestricted_source_context)
        assert not result.ok and "Permission denied" in (result.error or "")
    finally:
        locked.chmod(0o700)


@pytest.mark.asyncio
async def test_missing_repository_and_list_scope_remain_explicit_failures(
    unrestricted_source_context: ToolContext, tmp_path: Path,
) -> None:
    listing = await repo_list_tool({"path": "src"}, unrestricted_source_context)
    assert not listing.ok and "src" in (listing.error or "")
    disconnected = ToolContext("missing", "search_scope_fixture", "commander",
                               project_repo_root=str(tmp_path / "absent"))
    search = await repo_search_tool({"path": "src", "query": "class Filter"}, disconnected)
    assert not search.ok and "not connected" in (search.error or "")
