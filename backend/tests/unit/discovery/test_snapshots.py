from __future__ import annotations

import stat
from pathlib import Path

import pytest

from app.harness.discovery.snapshots import (
    SnapshotError,
    SnapshotPolicy,
    create_snapshot,
    materialize_candidate_workspace,
    verify_snapshot,
)


def test_snapshot_is_content_addressed_read_only_and_candidate_is_independent(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    (source / "libs").mkdir(parents=True)
    (source / "libs" / "model.py").write_text("VALUE = 1\n", encoding="utf-8")
    script = source / "train.py"
    script.write_text("print('train')\n", encoding="utf-8")
    script.chmod(0o755)
    (source / "ignored.txt").write_text("not selected\n", encoding="utf-8")
    cache = tmp_path / "cache"
    policy = SnapshotPolicy(allowed_paths=("libs/", "train.py"))

    first = create_snapshot(
        source_root=source,
        cache_root=cache,
        project="pimc",
        source_ref="working-tree:test",
        policy=policy,
    )
    second = create_snapshot(
        source_root=source,
        cache_root=cache,
        project="pimc",
        source_ref="working-tree:test",
        policy=policy,
    )

    assert first.manifest.snapshot_id == second.manifest.snapshot_id
    assert [item.path for item in first.manifest.files] == ["libs/model.py", "train.py"]
    assert stat.S_IMODE((first.root / "libs/model.py").stat().st_mode) == 0o444
    assert stat.S_IMODE((first.root / "train.py").stat().st_mode) == 0o555

    workspace = materialize_candidate_workspace(
        snapshot_root=first.root,
        workspaces_root=tmp_path / "workspaces",
        candidate_id="cand_abc123",
    )
    (workspace / "libs/model.py").write_text("VALUE = 2\n", encoding="utf-8")

    assert (source / "libs/model.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (first.root / "libs/model.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (
        materialize_candidate_workspace(
            snapshot_root=first.root,
            workspaces_root=tmp_path / "workspaces",
            candidate_id="cand_abc123",
        )
        == workspace
    )
    _make_writable(first.root)


def test_snapshot_rejects_allowed_symlink_and_private_binary(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target = source / "target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    (source / "linked.py").symlink_to(target)

    with pytest.raises(SnapshotError, match="symlink"):
        create_snapshot(
            source_root=source,
            cache_root=tmp_path / "cache-symlink",
            project="pimc",
            source_ref="test",
            policy=SnapshotPolicy(allowed_paths=("linked.py",)),
        )

    (source / "weights.pth").write_bytes(b"private")
    with pytest.raises(SnapshotError, match="forbidden"):
        create_snapshot(
            source_root=source,
            cache_root=tmp_path / "cache-private",
            project="pimc",
            source_ref="test",
            policy=SnapshotPolicy(allowed_paths=("weights.pth",)),
        )


def test_snapshot_verification_detects_tampering(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.py").write_text("VALUE = 1\n", encoding="utf-8")
    snapshot = create_snapshot(
        source_root=source,
        cache_root=tmp_path / "cache",
        project="pimc",
        source_ref="test",
        policy=SnapshotPolicy(allowed_paths=("model.py",)),
    )
    snapshot.root.chmod(0o755)
    model = snapshot.root / "model.py"
    model.chmod(0o644)
    model.write_text("VALUE = 2\n", encoding="utf-8")

    with pytest.raises(SnapshotError, match="hash mismatch"):
        verify_snapshot(snapshot.root)

    _make_writable(snapshot.root)


def test_snapshot_verification_rejects_extra_symlink_directory_and_mode_drift(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    (source / "pkg").mkdir(parents=True)
    (source / "pkg/model.py").write_text("VALUE = 1\n", encoding="utf-8")
    snapshot = create_snapshot(
        source_root=source,
        cache_root=tmp_path / "cache",
        project="pimc",
        source_ref="test",
        policy=SnapshotPolicy(allowed_paths=("pkg",)),
    )
    snapshot.root.chmod(0o755)
    outside = tmp_path / "outside"
    outside.mkdir()
    (snapshot.root / "linked-dir").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SnapshotError, match="symlink"):
        verify_snapshot(snapshot.root)

    (snapshot.root / "linked-dir").unlink()
    (snapshot.root / "pkg").chmod(0o755)
    with pytest.raises(SnapshotError, match="directory mode"):
        verify_snapshot(snapshot.root)

    _make_writable(snapshot.root)


def test_snapshot_policy_and_private_paths_fail_closed_case_insensitively(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="empty"):
        SnapshotPolicy(allowed_paths=("",))

    source = tmp_path / "source"
    source.mkdir()
    (source / ".Git").mkdir()
    (source / ".Git/config").write_text("private\n", encoding="utf-8")
    with pytest.raises(SnapshotError, match="forbidden"):
        create_snapshot(
            source_root=source,
            cache_root=tmp_path / "cache",
            project="pimc",
            source_ref="test",
            policy=SnapshotPolicy(allowed_paths=(".Git",)),
        )


@pytest.mark.parametrize(
    ("policy", "message"),
    (
        (SnapshotPolicy(allowed_paths=("pkg",), max_files=1), "max_files"),
        (
            SnapshotPolicy(allowed_paths=("pkg",), max_total_bytes=3),
            "max_total_bytes",
        ),
    ),
)
def test_snapshot_policy_bounds_total_files_and_bytes(
    tmp_path: Path,
    policy: SnapshotPolicy,
    message: str,
) -> None:
    source = tmp_path / "source"
    (source / "pkg").mkdir(parents=True)
    (source / "pkg/a.py").write_text("aa", encoding="utf-8")
    (source / "pkg/b.py").write_text("bb", encoding="utf-8")

    with pytest.raises(SnapshotError, match=message):
        create_snapshot(
            source_root=source,
            cache_root=tmp_path / "cache",
            project="pimc",
            source_ref="test",
            policy=policy,
        )


def test_candidate_workspace_rejects_unsafe_identifier(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError, match="unsafe"):
        materialize_candidate_workspace(
            snapshot_root=tmp_path / "missing",
            workspaces_root=tmp_path / "workspaces",
            candidate_id="../escape",
        )


def _make_writable(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        elif not path.is_symlink():
            path.chmod(0o644)
    root.chmod(0o755)
