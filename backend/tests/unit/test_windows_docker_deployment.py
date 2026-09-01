from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
WINDOWS_DEPLOY = REPO_ROOT / "deploy" / "windows"


def _yaml_mapping(name: str) -> dict[str, Any]:
    payload = yaml.safe_load((WINDOWS_DEPLOY / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


def _service(document: dict[str, Any], name: str) -> dict[str, Any]:
    services = document.get("services")
    assert isinstance(services, dict)
    service = services.get(name)
    assert isinstance(service, dict)
    return cast(dict[str, Any], service)


def test_windows_compose_is_cpu_first_and_persists_linux_state() -> None:
    document = _yaml_mapping("compose.yaml")
    backend = _service(document, "backend")
    frontend = _service(document, "frontend")
    redis = _service(document, "redis")

    environment = backend["environment"]
    assert environment["MARS_EXECUTION_DEVICE"] == "${MARS_EXECUTION_DEVICE:-cpu}"
    assert environment["MARS_DISTRIBUTION"] == "v31-wireless"
    assert environment["MARS_PROJECT_PACK_PATHS"] == "/opt/mars-v31-wireless/project_packs"
    assert backend["read_only"] is True
    assert frontend["read_only"] is True
    assert redis["user"] == "999:1000"
    assert backend["cap_drop"] == ["ALL"]
    assert frontend["cap_drop"] == ["ALL"]
    assert redis["cap_drop"] == ["ALL"]

    backend_volumes = backend["volumes"]
    assert "mars_runs:/app/runs" in backend_volumes
    assert "mars_workspace:/app/workspace" in backend_volumes
    assert "mars_knowledge:/app/knowledge" in backend_volumes
    assert "mars_runtime:/app/runtime" in backend_volumes
    assert all(
        not (isinstance(item, dict) and item.get("target") == "/app/.env.local")
        for item in backend_volumes
    )
    overlay_mounts = [
        item
        for item in backend_volumes
        if isinstance(item, dict) and "MARS_V31_OVERLAY_PATH" in str(item.get("source"))
    ]
    assert len(overlay_mounts) == 2
    assert all(item["read_only"] is True for item in overlay_mounts)
    assert all(item["bind"]["create_host_path"] is False for item in overlay_mounts)
    repo_link_mount = next(
        item for item in backend_volumes
        if isinstance(item, dict) and item.get("target") == "/app/projects/pimc/repo_link.yaml"
    )
    assert repo_link_mount["source"] == "./repo_link.demo.yaml"
    assert repo_link_mount["read_only"] is True

    assert backend["ports"] == ["127.0.0.1:${MARS_BACKEND_PORT:-8000}:8000"]
    assert frontend["ports"] == ["127.0.0.1:${MARS_FRONTEND_PORT:-3001}:3000"]
    assert "ports" not in redis


def test_production_overlay_requires_read_only_external_pimc_mounts() -> None:
    document = _yaml_mapping("compose.production.yaml")
    backend = _service(document, "backend")

    assert backend["environment"]["MARS_RUNTIME_MODE"] == "production"
    assert backend["environment"]["MARS_MOCK_MODE"] == "never"
    assert backend["environment"]["MARS_EXECUTION_BACKEND"] == "local_command"
    mounts = backend["volumes"]
    assert {item["target"] for item in mounts} == {
        "/mnt/pimc-repository",
        "/mnt/pimc-data",
        "/app/projects/pimc/repo_link.yaml",
    }
    assert all(item["read_only"] is True for item in mounts)
    assert all(item["bind"]["create_host_path"] is False for item in mounts)
    repo_link_mount = next(
        item for item in mounts if item["target"] == "/app/projects/pimc/repo_link.yaml"
    )
    assert repo_link_mount["source"] == "./repo_link.production.yaml"


@pytest.mark.parametrize(
    ("filename", "expected_path"),
    [
        ("repo_link.demo.yaml", "/app/workspace/repos/pimc-stub"),
        ("repo_link.production.yaml", "/mnt/pimc-repository"),
    ],
)
def test_container_repo_links_keep_all_baseline_protections(
    filename: str, expected_path: str
) -> None:
    original = yaml.safe_load(
        (REPO_ROOT / "projects" / "pimc" / "repo_link.yaml").read_text(encoding="utf-8")
    )
    descriptor = _yaml_mapping(filename)
    assert descriptor["repo_path"] == expected_path
    assert descriptor["read_only"] is True
    for name in ("project", "allowed_paths", "protected_paths", "ignore_patterns", "baseline_rules_file"):
        assert descriptor[name] == original[name]


def test_windows_images_run_as_non_root_and_have_health_checks() -> None:
    backend = (WINDOWS_DEPLOY / "Dockerfile.backend").read_text(encoding="utf-8")
    frontend = (WINDOWS_DEPLOY / "Dockerfile.frontend").read_text(encoding="utf-8")

    assert "USER mars" in backend
    assert "USER node" in frontend
    assert "HEALTHCHECK" in backend
    assert "HEALTHCHECK" in frontend
    assert "torch-${MARS_TORCH_VERSION}%2Bcpu" in backend
    assert "MARS_TORCH_WHEEL_BASE_URL" in backend
    assert "04cd8b002c03dd6a246fbb4ae5abf1edd42adf0a9929ad82162c973e5737b5ac" in backend
    assert "6d1b61e53a2c000e1e5cc49fc88aebc665bdf02c63910c243116d395d7cbc164" in backend
    assert "sha256sum --check --strict" in backend
    assert "uv export" in backend
    assert "--frozen" in backend
    assert "COPY --chown=mars:mars workspace/repos/pimc-stub" in backend
    assert "ln -s runtime/.env.local /app/.env.local" in backend
    assert "/app/.next/standalone" in frontend
    assert "COPY --from=builder --chown=node:node /app/node_modules" not in frontend
    assert "https://registry.npmjs.org" in frontend
    assert "${MARS_NPM_REGISTRY%/}" in frontend
    assert "HOSTNAME=0.0.0.0" in frontend
    assert "PORT=3000" in frontend


def test_one_click_scripts_do_not_bypass_windows_execution_policy() -> None:
    command_files = sorted(WINDOWS_DEPLOY.glob("*.cmd"))
    assert command_files
    for path in command_files:
        text = path.read_text(encoding="utf-8")
        assert "ExecutionPolicy" not in text
        assert "powershell.exe -NoLogo -NoProfile -File" in text
        assert 'set "MARS_EXIT_CODE=%ERRORLEVEL%"' in text
        assert "exit /b %MARS_EXIT_CODE%" in text

    powershell_files = sorted(WINDOWS_DEPLOY.glob("*.ps1"))
    assert powershell_files
    assert all(path.read_bytes().startswith(b"\xef\xbb\xbf") for path in powershell_files)

    start_script = (WINDOWS_DEPLOY / "Start-Mars.ps1").read_text(encoding="utf-8")
    common_script = (WINDOWS_DEPLOY / "Common.ps1").read_text(encoding="utf-8")
    assert '"--no-build", "--pull", "never"' in start_script
    assert "Resolve-MarsImageArchive -Path $ImageArchive" in start_script
    assert "Get-FileHash -Algorithm SHA256" in common_script
    assert "离线镜像包缺少 SHA256 文件" in common_script
    assert "[0-9a-fA-F]{64}" in common_script
    assert "$Context.FrontendPort -ne 3001" in common_script
    assert "$Context.BackendPort -ne 8000" in common_script
    assert "Assert-MarsImagePlatform" in start_script
    assert '$expectedPlatform -ne "linux/amd64"' in start_script
    assert "Assert-MarsReadiness -Context $context" in start_script
    assert start_script.index("Assert-MarsReadiness -Context") < start_script.index("MARS V3.1 已启动")
    assert '"execution_backend", "checks"' in common_script
    assert "$Readiness.ready -isnot [bool]" in common_script

    export_script = (WINDOWS_DEPLOY / "Export-MarsImages.ps1").read_text(
        encoding="utf-8"
    )
    assert '"buildx", "build", "--platform", $targetPlatform' in export_script
    assert "Assert-MarsImagePlatform" in export_script
    assert "Assert-MarsOfflinePorts -Context $context" in export_script
    assert "[switch]$Force" in export_script
    assert '"save", "--output", $partialArchive' in export_script
    assert "Move-Item -LiteralPath $partialArchive" in export_script

    assert (REPO_ROOT / "start-mars-windows.cmd").is_file()
    assert (REPO_ROOT / "start-mars-windows-offline.cmd").is_file()
    assert (REPO_ROOT / "start-mars-windows-production.cmd").is_file()
    assert (REPO_ROOT / "start-mars-windows-production-offline.cmd").is_file()
    offline_production = (WINDOWS_DEPLOY / "start-mars-production-offline.cmd").read_text()
    assert "-Production -Offline" in offline_production
    assert (REPO_ROOT / "status-mars-windows.cmd").is_file()
    assert (REPO_ROOT / "stop-mars-windows.cmd").is_file()


def test_example_environment_never_contains_a_secret() -> None:
    example = (WINDOWS_DEPLOY / "windows.env.example").read_text(encoding="utf-8")
    assignments = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in example.splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    # A blank Compose environment value would mask UI-saved credentials after
    # restart. No default assignment is allowed, not even an empty one.
    assert "DEEPSEEK_API_KEY" not in assignments
    assert "# DEEPSEEK_API_KEY=" in example
    assert assignments["MARS_EXECUTION_DEVICE"] == "cpu"
    assert assignments["PIMC_REPO_HOST_PATH"] == ""
    assert assignments["PIMC_DATA_HOST_PATH"] == ""

    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "**/.env" in dockerignore
    assert "**/.env.*" in dockerignore
    assert "deploy/windows/runtime" in dockerignore
    assert "deploy/windows/images" in dockerignore


def test_frontend_release_forces_the_patched_postcss_runtime() -> None:
    package = json.loads((REPO_ROOT / "frontend" / "package.json").read_text())
    assert package["devDependencies"]["postcss"] == "8.5.26"
    assert package["overrides"]["postcss"] == "$postcss"


@pytest.mark.parametrize("production", [False, True])
def test_compose_resolves_container_paths_without_a_docker_daemon(
    tmp_path: Path, production: bool
) -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose CLI is not installed; no daemon is required")
    probe = subprocess.run(
        [docker, "compose", "version"], capture_output=True, text=True, timeout=15
    )
    if probe.returncode != 0:
        pytest.skip("Docker Compose v2 CLI is not installed")

    # Never resolve the developer's real .env or forward their deployment
    # settings. A path containing spaces exercises Windows-style bundle names.
    deployment = tmp_path / "Windows deployment fixture"
    deployment.mkdir()
    for name in ("compose.yaml", "compose.production.yaml"):
        shutil.copyfile(WINDOWS_DEPLOY / name, deployment / name)
    shutil.copyfile(WINDOWS_DEPLOY / "windows.env.example", deployment / ".env")
    process_env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("MARS_", "PIMC_", "COMPOSE_"))
    }
    process_env.update({
        "MARS_V31_OVERLAY_PATH": str(tmp_path / "overlay fixture"),
        "PIMC_REPO_HOST_PATH": str(tmp_path / "research repository fixture"),
        "PIMC_DATA_HOST_PATH": str(tmp_path / "research data fixture"),
        "DEEPSEEK_API_KEY": "test-only-host-value-must-not-be-forwarded",
    })
    command = [
        docker, "compose", "--project-directory", str(deployment),
        "--env-file", str(deployment / ".env"), "-f", str(deployment / "compose.yaml"),
    ]
    if production:
        command.extend(["-f", str(deployment / "compose.production.yaml")])
    command.extend(["config", "--format", "json"])
    result = subprocess.run(
        command, check=True, capture_output=True, text=True, env=process_env, timeout=30
    )
    document = json.loads(result.stdout)
    backend = document["services"]["backend"]
    environment = backend["environment"]
    assert "DEEPSEEK_API_KEY" not in environment
    assert environment["MARS_EXECUTION_DEVICE"] == "cpu"
    assert environment["MARS_RUNTIME_MODE"] == ("production" if production else "development")
    assert environment["MARS_MOCK_MODE"] == ("never" if production else "auto")
    repo_links = [
        mount for mount in backend["volumes"]
        if mount["target"] == "/app/projects/pimc/repo_link.yaml"
    ]
    assert len(repo_links) == 1
    expected_name = "repo_link.production.yaml" if production else "repo_link.demo.yaml"
    assert repo_links[0]["source"] == str(deployment / expected_name)
    assert repo_links[0]["read_only"] is True
    assert repo_links[0]["bind"]["create_host_path"] is False
    for service in document["services"].values():
        assert service["platform"] == "linux/amd64"
    if production:
        assert environment["MARS_EXECUTION_BACKEND"] == "local_command"
        assert environment["PIMC_REPO_ROOT"] == "/mnt/pimc-repository"
        assert environment["PIMC_DATA_ROOT"] == "/mnt/pimc-data"


