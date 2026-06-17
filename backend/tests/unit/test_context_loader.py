from __future__ import annotations

import json
from pathlib import Path

from app.harness.context.loader import build_context
from app.harness.context.manifest import write as write_manifest
from app.storage.run_store import RunStore


def test_loader_renders_three_layers() -> None:
    pack = build_context(
        agent_role="idea",
        output_schema="proposal.v1",
        project="moe-pimc",
        user_request="How to simplify the router?",
        upstream_handoff={"prev": "previous version body"},
    )
    text = pack.render()
    assert "MARS" in text
    assert "Project: moe-pimc" in text
    assert "How to simplify the router?" in text
    assert "previous version body" in text


def test_manifest_writes_to_runs_dir(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = store.create(task="t", project="moe-pimc")
    pack = build_context(
        agent_role="idea",
        output_schema="proposal.v1",
        project="moe-pimc",
        user_request="x",
    )
    p = write_manifest(run_root=run.root, pack=pack, agent_name="idea")
    assert p.exists()
    assert p.parent.name == "context"
    assert p.suffix == ".json"
    payload = json.loads(p.read_text(encoding="utf-8"))
    assert payload["summary"]["source"] == "compiler"
    assert payload["compiled_manifest"]["schema"] == "context_compile_manifest.v1"
    assert payload["compiled_manifest"]["messages"]
    snap = p.parent / p.name.replace("_pack", "_snapshot").replace(".json", ".md")
    assert snap.exists()
