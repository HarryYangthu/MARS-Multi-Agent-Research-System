"""Exercise exact release selection against real Git objects and archive bytes."""
from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

from scripts.release.export_v30 import (
    ReleaseGateError,
    _extract_selection,
    _materialize_archive,
    _parse_allowlist,
    scan_export_tree,
    select_git_tree,
)

ROOT = Path(__file__).resolve().parents[3]


def _commit_files(root: Path, files: dict[str, str]) -> str:
    root.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    for arguments in (
        ("init", "-q"),
        ("config", "user.email", "release-test@example.invalid"),
        ("config", "user.name", "Release Test"),
        ("add", "."),
        ("commit", "-q", "-m", "reviewed source fixture"),
    ):
        subprocess.run(("git", *arguments), cwd=root, check=True, capture_output=True)
    return subprocess.check_output(("git", "rev-parse", "HEAD"), cwd=root, text=True).strip()


def test_exact_selection_and_archive_preserve_routes_without_unreviewed_files(tmp_path: Path) -> None:
    repo = tmp_path / "repository"
    expected = {
        "frontend/src/app/runs/[id]/page.tsx": "export default function Page() { return null; }\n",
        "backend/app/harness/schema/schemas/report.v1.json": '{"type": "object"}\n',
        "configs/skills/research.md": "Use the supplied sources.\n",
    }
    excluded = {
        ".env": "LOCAL_ONLY=private\n",
        "docs/validation/private.json": '{"local": true}\n',
        "scripts/local_debug.py": "raise RuntimeError('development only')\n",
        "frontend/tsconfig.tsbuildinfo": "generated\n",
        "backend/app/unreviewed.py": "# Must receive explicit export review.\n",
        # These would match [id] if selection used shell/fnmatch semantics.
        "frontend/src/app/runs/i/page.tsx": "unreviewed\n",
    }
    commit = _commit_files(repo, {**expected, **excluded, "manifest.txt": "\n".join(expected) + "\n"})
    # Selection and extraction must keep using committed objects after edits.
    (repo / "manifest.txt").write_text("**\n")
    (repo / next(iter(expected))).write_text("dirty workspace\n")
    selection = select_git_tree(repo, treeish=commit, allowlist_path="manifest.txt")
    assert set(selection.files) == set(expected)
    tree = tmp_path / "extracted"
    _extract_selection(repo, selection, tree)
    assert scan_export_tree(tree, selection.files) == ()
    archive_path = tmp_path / "selected-source.tar.gz"
    _materialize_archive(tree, selection.files, archive_path)
    with tarfile.open(archive_path) as archive:
        assert set(archive.getnames()) == set(expected)
        for relative, content in expected.items():
            stream = archive.extractfile(relative)
            assert stream is not None
            assert stream.read().decode() == content
    with pytest.raises(ReleaseGateError, match="overwrite"):
        _materialize_archive(tree, selection.files, archive_path)


@pytest.mark.parametrize("entry", ["**", "docs/**", "scripts/*", "backend/app/?.py"])
def test_manifest_refuses_wildcards(entry: str) -> None:
    with pytest.raises(ReleaseGateError, match="exact file"):
        _parse_allowlist(entry)


@pytest.mark.parametrize("entry", [
    ".env", "frontend/.env.local", "runs/run-1/input.json", "knowledge/literature/paper.txt",
    "workspace/repos/research.py", "local/config.yaml", "desktop/node_modules/pkg/index.js",
    "frontend/tsconfig.tsbuildinfo", "backend/app/__pycache__/main.pyc", "secrets/api.yaml",
    "keys/id_ed25519", "config/client.pem",
])
def test_explicit_entries_cannot_opt_private_state_or_caches_into_export(entry: str) -> None:
    with pytest.raises(ReleaseGateError, match="private or generated"):
        _parse_allowlist(entry)


@pytest.mark.parametrize("entry", ["./safe.txt", "a/../safe.txt", "a//safe.txt", "a/", "/safe.txt"])
def test_manifest_paths_must_be_normalized(entry: str) -> None:
    with pytest.raises(ReleaseGateError, match="normalized relative"):
        _parse_allowlist(entry)


def test_missing_exact_asset_and_duplicate_entry_fail_closed(tmp_path: Path) -> None:
    repo = tmp_path / "repository"
    commit = _commit_files(repo, {"safe.txt": "safe\n", "manifest.txt": "safe.txt\nmissing.json\n"})
    with pytest.raises(ReleaseGateError, match="missing files: missing.json"):
        select_git_tree(repo, treeish=commit, allowlist_path="manifest.txt")
    with pytest.raises(ReleaseGateError, match="duplicate"):
        _parse_allowlist("safe.txt\nsafe.txt\n")


def test_source_manifest_preserves_dynamic_resources_and_windows_callers() -> None:
    public = set(_parse_allowlist((ROOT / "scripts/release/v30_tree_allowlist.txt").read_text()))
    seed = set(_parse_allowlist((ROOT / "scripts/release/runtime_assets.txt").read_text()))
    assert seed <= public
    assert all((ROOT / relative).is_file() for relative in public)
    # Runtime context loading walks these directories dynamically; import-only
    # dependency analysis would silently drop the files below.
    dynamic = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "backend/app").rglob("*")
        if path.suffix in {".md", ".json"}
    }
    dynamic.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "templates/artifacts").glob("*.md"))
    assert dynamic <= seed
    registry = yaml.safe_load((ROOT / "configs/skills.yaml").read_text())
    for skill in registry["skills"].values():
        assert (Path("configs") / skill["instructions"]).as_posix() in seed
    for path in (
        "MARS-Windows.cmd", "install-mars-windows.cmd", "configure-mars-api.cmd",
        "start-mars-windows-native.cmd", "stop-mars-windows-native.cmd",
        "deploy/windows-native/Mars.ps1", "deploy/windows-native/Common.ps1",
        "deploy/windows-native/configure_api.py", "deploy/windows-native/serve_backend.py",
        "deploy/windows-native/test_api.py", "configs/windows_native.yaml",
        "frontend/scripts/clean-next-types.cjs", "frontend/package-lock.json",
    ):
        assert path in public
    assert "frontend/tsconfig.tsbuildinfo" not in public
    assert not any(path.startswith(("backend/tests/", "docs/validation/", "configs/evaluation/")) for path in public)
