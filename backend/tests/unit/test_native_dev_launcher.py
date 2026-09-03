from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_native_development_launcher_is_v31_and_docker_free() -> None:
    launcher = REPO_ROOT / "scripts" / "dev.sh"
    root_entrypoint = REPO_ROOT / "start-mars.sh"

    assert launcher.is_file()
    assert root_entrypoint.is_file()

    source = launcher.read_text(encoding="utf-8")
    assert 'MARS_DISTRIBUTION="v31-wireless"' in source
    assert "MARS_PROJECT_PACK_PATHS" in source
    assert 'REDIS_URL=""' in source
    assert "MARS_EXECUTION_DEVICE" in source
    assert "MARS_INSTALL_STATIC_CPU" in source
    assert 'MARS_OVERLAY_INSTALL="$MARS_OVERLAY[static]"' in source
    assert '"$MARS_ROOT/projects/synthetic_regression"' in source
    assert "pip install" in source
    assert "python -m uvicorn app.main:app" in source
    assert '--reload-dir "$MARS_ROOT/backend"' in source
    assert '--reload-dir "$MARS_OVERLAY/src"' in source
    assert "npm run dev" in source
    assert "docker compose" not in source.lower()
    assert "docker run" not in source.lower()
    assert "redis-server" not in source.lower()


def test_native_development_launcher_checks_required_tool_versions() -> None:
    source = (REPO_ROOT / "scripts" / "dev.sh").read_text(encoding="utf-8")

    assert "Python 3.11 is required" in source
    assert "Node.js 20+ is required" in source
    assert "mars_v31_wireless" in source
    assert "project_packs/pimc/project_pack.yaml" in source


def test_native_windows_launcher_installs_isolated_adapter_packages() -> None:
    common = (
        REPO_ROOT / "deploy" / "windows-native" / "Common.ps1"
    ).read_text(encoding="utf-8-sig")
    exporter = (
        REPO_ROOT
        / "deploy"
        / "windows-native"
        / "Export-MarsNativeDependencies.ps1"
    ).read_text(encoding="utf-8-sig")

    for package in ("mars-v31-wireless", "mars-synthetic-regression-adapter"):
        assert package in common
    assert "MARS_INSTALL_STATIC_CPU" in common
    assert "$overlayPackage" in exporter
    assert "$syntheticRoot" in exporter
