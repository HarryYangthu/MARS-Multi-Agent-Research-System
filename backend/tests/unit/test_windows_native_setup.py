"""Native setup contracts using real files and a real backend subprocess."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request

from packaging.requirements import Requirement
import pytest
import yaml

from app.settings import _parse_env_line, repo_root

ROOT = repo_root()
_spec = importlib.util.spec_from_file_location("windows_config", ROOT / "deploy/windows-native/configure_api.py")
assert _spec is not None and _spec.loader is not None
config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(config)


@pytest.mark.parametrize("provider", ["deepseek", "custom", "local_vllm"])
def test_routing_updates_focused_and_pipeline_roles_without_changing_tools(provider: str) -> None:
    original = yaml.safe_load((ROOT / "configs/agents.yaml").read_text())
    changed = config.route_agents(original, provider=provider, model_name="chosen-model",
                                  endpoint="http://127.0.0.1:1234/v1", key_env="WINDOWS_API_KEY")
    for role in ("idea", "idea_author", "idea_reviewer", "experiment", "coding", "execution", "writing"):
        assert changed[role]["model"]["provider"] == provider
        assert changed[role]["model"]["model"] == "chosen-model"
        assert changed[role]["model"]["base_url_env"] == "MARS_WINDOWS_API_BASE_URL"
        assert changed[role].get("tools") == original[role].get("tools")
    assert original == yaml.safe_load((ROOT / "configs/agents.yaml").read_text())


def test_env_updates_are_readable_and_preserve_other_settings(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_text('# preserved\nMARS_LOG_LEVEL=DEBUG\nKEY=old\nexport KEY=duplicate\n')
    config.update_env(path, {"KEY": "configuration-fixture-only", "ENDPOINT": "http://localhost:1234/v1"})
    parsed = dict(item for line in path.read_text().splitlines() if (item := _parse_env_line(line)))
    assert parsed == {"MARS_LOG_LEVEL": "DEBUG", "KEY": "configuration-fixture-only", "ENDPOINT": "http://localhost:1234/v1"}
    assert path.read_text().count("KEY=") == 1
    assert path.read_text().startswith("# preserved\n")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        config.update_env(path, {"KEY": "bad\nNEW_FIELD=bad"})
    assert path.read_bytes() == before


@pytest.mark.parametrize("endpoint", ["file:///tmp/secret", "https://key@example.org/v1", "https://example.org/v1?key=secret", "http://a b/v1"])
def test_endpoint_rejects_embedded_secrets_and_non_http(endpoint: str) -> None:
    with pytest.raises(ValueError):
        config.validate_endpoint(endpoint)


def test_pimc_path_setup_keeps_baseline_protection_and_original_backup(tmp_path: Path) -> None:
    for relative in ("projects/pimc/repo_link.yaml", "configs/execution.yaml"):
        dest = tmp_path / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, dest)
    original = (tmp_path / "projects/pimc/repo_link.yaml").read_bytes()
    code = tmp_path / "research code 中文"
    code.mkdir()
    (code / "train_static.py").write_text("# File path fixture, not an executed training result.\n")
    data = tmp_path / "data.pth"
    data.write_bytes(b"path validation fixture, never loaded as tensor data")
    config.configure_pimc(tmp_path, code, data)
    after = yaml.safe_load((tmp_path / "projects/pimc/repo_link.yaml").read_text())
    assert after["repo_path"] == code.resolve().as_posix()
    assert after["protected_paths"] == yaml.safe_load(original)["protected_paths"]
    backup = next((tmp_path / "local/windows/config-backups").glob("*/projects/pimc/repo_link.yaml"))
    assert backup.read_bytes() == original


def test_dependency_lock_contains_all_current_runtime_and_pimc_requirements() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    packages = {p["name"] for p in lock["package"]}
    requirements = [*project["dependencies"], *project["optional-dependencies"]["pimc"], *project["optional-dependencies"]["dev"]]
    assert {Requirement(r).name.lower().replace("_", "-") for r in requirements} <= packages
    manifest = yaml.safe_load((ROOT / "configs/windows_native.yaml").read_text())
    assert manifest["torch_index"] == "https://download.pytorch.org/whl/cpu"


def test_backend_starts_and_shuts_down_gracefully_via_control_file(tmp_path: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    shutdown = tmp_path / "shutdown"
    log = tmp_path / "backend.log"
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "backend"), "MARS_MOCK_MODE": "never", "MARS_DISTRIBUTION": "v30-core"}
    with log.open("wb") as output:
        process = subprocess.Popen([sys.executable, str(ROOT / "deploy/windows-native/serve_backend.py"),
                                    "--port", str(port), "--shutdown-file", str(shutdown)],
                                   cwd=ROOT, env=environment, stdout=output, stderr=output)
    try:
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            assert process.poll() is None, log.read_text()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                    assert json.load(response)["service"] == "mars-backend"
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.2)
        else:
            pytest.fail("backend readiness timed out: " + log.read_text())
        shutdown.write_text("stop")
        assert process.wait(timeout=30) == 0
        assert "Application shutdown complete" in log.read_text()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
