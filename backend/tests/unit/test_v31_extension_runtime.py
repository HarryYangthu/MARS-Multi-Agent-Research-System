from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

from app.bridge.extension_runtime import (
    build_extension_runtime,
    reset_extension_runtime,
)
from app.harness.project_packs.registry import ProjectPackError
from app.main import create_app
from app.settings import reset_settings_cache


def _write_pack(root: Path, *, distribution: str = "public") -> Path:
    pack = root / "demo"
    pack.mkdir(parents=True)
    (pack / "project_pack.yaml").write_text(
        "\n".join(
            [
                "schema_id: project_pack.v1",
                "project_id: demo",
                "display_name: Demo Pack",
                "pack_version: 1.0.0",
                'requires_core: \">=3.0.0,<3.1.0\"',
                f"distribution: {distribution}",
                "capabilities:",
                "  - model_discovery",
                "adapters:",
                "  evaluator:",
                "    protocol: adapter.v1",
                "    argv: ['{python}', -m, demo_adapter]",
            ]
        ),
        encoding="utf-8",
    )
    (pack / "project.yaml").write_text(
        "description: Synthetic test pack\ndomain: regression\ntags: [test]\n",
        encoding="utf-8",
    )
    (pack / "metrics.yaml").write_text("metrics: {}\n", encoding="utf-8")
    (pack / "discovery.yaml").write_text("search_space: {}\n", encoding="utf-8")
    (pack / "workflow.yaml").write_text("stages: []\n", encoding="utf-8")
    (pack / "ui_schema.json").write_text(
        json.dumps({"type": "object", "properties": {"seed": {"type": "integer"}}}),
        encoding="utf-8",
    )
    return pack


def test_v30_runtime_loads_public_pack_and_process_adapter(tmp_path: Path) -> None:
    root = tmp_path / "packs"
    _write_pack(root)

    runtime = build_extension_runtime(
        distribution="v30-core",
        pack_roots=(root,),
    )

    assert runtime.project_packs.get("demo").manifest.pack_version == "1.0.0"
    assert runtime.adapter_name("demo", "evaluator") == "demo:evaluator"
    assert runtime.adapters.names() == ("demo:evaluator",)
    adapter = runtime.adapters.get("demo:evaluator")
    assert getattr(adapter, "argv") == (sys.executable, "-m", "demo_adapter")


def test_private_pack_is_rejected_by_v30_and_accepted_by_v31(tmp_path: Path) -> None:
    root = tmp_path / "packs"
    _write_pack(root, distribution="private")

    with pytest.raises(ProjectPackError, match="private project pack"):
        build_extension_runtime(distribution="v30-core", pack_roots=(root,))

    runtime = build_extension_runtime(
        distribution="v31-wireless",
        pack_roots=(root,),
    )
    assert runtime.project_packs.get("demo").manifest.distribution == "private"


def test_configured_pack_missing_payload_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "packs"
    pack = root / "broken"
    pack.mkdir(parents=True)
    (pack / "project_pack.yaml").write_text(
        "\n".join(
            [
                "schema_id: project_pack.v1",
                "project_id: broken",
                "display_name: Broken",
                "pack_version: 1.0.0",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProjectPackError, match="missing project"):
        build_extension_runtime(distribution="v30-core", pack_roots=(root,))


def test_system_and_project_pack_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "packs"
    _write_pack(root, distribution="private")
    monkeypatch.setenv("MARS_DISTRIBUTION", "v31-wireless")
    monkeypatch.setenv("MARS_PROJECT_PACK_PATHS", str(root))
    reset_settings_cache()
    reset_extension_runtime()
    try:
        client = TestClient(create_app())

        version = client.get("/api/system/version")
        assert version.status_code == 200
        assert version.json()["distribution"] == "v31-wireless"
        assert version.json()["project_packs"][0]["project_id"] == "demo"

        projects = client.get("/api/projects")
        assert projects.status_code == 200
        demo = next(item for item in projects.json() if item["name"] == "demo")
        assert demo["compatibility_mode"] == "v31_pack"
        assert demo["capabilities"] == ["model_discovery"]

        ui_schema = client.get("/api/projects/demo/ui-schema")
        assert ui_schema.status_code == 200
        assert ui_schema.json()["properties"]["seed"]["type"] == "integer"
    finally:
        reset_settings_cache()
        reset_extension_runtime()
