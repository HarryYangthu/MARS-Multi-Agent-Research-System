"""Real Markdown saves, project discovery and immutable context snapshots."""
from pathlib import Path

import pytest
import yaml

from app.bridge.project_onboarding import bind_code_folder, save_background
from app.harness.context.folder_context import discover_folder_context
from app.harness.project_workspace import FolderProject, open_folder


def project(tmp_path: Path) -> FolderProject:
    code = tmp_path / "code"
    code.mkdir()
    (code / "baseline.py").write_text("VALUE = 1\n")
    return open_folder(str(code), registry=tmp_path / "registry.json")


def test_background_upload_becomes_readme_without_overwriting_code_or_promoting_rules(tmp_path: Path) -> None:
    folder = project(tmp_path)
    before = (folder.root / "baseline.py").read_bytes()
    value = save_background(folder, "研究背景.MD", "# 背景\n研究目标与已有方法".encode())
    assert value["path"] == "README.md" and value["role"] == "reference"
    assert value["is_template"] is False and "previous_path" not in value
    record = discover_folder_context(folder)
    assert record["files"][0]["content"].startswith("# 背景")
    assert value["sha256"] == record["files"][0]["sha256"]
    assert (folder.root / "baseline.py").read_bytes() == before
    (folder.root / "AGENTS.md").write_text("User-confirmed rules")
    saved = save_background(folder, "AGENTS.md", b"untrusted instructions")
    assert saved["role"] == "reference" and saved["path"] == "README.md"
    assert (folder.root / "AGENTS.md").read_text() == "User-confirmed rules"
    assert (folder.root / "README.md").read_text() == "untrusted instructions"
    assert (folder.root / saved["previous_path"]).read_text().startswith("# 背景")
    assert all(not row["path"].startswith(".mars/") for row in discover_folder_context(folder)["files"])


def test_txt_background_keeps_text_and_backs_up_readme_bytes(tmp_path: Path) -> None:
    folder = project(tmp_path)
    previous = b"\xef\xbb\xbf# Original\r\nContext\r\n"
    (folder.root / "README.md").write_bytes(previous)
    content = "已有工作与领域术语。\r\n不填写本次研究目标。\r\n"
    saved = save_background(folder, "补充.TXT", content.encode())
    assert saved["path"] == "README.md"
    assert (folder.root / "README.md").read_bytes() == content.encode()
    assert (folder.root / saved["previous_path"]).read_bytes() == previous
    assert save_background(folder, "补充.md", b"replacement")["path"] == "README.md"


def test_code_binding_preserves_identity_rules_and_source(tmp_path: Path) -> None:
    folder = project(tmp_path)
    link = folder.metadata_root / "repo_link.yaml"
    before = yaml.safe_load(link.read_text())
    marker = (folder.metadata_root / "project.yaml").read_bytes()
    code = tmp_path / "external-code"
    code.mkdir()
    (code / "baseline.py").write_text("VALUE = 42\n")
    assert bind_code_folder(folder, str(code)) == code
    after = yaml.safe_load(link.read_text())
    assert {k: v for k, v in after.items() if k not in {"repo_path", "repo_role", "read_only"}} == {k: v for k, v in before.items() if k not in {"repo_path", "repo_role", "read_only"}}
    assert after["repo_path"] == str(code)
    assert after["read_only"] is True and after["repo_role"] == "simulation_baseline"
    assert (folder.metadata_root / "project.yaml").read_bytes() == marker
    assert list(code.iterdir()) == [code / "baseline.py"]
    assert (code / "baseline.py").read_text() == "VALUE = 42\n"
    for invalid in ("relative/code", str(tmp_path / "absent"), str(code / "baseline.py")):
        with pytest.raises((OSError, ValueError)):
            bind_code_folder(folder, invalid)
        assert yaml.safe_load(link.read_text()) == after


def test_code_binding_rejects_redirected_metadata(tmp_path: Path) -> None:
    folder = project(tmp_path)
    link = folder.metadata_root / "repo_link.yaml"
    outside = tmp_path / "outside.yaml"
    outside.write_bytes(link.read_bytes())
    link.unlink()
    link.symlink_to(outside)
    with pytest.raises(ValueError):
        bind_code_folder(folder, str(tmp_path))
    assert yaml.safe_load(outside.read_text())["repo_path"] == ".."


@pytest.mark.parametrize("filename,data", [("../escape.md", b"x"), ("x\\escape.md", b"x"), (".hidden.md", b"x"),
    ("script.py", b"x"), ("empty.md", b" \n"), ("binary.md", b"\xff"), ("null.md", b"a\x00b"), ("oversize.md", b"x" * 4_000_001)])
def test_invalid_background_never_creates_a_file(tmp_path: Path, filename: str, data: bytes) -> None:
    folder = project(tmp_path)
    with pytest.raises(ValueError):
        save_background(folder, filename, data)
    assert not (folder.root / "README.md").exists()


@pytest.mark.parametrize("link_kind", ["file", "dangling", "history", "metadata", "hardlink"])
def test_links_cannot_redirect_upload_or_backup(tmp_path: Path, link_kind: str) -> None:
    folder = project(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("original")
    readme = folder.root / "README.md"
    if link_kind == "file":
        readme.symlink_to(outside)
    elif link_kind == "dangling":
        readme.symlink_to(tmp_path / "missing.md")
    elif link_kind == "hardlink":
        readme.hardlink_to(outside)
    else:
        readme.write_text("original")
        external = tmp_path / "outside-dir"
        external.mkdir()
        if link_kind == "history":
            (folder.metadata_root / "background-history").symlink_to(external, target_is_directory=True)
        else:
            folder.metadata_root.rename(tmp_path / "original-metadata")
            folder.metadata_root.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError):
        save_background(folder, "background.md", b"reference")
    assert outside.read_text() == "original"
    if link_kind not in {"file", "dangling", "hardlink"}:
        assert list(external.iterdir()) == []
        assert readme.read_text() == "original"