def test_runtime_key_survives_a_fresh_settings_load_through_the_volume_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import settings
    from app.api import config

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    local_env = tmp_path / ".env.local"
    local_env.symlink_to("runtime/.env.local")
    monkeypatch.setattr(config, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(settings, "LOCAL_ENV_FILES", (local_env,))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    config._write_local_env_values({"DEEPSEEK_API_KEY": "test-only-persisted-key"})

    assert local_env.is_symlink()
    assert (runtime / ".env.local").is_file()
    # No process key is set: this models a fresh backend process, not the
    # same-process set_runtime_env path used immediately after saving in the UI.
    assert settings.env_or_local("DEEPSEEK_API_KEY") == "test-only-persisted-key"
    monkeypatch.setattr(
        settings.Settings,
        "model_config",
        {**settings.Settings.model_config, "env_file": str(local_env)},
    )
    fresh_settings = settings.Settings()
    assert fresh_settings.deepseek_api_key == "test-only-persisted-key"


def test_windows_ci_exercises_both_powershell_runtimes() -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    job = workflow["jobs"]["windows-deployment-scripts"]
    assert job["runs-on"] == "windows-latest"
    assert set(job["strategy"]["matrix"]["shell"]) == {"powershell", "pwsh"}
    script = WINDOWS_DEPLOY / "tests" / "Test-DeploymentScripts.ps1"
    assert script.read_bytes().startswith(b"\xef\xbb\xbf")
    source = script.read_text(encoding="utf-8")
    assert "TEST DOUBLES" in source
    assert "Assert-MarsReadiness" in source
    assert "Resolve-MarsImageArchive" in source
