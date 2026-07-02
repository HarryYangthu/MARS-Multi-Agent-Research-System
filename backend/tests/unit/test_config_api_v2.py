from __future__ import annotations

from pathlib import Path

import pytest

from app.api import config as config_api
from app.api.config import _validate_yaml


def test_v2_config_validation_requires_top_level_key_for_new_configs() -> None:
    valid, _, errors = _validate_yaml("reporting", "reporting:\n  enabled: true\n")
    assert valid
    assert errors == []

    valid, _, errors = _validate_yaml("reporting", "enabled: true\n")
    assert not valid
    assert "top-level key 'reporting' is required" in errors


def test_agent_llm_config_view_masks_local_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "models.yaml").write_text(
        "providers:\n"
        "  deepseek:\n"
        "    api_key_env: DEEPSEEK_API_KEY\n"
        "    base_url: https://api.deepseek.com/v1\n",
        encoding="utf-8",
    )
    (config_dir / "agents.yaml").write_text(
        "idea:\n"
        "  enabled: true\n"
        "  model:\n"
        "    provider: deepseek\n"
        "    model: deepseek-chat\n"
        "    api_key_env: IDEA_DEEPSEEK_API_KEY\n"
        "    temperature: 0.3\n"
        "    max_tokens: 4096\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config_api, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        config_api,
        "env_or_local",
        lambda name: "sk-test-secret" if name == "IDEA_DEEPSEEK_API_KEY" else "",
    )

    view = config_api._read_agent_llm_config()

    assert view.agents[0].agent == "idea"
    assert view.agents[0].api_key_env == "IDEA_DEEPSEEK_API_KEY"
    assert view.agents[0].api_key_configured is True
    assert "sk-test-secret" not in view.model_dump_json()


def test_write_local_env_values_preserves_existing_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = tmp_path / ".env.local"
    env_path.write_text("# local secrets\nOLD_KEY=old\nIDEA_KEY=old-value\n", encoding="utf-8")
    monkeypatch.setattr(config_api, "repo_root", lambda: tmp_path)

    config_api._write_local_env_values(
        {
            "IDEA_KEY": "new-value",
            "SPACED_KEY": "value with spaces",
        }
    )

    text = env_path.read_text(encoding="utf-8")
    assert "# local secrets" in text
    assert "OLD_KEY=old" in text
    assert "IDEA_KEY=new-value" in text
    assert 'SPACED_KEY="value with spaces"' in text
