from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

from app.bridge.extension_runtime import (
    build_extension_runtime,
    reset_extension_runtime,
)
from app.execution.adapters.process import ProcessAdapter
from app.harness.project_packs.registry import ProjectPackError
from app.execution.remote import RemoteExecutorConfig, RemoteProjectAdapter
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
    workspace_runs_root = tmp_path / "runs"
    workspace_runs_root.mkdir()

    runtime = build_extension_runtime(
        distribution="v30-core",
        pack_roots=(root,),
        workspace_runs_root=workspace_runs_root,
    )

    assert runtime.project_packs.get("demo").manifest.pack_version == "1.0.0"
    assert runtime.adapter_name("demo", "evaluator") == "demo:evaluator"
    assert runtime.adapters.names() == ("demo:evaluator",)
    adapter = runtime.adapters.get("demo:evaluator")
    assert isinstance(adapter, ProcessAdapter)
    assert adapter.argv == (sys.executable, "-m", "demo_adapter")
    assert adapter.workspace_resolver is not None
    assert getattr(adapter.workspace_resolver, "runs_root") == workspace_runs_root


def test_src_layout_pack_is_available_to_adapter_without_core_install(
    tmp_path: Path,
) -> None:
    root = tmp_path / "packs"
    pack = _write_pack(root)
    (pack / "src" / "demo_adapter").mkdir(parents=True)
    (pack / "src" / "demo_adapter" / "__init__.py").write_text("", encoding="utf-8")

    runtime = build_extension_runtime(distribution="v30-core", pack_roots=(root,))

    adapter = runtime.adapters.get("demo:evaluator")
    environment = getattr(adapter, "env")
    assert environment is not None
    assert environment["PYTHONPATH"].split(os.pathsep)[0] == str((pack / "src").resolve())


def test_private_overlay_src_is_explicit_and_ambient_pythonpath_is_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    overlay = tmp_path / "overlay"
    pack = _write_pack(overlay / "project_packs")
    (overlay / "src" / "demo_adapter").mkdir(parents=True)
    (overlay / "src" / "demo_adapter" / "__init__.py").write_text(
        "",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", "/ambient/untrusted")

    runtime = build_extension_runtime(
        distribution="v30-core",
        pack_roots=(pack,),
    )

    adapter = runtime.adapters.get("demo:evaluator")
    environment = getattr(adapter, "env")
    assert environment is not None
    paths = environment["PYTHONPATH"].split(os.pathsep)
    assert paths[0] == str((overlay / "src").resolve())
    assert "/ambient/untrusted" not in paths


def test_remote_gpu_backend_registers_remote_project_adapter(tmp_path: Path) -> None:
    root = tmp_path / "packs"
    _write_pack(root)
    remote_python = "/opt/mars/bin/python"
    config = RemoteExecutorConfig(
        enabled=True,
        host="gpu.example.test",
        user="mars",
        key_path=tmp_path / "id_ed25519",
        known_hosts_path=tmp_path / "known_hosts",
        remote_root="/srv/mars",
        python=remote_python,
    )
    artifact_root = tmp_path / "artifacts"
    workspace_runs_root = tmp_path / "runs"
    workspace_runs_root.mkdir()

    runtime = build_extension_runtime(
        distribution="v30-core",
        pack_roots=(root,),
        execution_backend="remote_gpu",
        remote_config=config,
        remote_artifact_root=artifact_root,
        workspace_runs_root=workspace_runs_root,
    )

    adapter = runtime.adapters.get("demo:evaluator")
    assert isinstance(adapter, RemoteProjectAdapter)
    assert adapter.trusted_adapter_argv == (
        remote_python,
        "-m",
        "demo_adapter",
    )
    assert adapter.remote_worker_python == remote_python
    assert adapter.artifact_root == artifact_root
    assert adapter.workspace_resolver is not None
    assert getattr(adapter.workspace_resolver, "runs_root") == workspace_runs_root


def test_explicit_cpu_device_overrides_legacy_remote_backend(tmp_path: Path) -> None:
    root = tmp_path / "packs"
    _write_pack(root)
    remote_config = RemoteExecutorConfig(
        enabled=False,
        host="",
        user="",
        key_path=None,
        known_hosts_path=None,
        remote_root="",
    )

    runtime = build_extension_runtime(
        distribution="v30-core",
        pack_roots=(root,),
        execution_backend="remote_gpu",
        execution_device="cpu",
        remote_config=remote_config,
    )

    adapter = runtime.adapters.get("demo:evaluator")
    assert isinstance(adapter, ProcessAdapter)
    assert adapter.argv == (sys.executable, "-m", "demo_adapter")


def test_gpu_device_maps_non_remote_backend_to_remote_adapter(tmp_path: Path) -> None:
    root = tmp_path / "packs"
    _write_pack(root)
    remote_python = "/opt/mars/bin/python"
    remote_config = RemoteExecutorConfig(
        enabled=True,
        host="gpu.example.test",
        user="mars",
        key_path=tmp_path / "id_ed25519",
        known_hosts_path=tmp_path / "known_hosts",
        remote_root="/srv/mars",
        python=remote_python,
    )

    runtime = build_extension_runtime(
        distribution="v30-core",
        pack_roots=(root,),
        execution_backend="mock",
        execution_device="gpu",
        remote_config=remote_config,
    )

    adapter = runtime.adapters.get("demo:evaluator")
    assert isinstance(adapter, RemoteProjectAdapter)
    assert adapter.remote_worker_python == remote_python


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