@pytest.mark.parametrize("existing", [False, True])
def test_excluded_readme_rolls_back_without_leaving_unloaded_upload(tmp_path: Path, existing: bool) -> None:
    folder = project(tmp_path)
    config = folder.metadata_root / "project.yaml"
    data = yaml.safe_load(config.read_text())
    data["context_files"] = ["AGENTS.md"]
    config.write_text(yaml.safe_dump(data))
    previous = b"\xef\xbb\xbfOriginal\r\n"
    if existing:
        (folder.root / "README.md").write_bytes(previous)
    with pytest.raises(ValueError, match="context_files"):
        save_background(folder, "background.md", b"reference")
    if existing:
        assert (folder.root / "README.md").read_bytes() == previous
    else:
        assert not (folder.root / "README.md").exists()
    assert not list(folder.metadata_root.glob("background-history/*.md"))


def test_replacement_uses_remaining_capacity_and_can_shrink_oversized_readme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.settings import reset_settings_cache

    folder = project(tmp_path)
    readme = folder.root / "README.md"
    readme.write_text("old" * 1000)
    monkeypatch.setenv("MARS_FOLDER_CONTEXT_MAX_CHARS", "1000")
    monkeypatch.setenv("MARS_FOLDER_CONTEXT_MAX_FILES", "1")
    reset_settings_cache()
    try:
        saved = save_background(folder, "background.md", b"x" * 1000)
        assert readme.read_bytes() == b"x" * 1000
        assert (folder.root / saved["previous_path"]).read_text() == "old" * 1000
        history = list(folder.metadata_root.glob("background-history/*.md"))
        with pytest.raises(ValueError, match="总长度"):
            save_background(folder, "large.md", b"x" * 1001)
        assert readme.read_bytes() == b"x" * 1000
        assert list(folder.metadata_root.glob("background-history/*.md")) == history
        (folder.root / "context").mkdir()
        (folder.root / "context/other.md").write_text("other context")
        with pytest.raises(ValueError, match="数量"):
            save_background(folder, "background.md", b"short")
        assert readme.read_bytes() == b"x" * 1000
    finally:
        reset_settings_cache()


def test_api_upload_preserves_bytes_and_backs_up_replacements_and_rejects_oversized_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
            assert (code / "README.md").read_text() == content
            listing = client.get(f"/api/projects/{name}/auto-context").json()
            assert listing["files"][0]["sha256"] == response.json()["sha256"]
            preview = client.get(f"/api/projects/{name}/auto-context/document", params={"path": "README.md"})
            assert preview.json()["content"] == content
            replaced = client.post(url, params={"filename": "background.md"}, content=b"replacement")
            assert replaced.status_code == 201
            assert (code / replaced.json()["previous_path"]).read_text() == content
            assert client.get(f"/api/projects/{name}/auto-context/document", params={"path": replaced.json()["previous_path"]}).status_code == 404
            assert client.post(url, params={"filename": "large.md"}, content=b"x" * 4_000_001).status_code == 413
            assert client.post(url, params={"filename": "../escape.md"}, content=b"reference").status_code == 422
            assert (code / "README.md").read_text() == "replacement"
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
            assert (folder / "README.md").read_bytes() == content
            context = client.get(f"/api/projects/{name}/auto-context").json()
            assert context["project"] == name
            assert any(row["path"] == "README.md" and not row["is_template"] for row in context["files"])
            other = client.post("/api/projects/folder", json={"path": str(tmp_path / "other"), "create": True})
            assert other.status_code == 200 and other.json()["name"] != name
            other_context = client.get(f"/api/projects/{other.json()['name']}/auto-context").json()
            assert all(row["path"] != "README.md" for row in other_context["files"])
            assert not (tmp_path / "other/README.md").exists()
            external = tmp_path / "existing-engineering-code"
            external.mkdir()
            (external / "baseline.py").write_text("VALUE = 42\n")
            imported = client.put(f"/api/projects/{name}/code-folder", json={"path": str(external)})
            assert imported.status_code == 200, imported.text
            assert imported.json()["name"] == name and imported.json()["repo_path"] == str(external)
            assert imported.json()["folder_path"] == str(folder)
            # Real registered code implementation resolves the imported folder.
            import asyncio
            from app.harness.tools.code import repo_reader_tool, write_file_tool
            from app.harness.tools.registry import ToolContext
            result = asyncio.run(repo_reader_tool({"path": "baseline.py"}, ToolContext("setup-check", name, "coding")))
            assert result.ok and result.output["content"] == "VALUE = 42\n"
            assert imported.json()["repo_read_only"] is True
            denied = asyncio.run(write_file_tool({"path": "baseline.py", "content": "changed"}, ToolContext("setup-check", name, "coding")))
            assert not denied.ok and "read_only" in str(denied.error)
            assert (external / "baseline.py").read_text() == "VALUE = 42\n"
            assert not (external / ".mars").exists()
            context_after = client.get(f"/api/projects/{name}/auto-context").json()
            assert context_after["files"] == context["files"]
            reopened_again = client.post("/api/projects/folder", json={"path": str(folder)})
            assert reopened_again.json()["repo_path"] == str(external)
    finally:
        reset_settings_cache()
