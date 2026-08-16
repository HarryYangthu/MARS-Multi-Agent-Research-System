from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.release.export_v30 import ReleaseGateError, audit_release
from scripts.release.gitleaks_wrapper import GitleaksResult


def test_export_reads_committed_tree_and_allowlist_not_dirty_workspace(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    (repo / "safe.txt").write_text("committed public text\n", encoding="utf-8")
    (repo / "allowlist.txt").write_text("allowlist.txt\nsafe.txt\n", encoding="utf-8")
    commit = _commit(repo, "safe")
    baseline = audit_release(
        repo=repo,
        treeish=commit,
        allowlist_path="allowlist.txt",
        scan_history=False,
        gitleaks_runner=_passing_gitleaks,
    )
    (repo / "safe.txt").write_text("dirty " + "P" + "IMC" + " text\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("not selected\n", encoding="utf-8")
    (repo / "allowlist.txt").write_text("**\n", encoding="utf-8")

    dirty = audit_release(
        repo=repo,
        treeish=commit,
        allowlist_path="allowlist.txt",
        scan_history=False,
        gitleaks_runner=_passing_gitleaks,
    )

    assert baseline.decision == dirty.decision == "pass"
    assert baseline.tree_hash == dirty.tree_hash
    assert dirty.selected_files == ("allowlist.txt", "safe.txt")
    assert dirty.source_mode == "resolved_git_commit_objects_only"


def test_tree_and_history_denylist_findings_are_independent(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    marker = "D" + "PD"
    (repo / "allowlist.txt").write_text("allowlist.txt\npublic.txt\n", encoding="utf-8")
    (repo / "public.txt").write_text(f"legacy {marker}\n", encoding="utf-8")
    _commit(repo, "legacy")
    (repo / "public.txt").write_text("sanitized\n", encoding="utf-8")
    commit = _commit(repo, "sanitized")

    tree_only = audit_release(
        repo=repo,
        treeish=commit,
        allowlist_path="allowlist.txt",
        scan_history=False,
        gitleaks_runner=_passing_gitleaks,
    )
    with_history = audit_release(
        repo=repo,
        treeish=commit,
        allowlist_path="allowlist.txt",
        scan_history=True,
        gitleaks_runner=_passing_gitleaks,
    )

    assert tree_only.decision == "pass"
    assert with_history.decision == "blocked"
    assert any(
        finding.scope == "history" and finding.rule == "deny_term"
        for finding in with_history.findings
    )


def test_absolute_user_path_binary_internal_doc_and_docker_context_block(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    (repo / "docs").mkdir()
    (repo / "allowlist.txt").write_text(
        "allowlist.txt\nDockerfile\nconfig.txt\ndocs/**\nmodel.pdf\n",
        encoding="utf-8",
    )
    (repo / "Dockerfile").write_text("FROM scratch\nCOPY . /app\n", encoding="utf-8")
    absolute = "/" + "Users" + "/researcher/private/config"
    (repo / "config.txt").write_text(absolute + "\n", encoding="utf-8")
    (repo / "docs" / "implementation_report.md").write_text("notes\n", encoding="utf-8")
    (repo / "model.pdf").write_bytes(b"%PDF-1.4\x00")
    commit = _commit(repo, "unsafe")

    audit = audit_release(
        repo=repo,
        treeish=commit,
        allowlist_path="allowlist.txt",
        scan_history=False,
        gitleaks_runner=_passing_gitleaks,
    )

    rules = {finding.rule for finding in audit.findings}
    assert audit.decision == "blocked"
    assert {
        "absolute_user_path",
        "binary_extension",
        "dockerignore_missing",
        "internal_path",
    }.issubset(rules)


def test_archive_symlink_and_missing_gitleaks_fail_closed(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "safe.txt").write_text("safe\n", encoding="utf-8")
    (repo / "link.txt").symlink_to("safe.txt")
    (repo / "allowlist.txt").write_text(
        "allowlist.txt\nlink.txt\nsafe.txt\n", encoding="utf-8"
    )
    commit = _commit(repo, "symlink")

    with pytest.raises(ReleaseGateError, match="regular file"):
        audit_release(
            repo=repo,
            treeish=commit,
            allowlist_path="allowlist.txt",
            scan_history=False,
            gitleaks_runner=_passing_gitleaks,
        )

    repo_two = _repository(tmp_path / "second")
    (repo_two / "safe.txt").write_text("safe\n", encoding="utf-8")
    (repo_two / "allowlist.txt").write_text(
        "allowlist.txt\nsafe.txt\n", encoding="utf-8"
    )
    second_commit = _commit(repo_two, "safe")
    audit = audit_release(
        repo=repo_two,
        treeish=second_commit,
        allowlist_path="allowlist.txt",
        scan_history=False,
        gitleaks_runner=_missing_gitleaks,
    )

    assert audit.decision == "blocked"
    assert any(finding.rule == "gitleaks" for finding in audit.findings)


def _repository(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "release-test@example.invalid")
    _git(root, "config", "user.name", "Release Test")
    return root


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def _git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _passing_gitleaks(_repo: Path, _treeish: str, report: Path) -> GitleaksResult:
    report.write_text("[]\n", encoding="utf-8")
    return GitleaksResult(
        status="passed",
        returncode=0,
        report_path=str(report),
        command=("gitleaks-test-double",),
    )


def _missing_gitleaks(_repo: Path, _treeish: str, report: Path) -> GitleaksResult:
    return GitleaksResult(
        status="missing",
        returncode=127,
        report_path=str(report),
        command=(),
        detail="not installed",
    )
