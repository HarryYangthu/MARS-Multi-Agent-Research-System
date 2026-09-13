"""Configuration, schema and real-file checks; no model/tool substitutes."""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from app.agents.idea.focused_agent import FocusedIdeaAgent
from app.agents.idea.focused_research import focused_research_errors, research_schema
from app.harness.context.project_knowledge import load_project_knowledge
from app.harness.tools.search.source_fetch import extract_pdf
from app.harness.tools.registry import get_registry, ToolContext
from app.harness.tools.code import repo_reader_tool
from app.agents.base import RunRequest
from app.settings import repo_root
from app.api.timeline import _agent_event_worklog


def knowledge_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.yaml").write_text("knowledge_file: knowledge.md\n")
    return project


def test_knowledge_is_complete_frozen_per_run_and_refreshed_next_run(tmp_path: Path) -> None:
    project = knowledge_project(tmp_path)
    content = "PIMC 公式与经验\n" * 3000 + "最后一条不可遗漏"
    (project / "knowledge.md").write_text(content)
    first, receipt = load_project_knowledge(project, tmp_path / "run1")
    assert first == content and receipt["content"].endswith("最后一条不可遗漏")
    (project / "knowledge.md").write_text("已核对的新经验")
    assert load_project_knowledge(project, tmp_path / "run1")[0] == content
    assert load_project_knowledge(project, tmp_path / "run2")[0] == "已核对的新经验"


def test_knowledge_rejects_missing_empty_escaping_or_changed_snapshot(tmp_path: Path) -> None:
    project = knowledge_project(tmp_path)
    with pytest.raises(FileNotFoundError):
        load_project_knowledge(project, tmp_path / "run")
    (project / "knowledge.md").write_text(" ")
    with pytest.raises(ValueError, match="empty"):
        load_project_knowledge(project, tmp_path / "run")
    (project / "knowledge.md").write_text("original")
    load_project_knowledge(project, tmp_path / "run")
    snapshot = tmp_path / "run/input/project_knowledge.v1.json"
    record = json.loads(snapshot.read_text())
    record["content"] = "changed"
    snapshot.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="invalid"):
        load_project_knowledge(project, tmp_path / "run")
    (project / "project.yaml").write_text("knowledge_file: ../outside.md")
    with pytest.raises(ValueError, match="inside"):
        load_project_knowledge(project, tmp_path / "run2")


def test_actual_focused_configuration_uses_different_models_and_no_delegation() -> None:
    agent = FocusedIdeaAgent()
    snapshot = agent.service_profile_snapshot
    assert snapshot["author"]["model"] != snapshot["reviewer"]["model"]
    assert "idea.research_delegate" not in agent.config.tools
    assert "search.fetch_sources" in agent.config.tools
    assert agent.config.raw["loop"]["max_reflections"] == 2
    assert "min_sources" not in research_schema()["properties"]


@pytest.mark.asyncio
async def test_actual_focused_permissions_reach_schema_validation_before_network(tmp_path: Path) -> None:
    agent = FocusedIdeaAgent()
    registry = get_registry()
    scope = registry.scope_for_read_tools(agent.name, agent.configured_read_tools())
    ctx = ToolContext("permission-contract", "pimc", "idea", extra={"run_root": str(tmp_path)},
                      configured_read_scope=scope)
    for tool in ("search.cvf_search", "search.neurips_search", "search.openalex_search"):
        result = await registry.dispatch(tool, {}, ctx)
        assert not result.ok and result.status == "error" and "required" in str(result.error)


def test_knowledge_rejects_nonobject_snapshot(tmp_path: Path) -> None:
    project = knowledge_project(tmp_path)
    path = tmp_path / "run/input/project_knowledge.v1.json"
    path.parent.mkdir(parents=True)
    path.write_text("[]")
    with pytest.raises(ValueError, match="invalid"):
        load_project_knowledge(project, tmp_path / "run")


@pytest.mark.asyncio
async def test_actual_code_file_can_be_read_completely_and_rejects_escape(tmp_path: Path) -> None:
    folder = tmp_path / "libs"
    folder.mkdir()
    content = "# Background declarations\n" * 1000 + "class StaticPIMC: pass\n"
    (folder / "model.py").write_text(content)
    ctx = ToolContext("file-contract", "pimc", "idea", project_repo_root=str(tmp_path))
    first = await repo_reader_tool({"path": "libs/model.py"}, ctx)
    assert first.ok and first.output["truncated"]
    second = await repo_reader_tool({"path": "libs/model.py", "char_offset": first.output["next_offset"]}, ctx)
    assert second.ok and first.output["content"] + second.output["content"] == content
    assert second.output["next_offset"] is None
    escaped = await repo_reader_tool({"path": "../outside.py"}, ctx)
    assert not escaped.ok


@pytest.mark.asyncio
async def test_entire_maintained_knowledge_is_in_author_and_independent_reviewer_input(tmp_path: Path) -> None:
    agent = FocusedIdeaAgent()
    request = RunRequest(project="pimc", user_request="检查背景加载", extra={"run_root": str(tmp_path)})
    context = await agent.build_context(request)
    source = (repo_root() / "projects/pimc/context/public_context.md").read_text()
    author = agent._messages_for_context(request, context, purpose="knowledge-contract")
    reviewer = agent.review_messages(request, context)
    assert any(source in message.content for message in author)
    assert any(source in message.content for message in reviewer)
    assert json.loads((tmp_path / "input/project_knowledge.v1.json").read_text())["content"] == source


def test_review_progress_is_visible_in_worklog_without_becoming_run_approval() -> None:
    item = _agent_event_worklog(index=1, timestamp_hints={}, payload={
        "event": "agent.progress", "kind": "review", "agent": "idea", "phase": "reflect",
        "message": "审查发现边界条件缺失", "timestamp": "2026-09-13T00:00:00Z"})
    assert item is not None and item.kind == "progress"
    assert item.detail == "审查发现边界条件缺失" and item.status == "reflect"


def test_fulltext_evidence_cannot_be_replaced_by_a_declared_source(tmp_path: Path) -> None:
    metadata = {"research_context": {
        "schema": "idea.research_context.v1", "question": "test", "selection_principles": ["method fit"],
        "sources": [{"source_id": "source_1234567890abcdef", "title": "unread", "url": "https://example.org/unread",
                     "decision": "use", "reason": "unverified declaration"}],
        "stop_reason": "test", "open_questions": [],
    }}
    errors = focused_research_errors(metadata, [], tmp_path)
    assert any("actually retrieved" in error for error in errors)
    assert any("successful full-text" in error for error in errors)


def test_pdf_offset_reads_remainder_without_loss_or_duplicate_prefix() -> None:
    # Create and parse a real, minimal PDF; this is a parser fixture, not an
    # archived paper, network response or model observation.
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 700 Td (Complete method steps and assumptions.) Tj ET")
    page[NameObject("/Contents")] = stream
    output = BytesIO()
    writer.write(output)
    data = output.getvalue()
    full = extract_pdf(data, start_page=1, max_pages=1, max_chars=1000)["visible_pages"][0]["text"]
    first = extract_pdf(data, start_page=1, max_pages=1, max_chars=10)["visible_pages"][0]
    assert first["truncated"] and first["next_offset"] == 10
    rest = extract_pdf(data, start_page=1, max_pages=1, max_chars=1000, char_offset=first["next_offset"])["visible_pages"][0]
    assert first["text"] + rest["text"] == full
    assert rest["next_offset"] is None and not rest["truncated"]
