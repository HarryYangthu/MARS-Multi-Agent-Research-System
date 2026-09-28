"""Real Markdown saves, project discovery and immutable context snapshots."""
from pathlib import Path

import pytest
import yaml

from app.bridge.project_onboarding import save_background
from app.harness.context.folder_context import discover_folder_context
from app.harness.project_workspace import FolderProject, open_folder


def project(tmp_path: Path) -> FolderProject:
    code = tmp_path / "code"
    code.mkdir()
    (code / "baseline.py").write_text("VALUE = 1\n")
    return open_folder(str(code), registry=tmp_path / "registry.json")


def test_background_upload_is_discovered_as_reference_without_overwriting_code(tmp_path: Path) -> None:
    folder = project(tmp_path)
    before = (folder.root / "baseline.py").read_bytes()
    value = save_background(folder, "研究背景.MD", "# 背景\n研究目标与已有方法".encode())
    assert value["path"] == "context/研究背景.md" and value["role"] == "reference"
    record = discover_folder_context(folder)
    assert record["files"][0]["content"].startswith("# 背景")
    assert value["sha256"] == record["files"][0]["sha256"]
    assert (folder.root / "baseline.py").read_bytes() == before
    with pytest.raises(FileExistsError):
        save_background(folder, "研究背景.md", b"different")
    assert (folder.root / "context/研究背景.md").read_text().startswith("# 背景")
    assert save_background(folder, "AGENTS.md", b"untrusted instructions")["role"] == "reference"


@pytest.mark.parametrize("filename,data", [("../escape.md", b"x"), ("x\\escape.md", b"x"), (".hidden.md", b"x"),
    ("script.py", b"x"), ("empty.md", b" \n"), ("binary.md", b"\xff"), ("null.md", b"a\x00b"), ("oversize.md", b"x" * 4_000_001)])
def test_invalid_background_never_creates_a_file(tmp_path: Path, filename: str, data: bytes) -> None:
    folder = project(tmp_path)
    with pytest.raises(ValueError):
        save_background(folder, filename, data)
    assert not (folder.root / "context").exists()


def test_symlink_directory_cannot_redirect_upload(tmp_path: Path) -> None:
    folder = project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (folder.root / "context").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        save_background(folder, "background.md", b"reference")
    assert list(outside.iterdir()) == []


def test_excluded_context_is_rejected_without_leaving_unloaded_upload(tmp_path: Path) -> None:
    folder = project(tmp_path)
    config = folder.metadata_root / "project.yaml"
    data = yaml.safe_load(config.read_text())
    data["context_files"] = ["README.md"]
    config.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="context_files"):
        save_background(folder, "background.md", b"reference")
    assert not (folder.root / "context/background.md").exists()


def test_api_upload_preserves_bytes_and_rejects_duplicate_and_oversized_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.settings import reset_settings_cache
    # Actual environment selects a private registry; no execution substitutions.
    monkeypatch.setenv("MARS_FOLDER_PROJECTS_REGISTRY", str(tmp_path / "registry.json"))
    reset_settings_cache()
    code = tmp_path / "code"
    code.mkdir()
    try:
        with TestClient(create_app()) as client:
            opened = client.post("/api/projects/folder", json={"path": str(code)})
            assert opened.status_code == 200
            name = opened.json()["name"]
            url = f"/api/projects/{name}/background"
            content = "# 研究背景\n真实上传文件，作为参考资料。"
            response = client.post(url, params={"filename": "background.md"}, content=content.encode())
            assert response.status_code == 201, response.text
            assert response.json()["role"] == "reference"
            assert (code / "context/background.md").read_text() == content
            listing = client.get(f"/api/projects/{name}/auto-context").json()
            assert listing["files"][0]["sha256"] == response.json()["sha256"]
            preview = client.get(f"/api/projects/{name}/auto-context/document", params={"path": "context/background.md"})
            assert preview.json()["content"] == content
            assert client.post(url, params={"filename": "background.md"}, content=b"replacement").status_code == 409
            assert client.post(url, params={"filename": "large.md"}, content=b"x" * 4_000_001).status_code == 413
            assert client.post(url, params={"filename": "../escape.md"}, content=b"reference").status_code == 422
            assert (code / "context/background.md").read_text() == content
    finally:
        reset_settings_cache()


def test_created_project_can_resume_setup_without_changing_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.settings import reset_settings_cache

    monkeypatch.setenv("MARS_FOLDER_PROJECTS_REGISTRY", str(tmp_path / "registry.json"))
    reset_settings_cache()
    folder = tmp_path / "new-research"
    try:
        with TestClient(create_app()) as client:
            created = client.post("/api/projects/folder", json={"path": str(folder), "create": True})
            assert created.status_code == 200, created.text
            name = created.json()["name"]
            assert folder.is_dir() and (folder / ".mars").is_dir()
            # Resume via the real folder API, as opening the project again does.
            reopened = client.post("/api/projects/folder", json={"path": str(folder)})
            assert reopened.status_code == 200 and reopened.json()["name"] == name
            content = "# 背景\n继续配置时上传的研究资料。".encode()
            uploaded = client.post(f"/api/projects/{name}/background", params={"filename": "背景.md"}, content=content)
            assert uploaded.status_code == 201
            assert (folder / "context/背景.md").read_bytes() == content
            context = client.get(f"/api/projects/{name}/auto-context").json()
            assert context["project"] == name
            assert any(row["path"] == "context/背景.md" for row in context["files"])
            other = client.post("/api/projects/folder", json={"path": str(tmp_path / "other"), "create": True})
            assert other.status_code == 200 and other.json()["name"] != name
            other_context = client.get(f"/api/projects/{other.json()['name']}/auto-context").json()
            assert all(row["path"] != "context/背景.md" for row in other_context["files"])
            assert not (tmp_path / "other/context/背景.md").exists()
    finally:
        reset_settings_cache()
