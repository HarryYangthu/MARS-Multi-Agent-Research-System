"""Audit and optionally materialize a public V3.0 snapshot from Git objects."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Literal, Sequence

from scripts.release.gitleaks_wrapper import GitleaksResult, run_gitleaks

Scope = Literal["tree", "history", "tooling", "docker"]
GitleaksRunner = Callable[[Path, str, Path], GitleaksResult]

_DENY_TERMS = (
    "P" + "IMC",
    "D" + "PD",
    "Hua" + "wei",
    "华" + "为",
)
_BINARY_SUFFIXES = frozenset(
    {
        ".7z",
        ".bin",
        ".ckpt",
        ".doc",
        ".docx",
        ".gif",
        ".gz",
        ".h5",
        ".hdf5",
        ".jpeg",
        ".jpg",
        ".mat",
        ".npy",
        ".npz",
        ".pdf",
        ".pkl",
        ".png",
        ".pt",
        ".pth",
        ".ppt",
        ".pptx",
        ".tar",
        ".tgz",
        ".webp",
        ".xls",
        ".xlsx",
        ".zip",
    }
)
_INTERNAL_PATH_PATTERNS = (
    ".claude/**",
    "CLAUDE.md",
    "**/* 2.md",
    "**/*_副本.*",
    "docs/interview/**",
    "docs/implementation_report.md",
    "docs/phase_*_status.md",
    "docs/**/*development*",
    "docs/**/*requirements*",
)
_DOCKER_REQUIRED_IGNORES = (
    ".git",
    ".env",
    "node_modules",
    "runs",
    "workspace",
    "knowledge",
)
_MAX_HISTORY_FINDINGS = 500


class ReleaseGateError(RuntimeError):
    """Raised when a release input cannot be proven safe."""


@dataclass(frozen=True)
class Finding:
    rule: str
    scope: Scope
    path: str
    line: int
    detail: str
    evidence_hash: str
    commit: str = ""


@dataclass(frozen=True)
class GitSelection:
    commit: str
    allowlist_path: str
    patterns: tuple[str, ...]
    files: tuple[str, ...]


@dataclass(frozen=True)
class ReleaseAudit:
    schema_id: str
    decision: Literal["pass", "blocked"]
    source_mode: str
    commit: str
    tree_hash: str
    allowlist_path: str
    allowlist_patterns: tuple[str, ...]
    selected_files: tuple[str, ...]
    findings: tuple[Finding, ...]
    gitleaks: GitleaksResult
    dry_run: bool
    materialized_archive: str

    def as_json(self) -> dict[str, object]:
        return {
            **asdict(self),
            "findings": [asdict(finding) for finding in self.findings],
            "gitleaks": asdict(self.gitleaks),
        }


def audit_release(
    *,
    repo: Path,
    treeish: str,
    allowlist_path: str,
    scan_history: bool = True,
    gitleaks_required: bool = True,
    gitleaks_runner: GitleaksRunner | None = None,
    materialize: Path | None = None,
) -> ReleaseAudit:
    repository = _validated_repository(repo)
    selection = select_git_tree(
        repository,
        treeish=treeish,
        allowlist_path=allowlist_path,
    )
    with tempfile.TemporaryDirectory(prefix="mars-v30-release-") as temporary:
        temporary_root = Path(temporary)
        export_root = temporary_root / "tree"
        _extract_selection(repository, selection, export_root)
        findings = list(scan_export_tree(export_root, selection.files))
        if scan_history:
            findings.extend(scan_git_history(repository, selection.commit))
        leak_report = temporary_root / "gitleaks.json"
        runner = gitleaks_runner or _default_gitleaks_runner
        gitleaks = runner(repository, selection.commit, leak_report)
        if gitleaks.status != "passed" and (
            gitleaks_required or gitleaks.status != "missing"
        ):
            findings.append(
                Finding(
                    rule="gitleaks",
                    scope="tooling",
                    path="",
                    line=0,
                    detail=f"gitleaks status is {gitleaks.status}",
                    evidence_hash=_hash_text(gitleaks.detail or gitleaks.status),
                )
            )
        tree_hash = _tree_hash(export_root, selection.files)
        decision: Literal["pass", "blocked"] = "blocked" if findings else "pass"
        materialized = ""
        if materialize is not None:
            if decision != "pass":
                raise ReleaseGateError("materialization is blocked by release findings")
            _materialize_archive(export_root, selection.files, materialize)
            materialized = str(materialize.resolve())
        return ReleaseAudit(
            schema_id="v30_release_audit.v1",
            decision=decision,
            source_mode="resolved_git_commit_objects_only",
            commit=selection.commit,
            tree_hash=tree_hash,
            allowlist_path=selection.allowlist_path,
            allowlist_patterns=selection.patterns,
            selected_files=selection.files,
            findings=tuple(findings),
            gitleaks=gitleaks,
            dry_run=materialize is None,
            materialized_archive=materialized,
        )


def select_git_tree(repo: Path, *, treeish: str, allowlist_path: str) -> GitSelection:
    if not treeish.strip():
        raise ReleaseGateError("treeish is required")
    safe_allowlist = _safe_relative(allowlist_path, label="allowlist path")
    commit = _git_text(repo, "rev-parse", "--verify", f"{treeish}^{{commit}}").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ReleaseGateError("treeish did not resolve to a full commit hash")
    allowlist_text = _git_text(repo, "show", f"{commit}:{safe_allowlist}")
    patterns = _parse_allowlist(allowlist_text)
    tree_output = _git_bytes(repo, "ls-tree", "-r", "-z", "--name-only", commit)
    all_files = tuple(
        item.decode("utf-8") for item in tree_output.split(b"\0") if item
    )
    selected = tuple(
        path
        for path in all_files
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)
    )
    if not selected:
        raise ReleaseGateError("allowlist selected no committed files")
    unmatched = [
        pattern
        for pattern in patterns
        if not any(fnmatch.fnmatchcase(path, pattern) for path in all_files)
    ]
    if unmatched:
        raise ReleaseGateError(
            "allowlist contains unmatched patterns: " + ", ".join(unmatched)
        )
    return GitSelection(
        commit=commit,
        allowlist_path=safe_allowlist,
        patterns=patterns,
        files=selected,
    )


def scan_export_tree(root: Path, selected_files: Sequence[str]) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for relative in selected_files:
        path = root / relative
        if any(fnmatch.fnmatchcase(relative, pattern) for pattern in _INTERNAL_PATH_PATTERNS):
            findings.append(_finding("internal_path", "tree", relative, 0, relative))
        if path.suffix.casefold() in _BINARY_SUFFIXES:
            findings.append(_finding("binary_extension", "tree", relative, 0, relative))
            continue
        payload = path.read_bytes()
        if b"\0" in payload:
            findings.append(_finding("binary_content", "tree", relative, 0, payload[:64]))
            continue
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(_finding("non_utf8_content", "tree", relative, 0, payload[:64]))
            continue
        findings.extend(_scan_text(relative, text, scope="tree"))
    findings.extend(_scan_docker_context(root, selected_files))
    return tuple(_deduplicate(findings))


def scan_git_history(repo: Path, commit: str) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    commits = _git_text(repo, "rev-list", commit).splitlines()
    history_absolute_patterns = (
        "/" + "Users" + "/[A-Za-z0-9._-]+",
        "/" + "home" + "/[A-Za-z0-9._-]+",
        "[A-Za-z]:\\\\" + "Users" + "\\\\[^\\\\[:space:]]+",
    )
    for history_commit in commits:
        fixed = _git_completed(
            repo,
            "grep",
            "-n",
            "-I",
            "-i",
            "-F",
            *tuple(argument for term in _DENY_TERMS for argument in ("-e", term)),
            history_commit,
            "--",
            allowed_returncodes=(0, 1),
        )
        regex = _git_completed(
            repo,
            "grep",
            "-n",
            "-I",
            "-E",
            *tuple(
                argument
                for pattern in history_absolute_patterns
                for argument in ("-e", pattern)
            ),
            history_commit,
            "--",
            allowed_returncodes=(0, 1),
        )
        findings.extend(_history_output_findings(fixed.stdout, history_commit, "deny_term"))
        findings.extend(
            _history_output_findings(regex.stdout, history_commit, "absolute_user_path")
        )
        if len(findings) >= _MAX_HISTORY_FINDINGS:
            findings = findings[:_MAX_HISTORY_FINDINGS]
            findings.append(
                _finding(
                    "history_scan_truncated",
                    "history",
                    "",
                    0,
                    str(_MAX_HISTORY_FINDINGS),
                    commit=history_commit,
                )
            )
            break
    return tuple(_deduplicate(findings))


def write_reports(audit: ReleaseAudit, report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "release_audit.json"
    migration_path = report_dir / "migration_plan.md"
    json_path.write_text(
        json.dumps(audit.as_json(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    migration_path.write_text(_migration_markdown(audit), encoding="utf-8")
    return json_path, migration_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run a fail-closed V3.0 public export from a committed tree"
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--treeish", required=True)
    parser.add_argument(
        "--allowlist", default="scripts/release/v30_tree_allowlist.txt"
    )
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--materialize", type=Path)
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument(
        "--gitleaks-if-installed",
        action="store_true",
        help="Development evidence only; release automation must not use this flag.",
    )
    parsed = parser.parse_args(argv)
    try:
        audit = audit_release(
            repo=parsed.repo,
            treeish=parsed.treeish,
            allowlist_path=parsed.allowlist,
            scan_history=not parsed.no_history,
            gitleaks_required=not parsed.gitleaks_if_installed,
            materialize=parsed.materialize,
        )
    except ReleaseGateError as exc:
        sys.stderr.write(f"release gate error: {exc}\n")
        return 2
    if parsed.report_dir is not None:
        write_reports(audit, parsed.report_dir)
    sys.stdout.write(json.dumps(audit.as_json(), ensure_ascii=False, sort_keys=True) + "\n")
    return 0 if audit.decision == "pass" else 1


def _validated_repository(repo: Path) -> Path:
    resolved = repo.resolve()
    result = _git_completed(resolved, "rev-parse", "--is-inside-work-tree")
    if result.stdout.strip() != "true":
        raise ReleaseGateError(f"not a Git worktree: {resolved}")
    return resolved


def _parse_allowlist(value: str) -> tuple[str, ...]:
    patterns: list[str] = []
    for line_number, line in enumerate(value.splitlines(), start=1):
        pattern = line.strip()
        if not pattern or pattern.startswith("#"):
            continue
        if pattern.startswith("!"):
            raise ReleaseGateError(
                f"negative allowlist pattern is forbidden at line {line_number}"
            )
        _safe_relative(pattern, label=f"allowlist line {line_number}", allow_glob=True)
        patterns.append(pattern)
    if not patterns:
        raise ReleaseGateError("allowlist must contain at least one path pattern")
    return tuple(patterns)


def _safe_relative(value: str, *, label: str, allow_glob: bool = False) -> str:
    if not value or "\\" in value or "\x00" in value or ":" in value:
        raise ReleaseGateError(f"invalid {label}: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReleaseGateError(f"{label} must be a normalized relative path")
    if not allow_glob and any(token in value for token in "*?["):
        raise ReleaseGateError(f"globs are forbidden in {label}")
    return path.as_posix()


def _extract_selection(repo: Path, selection: GitSelection, output: Path) -> None:
    output.mkdir(parents=True)
    archive_path = output.parent / "tree.tar"
    command = [
        "git",
        "archive",
        "--format=tar",
        selection.commit,
        "--",
        *selection.files,
    ]
    with archive_path.open("wb") as archive_stream:
        completed = subprocess.run(
            command,
            cwd=repo,
            check=False,
            stdout=archive_stream,
            stderr=subprocess.PIPE,
        )
    if completed.returncode != 0:
        raise ReleaseGateError(
            "git archive failed: "
            + completed.stderr.decode("utf-8", errors="replace")[-2000:]
        )
    extracted: set[str] = set()
    with tarfile.open(archive_path, mode="r:") as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            # Git tree paths may legitimately contain glob metacharacters (for
            # example a Next.js ``[id]`` route). The member is still matched
            # as an exact string against the already-resolved selection.
            relative = _safe_relative(
                member.name,
                label="archive member",
                allow_glob=True,
            )
            if relative not in selection.files:
                raise ReleaseGateError(f"archive contained unselected path: {relative}")
            if not member.isfile() or member.issym() or member.islnk():
                raise ReleaseGateError(f"archive member is not a regular file: {relative}")
            source = archive.extractfile(member)
            if source is None:
                raise ReleaseGateError(f"cannot read archive member: {relative}")
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())
            target.chmod(member.mode & 0o777)
            extracted.add(relative)
    missing = set(selection.files) - extracted
    if missing:
        raise ReleaseGateError(
            "archive omitted selected paths: " + ", ".join(sorted(missing)[:20])
        )


def _scan_text(path: str, text: str, *, scope: Scope) -> list[Finding]:
    findings: list[Finding] = []
    absolute = _absolute_path_pattern()
    secret_patterns = (
        re.compile(r"AKIA[0-9A-Z]{16}"),
        re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    )
    for line_number, line in enumerate(text.splitlines(), start=1):
        folded = line.casefold()
        for term in _DENY_TERMS:
            if term.casefold() in folded:
                findings.append(
                    _finding("deny_term", scope, path, line_number, term)
                )
        if absolute.search(line):
            findings.append(
                _finding("absolute_user_path", scope, path, line_number, line)
            )
        if any(pattern.search(line) for pattern in secret_patterns):
            findings.append(
                _finding("secret_pattern", scope, path, line_number, line)
            )
    return findings


def _absolute_path_pattern() -> re.Pattern[str]:
    roots = ("/" + "Users", "/" + "home")
    unix = "(?:" + "|".join(re.escape(root) for root in roots) + r")/[A-Za-z0-9._-]+(?:/[^\s\"']+)?"
    windows = r"[A-Za-z]:\\" + "Users" + r"\\[^\\\s]+(?:\\[^\s\"']+)?"
    return re.compile(f"(?:{unix}|{windows})")


def _scan_docker_context(root: Path, selected_files: Sequence[str]) -> list[Finding]:
    docker_files = [
        path
        for path in selected_files
        if Path(path).name.startswith("Dockerfile") or "docker-compose" in Path(path).name
    ]
    if not docker_files:
        return []
    ignore_path = root / ".dockerignore"
    if not ignore_path.is_file():
        return [_finding("dockerignore_missing", "docker", ".dockerignore", 0, "missing")]
    entries = tuple(
        line.strip().rstrip("/")
        for line in ignore_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#") and not line.startswith("!")
    )
    findings: list[Finding] = []
    for required in _DOCKER_REQUIRED_IGNORES:
        if not any(
            entry == required
            or entry.startswith(required + "/")
            or entry.endswith("/" + required)
            for entry in entries
        ):
            findings.append(
                _finding(
                    "dockerignore_incomplete",
                    "docker",
                    ".dockerignore",
                    0,
                    required,
                )
            )
    dangerous = (
        ".env",
        ".git/",
        "node_modules/",
        "runs/",
        "workspace/uploads/",
    )
    for path in selected_files:
        if path == ".env" or any(path.startswith(prefix) for prefix in dangerous[1:]):
            findings.append(_finding("docker_context_path", "docker", path, 0, path))
    return findings


def _history_output_findings(
    output: str, commit: str, rule: str
) -> list[Finding]:
    findings: list[Finding] = []
    for line in output.splitlines():
        parts = line.split(":", 3)
        if len(parts) != 4:
            continue
        _, path, line_number, evidence = parts
        try:
            parsed_line = int(line_number)
        except ValueError:
            parsed_line = 0
        findings.append(
            _finding(
                rule,
                "history",
                path,
                parsed_line,
                evidence,
                commit=commit,
            )
        )
    return findings


def _finding(
    rule: str,
    scope: Scope,
    path: str,
    line: int,
    evidence: object,
    *,
    commit: str = "",
) -> Finding:
    return Finding(
        rule=rule,
        scope=scope,
        path=path,
        line=line,
        detail=_detail_for_rule(rule),
        evidence_hash=_hash_text(repr(evidence)),
        commit=commit,
    )


def _detail_for_rule(rule: str) -> str:
    return {
        "absolute_user_path": "user-specific absolute path must become config",
        "binary_content": "binary content is not permitted in the source export",
        "binary_extension": "binary artifact extension is not permitted",
        "deny_term": "domain or organization denylist marker found",
        "docker_context_path": "sensitive runtime path entered Docker context",
        "dockerignore_incomplete": "Docker ignore policy lacks a required boundary",
        "dockerignore_missing": "Docker build files require a root ignore policy",
        "gitleaks": "secret-history scan did not pass",
        "history_scan_truncated": "history evidence reached the configured cap",
        "internal_path": "internal-only document path is not public-release material",
        "non_utf8_content": "non-UTF-8 file requires explicit public review",
        "secret_pattern": "secret-shaped content requires removal and rotation review",
    }.get(rule, "release policy violation")


def _deduplicate(findings: Sequence[Finding]) -> list[Finding]:
    unique: dict[tuple[str, Scope, str, int, str], Finding] = {}
    for finding in findings:
        key = (finding.rule, finding.scope, finding.path, finding.line, finding.commit)
        unique.setdefault(key, finding)
    return [unique[key] for key in sorted(unique)]


def _tree_hash(root: Path, files: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(files):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / relative).read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _materialize_archive(root: Path, files: Sequence[str], target: Path) -> None:
    if target.exists():
        raise ReleaseGateError(f"refusing to overwrite existing archive: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(target, mode="x:gz") as archive:
        for relative in sorted(files):
            archive.add(root / relative, arcname=relative, recursive=False)


def _migration_markdown(audit: ReleaseAudit) -> str:
    grouped: dict[str, set[str]] = {}
    for finding in audit.findings:
        grouped.setdefault(finding.path or "<repository>", set()).add(finding.rule)
    lines = [
        "# V3.0 public release migration evidence",
        "",
        f"- Decision: `{audit.decision}`",
        f"- Commit: `{audit.commit}`",
        f"- Source mode: `{audit.source_mode}`",
        f"- Selected files: {len(audit.selected_files)}",
        f"- Findings: {len(audit.findings)}",
        "",
        "## Migration list",
        "",
    ]
    if not grouped:
        lines.append("No migration findings.")
    else:
        for path in sorted(grouped):
            rules = ", ".join(sorted(grouped[path]))
            lines.append(f"- `{path}` — {rules}; move project-specific material out or sanitize it.")
    lines.extend(
        [
            "",
            "## Committed allowlist",
            "",
            *[f"- `{pattern}`" for pattern in audit.allowlist_patterns],
            "",
            "This report does not rewrite history, delete remotes, or publish an archive.",
        ]
    )
    return "\n".join(lines) + "\n"


def _default_gitleaks_runner(repo: Path, treeish: str, report: Path) -> GitleaksResult:
    return run_gitleaks(repo=repo, treeish=treeish, report_path=report)


def _git_text(repo: Path, *arguments: str) -> str:
    return _git_completed(repo, *arguments).stdout


def _git_bytes(repo: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=repo,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ReleaseGateError(
            completed.stderr.decode("utf-8", errors="replace")[-2000:]
        )
    return completed.stdout


def _git_completed(
    repo: Path,
    *arguments: str,
    allowed_returncodes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode not in allowed_returncodes:
        raise ReleaseGateError(completed.stderr[-2000:] or completed.stdout[-2000:])
    return completed


def _hash_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


if __name__ == "__main__":
    raise SystemExit(main())
