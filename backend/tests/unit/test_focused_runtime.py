"""Real receipt files and actual context construction; no provider substitutes."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.base import RunRequest
from app.agents.idea.focused_agent import FocusedIdeaAgent
from app.agents.idea.focused_runtime import (
    bind_focused_snapshot, invocation_snapshot_path, load_focused_snapshot,
)
from app.harness.agent_loop.trace import atomic_json


def test_explicit_retry_pins_new_configuration_without_rewriting_history(tmp_path: Path) -> None:
    old = FocusedIdeaAgent(author_settings={"request_timeout_seconds": 300}).service_profile_snapshot
    current = FocusedIdeaAgent().service_profile_snapshot
    original = tmp_path / "input/idea_focused.v1.json"
    atomic_json(original, old)
    checkpoint = tmp_path / "agent_traces/idea/previous/checkpoint.json"
    # Opaque file preservation fixture, never passed off as an executed model.
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("historical checkpoint bytes\n")
    before = original.read_bytes(), checkpoint.read_bytes()

    with pytest.raises(ValueError, match="new invocation"):
        bind_focused_snapshot(tmp_path, current, invocation="retry")
    with pytest.raises(ValueError, match="new invocation"):
        bind_focused_snapshot(tmp_path, current, revision_reason="retry")
    with pytest.raises(ValueError, match="new invocation"):
        bind_focused_snapshot(tmp_path, current, invocation="previous", revision_reason="retry")
    bind_focused_snapshot(tmp_path, current, invocation="retry", revision_reason="explicit retry")
    assert load_focused_snapshot(tmp_path, "retry") == current
    assert load_focused_snapshot(tmp_path, "previous") == old
    assert (original.read_bytes(), checkpoint.read_bytes()) == before

    bind_focused_snapshot(tmp_path, current, invocation="retry", resume="retry")
    with pytest.raises(ValueError, match="new invocation"):
        bind_focused_snapshot(tmp_path, old, invocation="retry", revision_reason="cannot rewrite")
    with pytest.raises(ValueError, match="new invocation"):
        bind_focused_snapshot(tmp_path, current, resume="previous", revision_reason="cannot migrate resume")
    bind_focused_snapshot(tmp_path, old, resume="previous")
    assert (original.read_bytes(), checkpoint.read_bytes()) == before


def test_first_invocation_and_legacy_resume_use_matching_receipts(tmp_path: Path) -> None:
    config = FocusedIdeaAgent().service_profile_snapshot
    bind_focused_snapshot(tmp_path, config, invocation="initial")
    assert load_focused_snapshot(tmp_path, "initial") == config
    assert load_focused_snapshot(tmp_path, "legacy") == config
    bind_focused_snapshot(tmp_path, config, resume="legacy")
    assert invocation_snapshot_path(tmp_path, "legacy").is_file()
    with pytest.raises(ValueError, match="differs from task"):
        bind_focused_snapshot(tmp_path, config, invocation="different", resume="initial")


@pytest.mark.parametrize("invocation", ["", "../escape", "/absolute", "a/b"])
def test_invocation_id_cannot_escape_run(tmp_path: Path, invocation: str) -> None:
    with pytest.raises(ValueError, match="invocation ID"):
        invocation_snapshot_path(tmp_path, invocation)


def test_missing_and_invalid_historical_receipts_fail_closed(tmp_path: Path) -> None:
    config = FocusedIdeaAgent().service_profile_snapshot
    checkpoint = tmp_path / "agent_traces/idea/old/checkpoint.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("historical checkpoint bytes")
    with pytest.raises(ValueError, match="historical run"):
        bind_focused_snapshot(tmp_path, config, invocation="new", revision_reason="retry")
    with pytest.raises(ValueError, match="historical run"):
        bind_focused_snapshot(tmp_path, config, resume="old")
    original = tmp_path / "input/idea_focused.v1.json"
    original.write_text("[]")
    with pytest.raises(ValueError, match="invalid"):
        bind_focused_snapshot(tmp_path, config, invocation="new", revision_reason="retry")
    with pytest.raises(ValueError, match="invalid"):
        load_focused_snapshot(tmp_path, "old")


@pytest.mark.asyncio
async def test_retry_builds_actual_focused_context_with_current_configuration(tmp_path: Path) -> None:
    previous = FocusedIdeaAgent(author_settings={"request_timeout_seconds": 300})
    agent = FocusedIdeaAgent()
    original = tmp_path / "input/idea_focused.v1.json"
    atomic_json(original, previous.service_profile_snapshot)
    before = original.read_bytes()
    request = RunRequest(project="pimc", user_request="检查配置更新后的重试", extra={
        "run_root": str(tmp_path), "invocation_id": "new-context", "revision_reason": "用户重试失败节点"})
    context = await agent.build_context(request)
    assert context.metadata["idea_runtime_profile"]["profile_id"] == "focused_v1"
    assert load_focused_snapshot(tmp_path, "new-context") == agent.service_profile_snapshot
    assert original.read_bytes() == before
    request.extra["resume_invocation"] = "new-context"
    resumed = await agent.build_context(request)
    assert resumed.metadata["idea_runtime_profile"] == context.metadata["idea_runtime_profile"]
