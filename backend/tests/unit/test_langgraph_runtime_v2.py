from __future__ import annotations

import json
from pathlib import Path

from app.bridge.langgraph_runtime import LangGraphRuntimeFacade
from app.bridge.workflow_service import build_pipeline
from app.storage.run_store import RunStore


def test_langgraph_runtime_manifest_preserves_legacy_graph_shape(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="langgraph", project="pimc")
    graph = build_pipeline("pipeline")
    facade = LangGraphRuntimeFacade()

    result = facade.compile(graph)
    manifest = facade.write_manifest(run=run, graph=graph)

    assert set(result.nodes) == {"idea", "experiment", "coding", "execution", "writing"}
    assert manifest["legacy_state_compat"] is True
    path = run.subdir("context") / "langgraph_runtime.v2.json"
    assert path.exists()
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["checkpoint_namespace"].endswith(run.run_id)

