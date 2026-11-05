from pathlib import Path

import yaml

from mars_app.hpo import normalize_spec
from mars_app.launcher import Launcher, validate_contract
from mars_app.settings import Settings


def test_contract_environment_is_validated_and_saved(tmp_path: Path) -> None:
    template = {"title": "环境选择", "objective": "真实执行",
                "targets": {"gates": [{"metric": "error", "max_worse": 0}]}, "budget": {"routes": 1}}
    for value in ("auto", True, {}, None):
        contract = {**template, "execution": {"environment": value}}
        assert any("execution.environment" in error for error in validate_contract(contract))
    settings = Settings(core_root=tmp_path / "core", data_dir=tmp_path / "app")
    for environment in ("local", "remote"):
        contract = {**template, "execution": {"environment": environment}}
        assert validate_contract(contract) == []
        path = Launcher(settings).write_task(f"env-{environment}", contract, True)
        assert yaml.safe_load(path.read_text())["execution"]["environment"] == environment
        assert "execution" not in template


def test_search_keeps_environment_when_normalizing() -> None:
    spec = {"target": {"kind": "baseline", "dir": "/baseline"}, "datasets": ["a"], "steps": 8,
            "n_trials": 2, "parallel": 1, "params": [{"key": "learning_rate", "mode": "choices", "choices": [0.001]}]}
    space = {"datasets": ["a"], "keys": [{"key": "learning_rate", "type": "float", "declared": True}],
             "metrics": {"primary": "error", "fields": {"error": {"better": "lower"}}}}
    for environment in ("local", "remote"):
        cleaned, errors = normalize_spec({**spec, "environment": environment}, space, lambda _: True)
        assert errors == [] and cleaned["environment"] == environment
    _, errors = normalize_spec({**spec, "environment": "auto"}, space, lambda _: True)
    assert any("执行环境" in error for error in errors)


def test_state_changes_only_from_this_app() -> None:
    from mars_app.main import same_origin
    assert same_origin(None, "127.0.0.1:8020")  # curl, scripts
    assert same_origin("http://127.0.0.1:8020", "127.0.0.1:8020")
    assert same_origin("http://localhost:5180", "127.0.0.1:8020")  # Vite dev proxy
    assert not same_origin("https://evil.example", "127.0.0.1:8020")
    assert not same_origin("http://localhost:9999", "127.0.0.1:8020")
    assert not same_origin("null", "127.0.0.1:8020")


def test_only_loopback_host_names(monkeypatch) -> None:
    from mars_app.main import local_host
    assert local_host("127.0.0.1:8020") and local_host("localhost:5180") and local_host("[::1]:8020")
    assert not local_host("attacker.example:8020")  # DNS rebinding: Origin and Host both name the attacker
    assert not local_host(None)
    monkeypatch.setenv("MARS_APP_ALLOWED_HOSTS", "mars.lab")
    assert local_host("mars.lab:8020")
