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
from app.harness.agent_loop.executor import validate_native_thinking
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.llm.provider_base import LLMConfig

ROOT = repo_root()
_spec = importlib.util.spec_from_file_location("windows_config", ROOT / "deploy/windows-native/configure_api.py")
assert _spec is not None and _spec.loader is not None
config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(config)


@pytest.mark.parametrize("provider", ["zhipu", "deepseek", "custom", "local_vllm"])
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


@pytest.mark.parametrize("provider,expected,thinking,effort", [
    ("zhipu", "glm-5.3", True, "low"),
    ("deepseek", "deepseek-v4-pro", False, None),
])
def test_blank_official_model_uses_own_defaults_for_every_role(provider: str, expected: str,
                                                             thinking: bool, effort: str | None) -> None:
    original = yaml.safe_load((ROOT / "configs/agents.yaml").read_text())
    routed = config.route_agents(original, provider=provider, model_name="", endpoint="https://example.org/v1", key_env="SETUP_KEY")
    for body in routed.values():
        if not isinstance(body, dict) or "model" not in body:
            continue
        model = body["model"]
        assert model["model"] == expected and model["provider"] == provider
        assert model["thinking"]["enabled"] is thinking
        assert model.get("reasoning_effort") == effort
        assert model["api_key_env"] == "SETUP_KEY"
        for participant in body.get("debate", {}).get("participants", []):
            assert participant["model"] == expected and participant["provider"] == provider


@pytest.mark.parametrize("provider,model,thinking", [
    ("zhipu", "glm-5.3", True), ("deepseek", "deepseek-v4-pro", False),
    ("custom", "gateway-model", False), ("local_vllm", "local-model", False),
])
def test_focused_and_cli_overrides_follow_selected_capability(provider: str, model: str, thinking: bool) -> None:
    for relative in ("configs/idea_focused.yaml", "configs/cli_research.yaml"):
        source = yaml.safe_load((ROOT / relative).read_text())
        routed = config.route_generation_settings(source, provider=provider, model_name=model)
        for role in ("author", "research_author", "generation"):
            if role in routed:
                assert routed[role]["thinking_enabled"] is thinking
        for loop in ("loop", "coding_loop"):
            if loop in routed:
                validate_native_thinking(LLMConfig(provider=provider, model=model, thinking_enabled=thinking),
                                         AgentLoopPolicy.from_mapping(routed[loop]))
        assert source == yaml.safe_load((ROOT / relative).read_text())
    if provider in {"custom", "local_vllm"}:
        with pytest.raises(ValueError, match="实际加载"):
            config.setup_model(provider, "")


def test_windows_probe_keeps_required_glm_thinking_and_bounded_output() -> None:
    spec = importlib.util.spec_from_file_location("windows_probe", ROOT / "deploy/windows-native/test_api.py")
    assert spec is not None and spec.loader is not None
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    original = LLMConfig(provider="zhipu", model="glm-5.3", thinking_enabled=False, max_tokens=32)
    selected = probe.probe_config(original)
    assert selected.thinking_enabled is True and selected.reasoning_effort == "low"
    assert selected.max_tokens == 2048 and selected.max_retries == 0
    assert selected.request_timeout_seconds == 45
    assert original.thinking_enabled is False and original.max_tokens == 32
    custom = probe.probe_config(LLMConfig(provider="custom", model="other-model", thinking_enabled=True))
    assert custom.thinking_enabled is False and custom.reasoning_effort is None


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
    settings = yaml.safe_load((tmp_path / "configs/execution.yaml").read_text())["execution"]["paper_static"]
    assert settings["enabled"] is True
    assert settings["data_path"] == data.resolve().as_posix()
    assert settings["python"] == Path(sys.executable).resolve().as_posix()
    config.configure_pimc(tmp_path, code)
    incomplete = yaml.safe_load((tmp_path / "configs/execution.yaml").read_text())["execution"]["paper_static"]
    assert incomplete["enabled"] is False and incomplete["data_path"] == ""


@pytest.mark.parametrize("missing", ["code", "data", "python"])
def test_pimc_invalid_paths_cannot_enable_or_partially_update_configuration(tmp_path: Path, missing: str) -> None:
    for relative in ("projects/pimc/repo_link.yaml", "configs/execution.yaml"):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    before = {relative: (tmp_path / relative).read_bytes()
              for relative in ("projects/pimc/repo_link.yaml", "configs/execution.yaml")}
    code = tmp_path / "code"
    code.mkdir()
    (code / "train_static.py").write_text("# existing path, no claimed training execution\n")
    data = tmp_path / "samples.pth"
    data.write_bytes(b"explicit path fixture, not tensor data")
    interpreter = Path(sys.executable)
    if missing == "code":
        code = tmp_path / "missing-code"
    elif missing == "data":
        data = tmp_path / "missing-data"
    else:
        interpreter = tmp_path / "missing-python"
    with pytest.raises(ValueError):
        config.configure_pimc(tmp_path, code, data, python_executable=interpreter)
    assert all((tmp_path / relative).read_bytes() == value for relative, value in before.items())


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
