import json
from pathlib import Path

import pytest

from app.bridge.agent_inspection import inspection_catalog, inspection_event


def write_events(root: Path, agent: str, rows: list[dict[str, object]]) -> None:
    folder = root / "agent_traces" / agent / "invocation"
    folder.mkdir(parents=True)
    (folder / "events.jsonl").write_text("\n".join(json.dumps(row) for row in rows))


def test_inspection_loads_one_real_record_and_does_not_mix_agent_inputs(tmp_path: Path) -> None:
    write_events(tmp_path, "idea", [
        {"event_seq": 1, "kind": "context_packed", "segments": [{"source": "project/AGENTS.md"}]},
        {"event_seq": 2, "kind": "model_request", "visible": [{"role": "user", "content": "goal"}]},
        {"event_seq": 3, "kind": "provider_request", "visible": {"messages": [{"role": "user", "content": "goal"}]}},
        {"event_seq": 4, "kind": "model_response", "visible": "output"},
    ])
    write_events(tmp_path, "coding", [{"event_seq": 1, "kind": "model_request", "visible": "other input"}])
    catalog = inspection_catalog(tmp_path, "idea")
    assert len(catalog["events"]) == 4
    assert len(catalog["requests"]) == 1
    assert "visible" not in catalog["requests"][0]
    detail = inspection_event(tmp_path, "idea", "idea:invocation:2")
    assert detail["prompt_source"] == "provider_request"
    assert detail["wire_payload"]["messages"][0]["content"] == "goal"
    assert detail["assembly"]["segments"][0]["source"] == "project/AGENTS.md"


def test_historical_snapshot_and_path_boundaries(tmp_path: Path) -> None:
    write_events(tmp_path, "idea", [{"event_seq": 1, "kind": "model_request", "visible": "input"}])
    detail = inspection_event(tmp_path, "idea", "idea:invocation:1")
    assert detail["wire_payload"] is None
    assert detail["prompt_source"] == "pre_provider_snapshot"
    with pytest.raises(ValueError):
        inspection_catalog(tmp_path, "../coding")
    with pytest.raises(ValueError):
        inspection_event(tmp_path, "idea", "../../secret")
    with pytest.raises(FileNotFoundError):
        inspection_event(tmp_path, "idea", "coding:invocation:1")


def test_inspection_ignores_incomplete_tail_and_external_symlink(tmp_path: Path) -> None:
    write_events(tmp_path, "idea", [{"event_seq": 1, "kind": "model_request"}])
    path = tmp_path / "agent_traces/idea/invocation/events.jsonl"
    with path.open("a") as stream:
        stream.write('\n{"event_seq": 2')
    assert len(inspection_catalog(tmp_path, "idea")["events"]) == 1

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "events.jsonl").write_text('{"event_seq": 1, "kind": "model_request"}')
    (tmp_path / "agent_traces/idea/external").symlink_to(outside)
    assert len(inspection_catalog(tmp_path, "idea")["events"]) == 1


def test_commander_reads_only_linked_conversation_and_hash_verified_payload(tmp_path: Path) -> None:
    from app.harness.context.runtime_manifest import record_manifest
    from app.harness.llm.provider_base import Message
    root = tmp_path / "runs/run_one"
    root.mkdir(parents=True)
    conversation = tmp_path / "conversations/conv_one"
    conversation.mkdir(parents=True)
    (conversation / "session.json").write_text(json.dumps({"linked_run_id": "run_one"}))
    record_manifest(conversation, agent="commander", node="conv_one", project="project",
                    messages=[Message("user", "actual retained input")], tools=(),
                    manifest={"segments": [], "budget": 100, "target": 90, "used": 10})
    catalog = inspection_catalog(root, "commander")
    assert len(catalog["requests"]) == 1
    detail = inspection_event(root, "commander", catalog["requests"][0]["id"])
    assert detail["event"]["visible"]["messages"][0]["content"] == "actual retained input"
    assert detail["prompt_source"] == "pre_provider_snapshot"
    assert inspection_catalog(tmp_path / "runs/other_run", "commander")["requests"] == []
    material = next((conversation / "context/materials").glob("*.json"))
    content = json.loads(material.read_text())
    content["message"]["content"] = '{"messages": []}'
    material.write_text(json.dumps(content))
    assert inspection_catalog(root, "commander")["requests"] == []
