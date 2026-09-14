"""Pure presentation and real temporary-file boundary tests; no execution doubles."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.api.idea_materials import collect_idea_materials, enrich_source_errors
from app.api.timeline import _context_manifest_worklog
from app.harness.agent_loop.trace import digest
from app.harness.schema.frontmatter_parser import dumps
from app.harness.tools.search.source_fetch import source_failure_summary
from app.storage.run_store import RunHandle


def save(root: Path, relative: str, data: Any) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_empty_run_has_no_invented_sources_or_context(tmp_path: Path) -> None:
    view = collect_idea_materials(tmp_path, "empty")
    assert not view.items and not view.warnings


def test_full_knowledge_snapshot_and_partial_code_stay_distinct(tmp_path: Path) -> None:
    content = "# PIMC\n" + "背景知识\n" * 5000
    save(tmp_path, "input/project_knowledge.v1.json", {"content": content, "sha256": digest(content), "source": "context/public_context.md"})
    save(tmp_path, "agent_traces/idea/trace/tools/0001.json", {
        "tool": "code.repo_reader", "args": {"path": "libs/model.py"}, "ok": True,
        "output": {"content": "class StaticPIMC:", "char_start": 100, "char_end": 117, "total_chars": 10000, "truncated": True},
    })
    view = collect_idea_materials(tmp_path, "demo")
    knowledge, code = view.items
    assert knowledge.text == content and knowledge.status == "已载入快照"
    assert code.status == "已读取片段" and code.text == "class StaticPIMC:"
    assert "100–117 / 10000" in code.description
    assert all("text" not in item for item in view.model_dump()["items"])
    assert collect_idea_materials(tmp_path, "demo").items[1].id == code.id


def test_changed_snapshot_is_not_claimed_valid(tmp_path: Path) -> None:
    save(tmp_path, "input/project_knowledge.v1.json", {"content": "changed", "sha256": digest("original")})
    assert collect_idea_materials(tmp_path, "demo").items[0].status == "快照校验失败"


def test_failed_source_retains_reason_and_is_not_downloaded(tmp_path: Path) -> None:
    # Input to the read-only ledger presenter, not a simulated acquisition.
    row = {"title": "Unavailable paper", "url": "https://arxiv.org/abs/1903.03107", "download_url": "https://arxiv.org/pdf/1903.03107.pdf",
           "ok": False, "error_code": "http_404", "error": "source HTTP status 404"}
    save(tmp_path, "idea/research/downloads/source_fetch_index.v1.json", [row])
    path = tmp_path / "idea/idea_proposal.v2.md"
    path.write_text(dumps({"research_context": {"sources": [{"title": row["title"], "url": row["url"], "decision": "defer", "reason": "全文未读"}]}}, ""))
    view = collect_idea_materials(tmp_path, "demo")
    paper = next(item for item in view.items if item.kind == "paper")
    assert len([item for item in view.items if item.kind == "paper"]) == 1
    assert "http_404" in paper.description and paper.decision == "defer"
    assert not paper.archive_available and not paper.preview_available
    assert not paper.read_windows


def test_missing_archive_keeps_read_window_but_never_claims_download(tmp_path: Path) -> None:
    row = {"title": "Read receipt", "url": "https://example.org/paper", "ok": True, "archive_complete": True,
           "download_path": str(tmp_path / "missing.pdf"), "source_id": "source_0000000000000000",
           "visible_pages": [{"page": 2, "text": "a recorded excerpt", "char_start": 0, "char_end": 18, "full_page_text_chars": 200}]}
    save(tmp_path, "idea/research/downloads/source_fetch_index.v1.json", [row, row])
    view = collect_idea_materials(tmp_path, "demo")
    paper = view.items[0]
    assert len(view.items) == 1 and len(paper.read_windows) == 1
    assert not paper.archive_available and paper.preview_available
    assert paper.status == "仅保留阅读记录" and view.warnings
    assert paper.text.count("a recorded excerpt") == 1


def test_outside_symlinks_malformed_and_large_files_are_not_read(tmp_path: Path) -> None:
    root = tmp_path / "run"
    (root / "input").mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("private text")
    (root / "input/user_request.md").symlink_to(outside)
    (root / "input/project_knowledge.v1.json").write_text("{")
    context = root / "context/idea_context_snapshot.v2.md"
    context.parent.mkdir()
    context.write_text("x" * 2_000_001)
    view = collect_idea_materials(root, "demo")
    assert not view.items and len(view.warnings) == 3
    assert "private text" not in view.model_dump_json()


def test_paper_links_reject_executable_schemes_and_latest_version_wins(tmp_path: Path) -> None:
    (tmp_path / "idea").mkdir()
    for version in (2, 10):
        (tmp_path / f"idea/idea_proposal.v{version}.md").write_text(dumps({"research_context": {
            "question": f"version {version}", "sources": [{"title": "Paper", "url": "javascript:alert(1)", "decision": "reject"}],
        }}, ""))
    view = collect_idea_materials(tmp_path, "demo")
    assert view.items[0].source_url == ""
    assert "version 10" in view.items[-1].text


def test_diagnostic_enrichment_is_exact_and_does_not_rewrite_history(tmp_path: Path) -> None:
    generic = "source fetch failed; see individual errors"
    args = {"sources": [{"url": "https://arxiv.org/abs/1903.03107"}], "max_pages": 6}
    call = {"tool": "search.fetch_sources", "error": generic, "args": args,
            "output": {"sources": [{"title": "Paper", "ok": False, "error_code": "http_404", "error": "HTTP 404"}]}}
    path = save(tmp_path, "agent_traces/idea/trace/tools/0001.json", call)
    original = path.read_bytes()
    entries = [{"tool": call["tool"], "args": args, "error": generic}]
    enriched = enrich_source_errors(tmp_path, entries)
    assert "Paper [http_404]" in enriched[0]["error"]
    assert enriched[0]["original_error"] == generic and entries[0]["error"] == generic
    assert path.read_bytes() == original
    assert enrich_source_errors(tmp_path, [{"args": {}, "error": generic}])[0]["error"] == generic


def test_compiled_context_pack_is_counted_for_its_agent(tmp_path: Path) -> None:
    save(tmp_path, "context/idea_context_pack.v2.json", {"agent": "idea", "timestamp": "2026-09-14T01:00:00Z",
         "compiled_manifest": {"message_count": 3}})
    run = RunHandle("demo", tmp_path, "pimc", "task", "idea", "2026-09-14T01:00:00Z")
    items = _context_manifest_worklog(run=run, agent_filter="idea")
    assert len(items) == 1 and items[0].kind == "context"
    assert not _context_manifest_worklog(run=run, agent_filter="coding")


def test_failure_summary_keeps_title_code_and_bounds_size() -> None:
    rows = [{"ok": False, "title": f"Paper {i}", "error_code": "http_404", "error": "x" * 2000} for i in range(10)]
    summary = source_failure_summary(rows)
    assert "10 source(s) failed" in summary and "Paper 0 [http_404]" in summary
    assert "+6 more" in summary and len(summary) < 2000
