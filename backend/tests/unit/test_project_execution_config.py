"""Real project execution config files and fail-closed GPU readiness."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from app.execution.remote.executor import RemoteExecutorConfig

from app.bridge import project_execution_config as service


@pytest.fixture()
def execution_file(tmp_path: Path) -> Path:
    root = tmp_path / "proj" / ".mars"
    root.mkdir(parents=True)
    return root / "execution.yaml"


def _gpu_fields(tmp_path: Path) -> dict[str, object]:
    key = tmp_path / "id_ed25519"
    key.write_text("KEY")
    known = tmp_path / "known_hosts"
    known.write_text("host key\n")
    return {"host": "gpu-lab.example.com", "port": "22", "user": "harry",
            "key_path": str(key), "known_hosts": str(known),
            "remote_root": "/data/mars-runs", "python": "python3", "gpu_ids": "0,1"}


def test_absent_config_means_local_simulation(execution_file: Path) -> None:
    config = service.load_execution_file(execution_file)
    assert config["device"] == "local" and "remote_gpu" not in config
    status = service.execution_status(service.load_execution_file(execution_file), runtime_backend="paper_static", remote_capable=False)
    assert status["ready"] is True and status["device"] == "local"


def test_save_remote_gpu_roundtrip_and_default_local(execution_file: Path, tmp_path: Path) -> None:
    saved = service.save_execution_file(execution_file, {"device": "remote_gpu", "remote_gpu": _gpu_fields(tmp_path)})
    assert saved["device"] == "remote_gpu"
    stored = yaml.safe_load(execution_file.read_text())
    assert stored["schema"] == "project_execution.v1" and stored["remote_gpu"]["host"] == "gpu-lab.example.com"
    assert stored["remote_gpu"]["port"] == 22
    reloaded = service.load_execution_file(execution_file)
    assert reloaded["remote_gpu"]["key_path"] == str(tmp_path / "id_ed25519")
    local = service.save_execution_file(execution_file, {"device": "local"})
    assert local["device"] == "local" and "remote_gpu" not in local


@pytest.mark.parametrize("mutation, message", [
    ({"host": "bad host!"}, "主机名"),
    ({"user": ""}, "用户名"),
    ({"port": "70000"}, "端口"),
    ({"key_path": "/nonexistent/key"}, "私钥"),
    ({"known_hosts": "/nonexistent/known"}, "known_hosts"),
    ({"remote_root": "relative/path"}, "绝对路径"),
])
def test_remote_gpu_validation_fails_closed(
    execution_file: Path, tmp_path: Path, mutation: dict[str, str], message: str
) -> None:
    fields = _gpu_fields(tmp_path) | mutation
    with pytest.raises(ValueError, match=message):
        service.save_execution_file(execution_file, {"device": "remote_gpu", "remote_gpu": fields})


def test_status_reports_missing_prerequisites_without_network(execution_file: Path, tmp_path: Path) -> None:
    fields = _gpu_fields(tmp_path)
    service.save_execution_file(execution_file, {"device": "remote_gpu", "remote_gpu": fields})
    ready = service.execution_status(service.load_execution_file(execution_file), runtime_backend="paper_static", remote_capable=False)
    assert ready["ready"] is False and ready["missing"] == []
    assert "remote_runtime_not_enabled" in ready["findings"]
    assert "remote_dispatch_not_enabled" in ready["findings"]
    # Environment drift after saving (key file removed) must surface, not hide.
    Path(str(fields["key_path"])).unlink()
    drifted = service.execution_status(service.load_execution_file(execution_file), runtime_backend="paper_static", remote_capable=False)
    assert drifted["ready"] is False
    assert "ssh_key_path_not_file" in drifted["findings"]


def test_invalid_device_and_schema_rejected(execution_file: Path) -> None:
    with pytest.raises(ValueError, match="device"):
        service.save_execution_file(execution_file, {"device": "tpu"})
    execution_file.write_text("schema: other\n")
    with pytest.raises(ValueError, match="无效"):
        service.load_execution_file(execution_file)


def test_matching_dispatcher_is_only_prerequisite_ready(execution_file: Path, tmp_path: Path) -> None:
    fields = _gpu_fields(tmp_path)
    config = service.save_execution_file(execution_file, {"device": "remote_gpu", "remote_gpu": fields})
    dispatched = RemoteExecutorConfig(enabled=True, host=str(fields["host"]), user=str(fields["user"]),
        key_path=Path(str(fields["key_path"])), known_hosts_path=Path(str(fields["known_hosts"])),
        remote_root=str(fields["remote_root"]), gpu_ids=("0", "1"))
    status = service.execution_status(config, runtime_backend="remote_gpu", remote_capable=True, dispatched=dispatched)
    assert status["ready"] and not status["connection_verified"]
    config["remote_gpu"]["host"] = "another.example.com"
    mismatch = service.execution_status(config, runtime_backend="remote_gpu", remote_capable=True, dispatched=dispatched)
    assert not mismatch["ready"] and "remote_dispatch_mismatch:host" in mismatch["findings"]
