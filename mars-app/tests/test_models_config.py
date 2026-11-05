from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
import yaml

from mars_app import models_config as mc

TEMPLATE = """# 内网模型配置模板
env_files:
  - models.env
providers:
  intranet:
    api_base: http://gateway.example/v1
    api_key_env: MARS_INTRANET_API_KEY
roles:
  strong: {provider: intranet, model: GLM-5.x, max_tokens: 48000, timeout: 1800, stream: false,
           extra_body: {chat_template_kwargs: {enable_thinking: true}}, custom_field: keep-me}
  fast: {provider: intranet, model: Qwen-27B, max_tokens: 8000, timeout: 900, stream: false}
  coder: {provider: intranet, model: GLM-5.x, max_tokens: 16000, timeout: 900}
fallbacks: {strong: fast}
retries: 2
"""


def test_selected_provider_models_survive_settings_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "models.yaml"
    original = yaml.safe_load(TEMPLATE)
    original['providers']['intranet']['models'] = ['model1', 'model2']
    path.write_text(yaml.safe_dump(original))
    monkeypatch.setenv('MARS_MODELS_CONFIG', str(path))
    edited = json.loads(json.dumps(original))
    del edited['providers']['intranet']['models']
    edited['roles']['fast']['model'] = 'model2'
    cfg = mc.merge(original, edited)
    mc.save(path, cfg)
    view = mc.view(tmp_path)
    assert view['providers']['intranet']['models'] == ['model1', 'model2']
    assert view['roles']['fast']['model'] == 'model2'


def test_role_connections_keep_options_and_credentials_independent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "models.yaml"
    monkeypatch.setenv("MARS_MODELS_CONFIG", str(path))
    original = yaml.safe_load(TEMPLATE)
    original["providers"]["intranet"]["models"] = ["GLM-V5.2", "GLM-V5"]
    edited = {"providers": {}, "roles": {}, "fallbacks": {}, "retries": 1}
    for name, role in original["roles"].items():
        key = f"MARS_{name.upper()}_API_KEY"
        monkeypatch.delenv(key, raising=False)
        edited["providers"][name] = {"api_base": f"http://{name}.example/v1", "api_key_env": key}
        edited["roles"][name] = {**role, "provider": name}
    cfg = mc.merge(original, edited)
    mc.save(path, cfg)
    for name in edited["roles"]:
        assert cfg["providers"][name]["models"] == ["GLM-V5.2", "GLM-V5"]
        mc.set_key(path, cfg, f"MARS_{name.upper()}_API_KEY", f"{name}-initial")
    mc.set_key(path, cfg, "MARS_CODER_API_KEY", "coder-changed")
    values = mc.read_env(path.parent / "models.env")
    assert values == {"MARS_STRONG_API_KEY": "strong-initial", "MARS_FAST_API_KEY": "fast-initial",
                      "MARS_CODER_API_KEY": "coder-changed"}
    public = json.dumps(mc.view(tmp_path))
    assert not any(value in public for value in values.values())


@pytest.fixture()
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "local/models.yaml"
    path.parent.mkdir()
    path.write_text(TEMPLATE, encoding="utf-8")
    monkeypatch.setenv("MARS_MODELS_CONFIG", str(path))
    monkeypatch.delenv("MARS_INTRANET_API_KEY", raising=False)
    return path


def test_view_reads_the_configured_file_and_hides_keys(config: Path, tmp_path: Path) -> None:
    (config.parent / "models.env").write_text("MARS_INTRANET_API_KEY='sk-secret-value'\n", encoding="utf-8")
    view = mc.view(tmp_path / "core")
    assert view["editable"] is True and view["path"] == str(config.resolve())
    assert view["roles"]["strong"]["model"] == "GLM-5.x"
    assert view["keys"] == {"MARS_INTRANET_API_KEY": {"set": True, "in_file": str((config.parent / "models.env").resolve()),
                                                      "in_environment": False}}
    assert "sk-secret-value" not in json.dumps(view)


def test_view_without_env_is_read_only_core_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARS_MODELS_CONFIG", raising=False)
    (tmp_path / "core/configs").mkdir(parents=True)
    (tmp_path / "core/configs/models.yaml").write_text(TEMPLATE, encoding="utf-8")
    view = mc.view(tmp_path / "core")
    assert view["editable"] is False and view["path"].endswith("configs/models.yaml")


def test_merge_keeps_unknown_fields_and_unsets_cleared_ones(config: Path) -> None:
    original = mc.load(config)
    edited = {"providers": original["providers"], "fallbacks": {"strong": "fast"}, "retries": 3,
              "roles": {name: dict(role) for name, role in original["roles"].items()}}
    edited["roles"]["strong"].update(model="GLM-V5.2", stream=None, json_mode=False)
    merged = mc.merge(original, edited)
    strong = merged["roles"]["strong"]
    assert strong["model"] == "GLM-V5.2" and strong["json_mode"] is False and "stream" not in strong
    assert strong["custom_field"] == "keep-me" and merged["env_files"] == ["models.env"] and merged["retries"] == 3
    mc.save(config, merged)
    saved = config.read_text(encoding="utf-8")
    assert saved.startswith("# 由 mars-app") and yaml.safe_load(saved)["roles"]["strong"]["model"] == "GLM-V5.2"
    assert len(list(config.parent.glob("models.yaml.bak-*"))) == 1  # the previous version is kept


def test_validate_reports_each_problem(config: Path) -> None:
    cfg = mc.load(config)
    cfg["providers"]["intranet"].update(api_base="gateway.example", api_key_env="bad name")
    cfg["roles"]["fast"].update(provider="nope", max_tokens=0, stream="no", extra_body=[1])
    del cfg["roles"]["coder"]
    cfg["fallbacks"] = {"strong": "strong"}
    joined = "；".join(mc.validate(cfg))
    for expected in ("地址必须以 http", "Key 变量名", "缺少角色 coder", "fast：服务 nope 不存在", "max_tokens 必须是正整数",
                     "stream 只能是", "extra_body 必须是映射", "降级 strong → strong 无效"):
        assert expected in joined, expected
    with pytest.raises(ValueError):
        mc.save(config, cfg)
    assert not list(config.parent.glob("models.yaml.bak-*"))  # an invalid config is not written, nor backed up


def test_set_key_writes_replaces_and_removes(config: Path) -> None:
    cfg = mc.load(config)
    env = config.parent / "models.env"
    env.write_text("# 注释\nOTHER=1\n", encoding="utf-8")
    result = mc.set_key(config, cfg, "MARS_INTRANET_API_KEY", "sk-1")
    assert result["env_file"] == str(env.resolve()) and "sk-1" not in json.dumps(result)
    mc.set_key(config, cfg, "MARS_INTRANET_API_KEY", "sk-2")
    assert env.read_text(encoding="utf-8") == "# 注释\nOTHER=1\nMARS_INTRANET_API_KEY=sk-2\n"
    if os.name != "nt":
        assert stat.S_IMODE(env.stat().st_mode) == 0o600
    mc.set_key(config, cfg, "MARS_INTRANET_API_KEY", "")
    assert "MARS_INTRANET_API_KEY" not in mc.read_env(env)
    with pytest.raises(ValueError):
        mc.set_key(config, cfg, "PATH", "x")  # only the configured key variables can be written
    with pytest.raises(ValueError):
        mc.set_key(config, cfg, "MARS_INTRANET_API_KEY", "a\nEVIL=1")
