from __future__ import annotations

from pathlib import Path

import pytest

from app import settings


def test_read_local_env_vars_ignores_non_file_mount(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_directory = tmp_path / ".env.local"
    env_directory.mkdir()
    monkeypatch.setattr(settings, "LOCAL_ENV_FILES", (env_directory,))

    assert settings.read_local_env_vars() == {}


def test_fresh_runtime_enables_public_literature_without_environment_file(tmp_path: Path) -> None:
    configured = settings.Settings(_env_file=tmp_path / "missing.env")  # type: ignore[call-arg]
    assert configured.mars_enable_network_tools
    assert "arxiv.org" in configured.mars_web_search_allowlist.split(",")
    assert "openaccess.thecvf.com" in configured.mars_web_search_allowlist.split(",")


def test_offline_and_custom_source_policy_remain_explicit_env_overrides(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text("MARS_ENABLE_NETWORK_TOOLS=false\nMARS_WEB_SEARCH_ALLOWLIST=openreview.net\n")
    configured = settings.Settings(_env_file=env_file)  # type: ignore[call-arg]
    assert not configured.mars_enable_network_tools
    assert configured.mars_web_search_allowlist == "openreview.net"
