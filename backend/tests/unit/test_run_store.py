from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.storage.run_store import RUN_SUBDIRS, RunStore


def test_create_run_makes_9_subdirs(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    handle = store.create(
        task="pimc moe ablation!",
        project="moe-pimc",
        entrypoint="cli",
        user_request="research question text",
        now=datetime(2026, 5, 4, 23, 10, tzinfo=timezone.utc),
    )
    assert handle.run_id.startswith("2026-05-04T2310_")
    assert handle.root.exists()
    for sub in RUN_SUBDIRS:
        assert (handle.root / sub).is_dir(), sub
    meta = json.loads((handle.root / "run_meta.json").read_text())
    assert meta["project"] == "moe-pimc"
    assert meta["task"] == "pimc moe ablation!"
    assert (handle.root / "input" / "user_request.md").read_text() == "research question text"


def test_list_and_get(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    h1 = store.create(task="t1", project="p", now=datetime(2026, 5, 4, 1, 0, tzinfo=timezone.utc))
    h2 = store.create(task="t2", project="p", now=datetime(2026, 5, 4, 2, 0, tzinfo=timezone.utc))
    listed = store.list()
    assert {r.run_id for r in listed} == {h1.run_id, h2.run_id}
    fetched = store.get(h1.run_id)
    assert fetched is not None and fetched.task == "t1"
    assert store.get("does-not-exist") is None


def test_write_event(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    h = store.create(task="t", project="p")
    h.write_event("agent_events", {"agent": "idea", "state": "running"})
    h.write_event("agent_events", {"agent": "idea", "state": "done"})
    line = (h.subdir("events") / "agent_events.jsonl").read_text().strip().splitlines()
    assert len(line) == 2
    assert json.loads(line[0])["state"] == "running"
