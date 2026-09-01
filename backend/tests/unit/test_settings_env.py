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
