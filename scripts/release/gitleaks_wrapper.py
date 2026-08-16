"""Non-destructive argv-only wrapper around the optional gitleaks executable."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Sequence

GitleaksStatus = Literal["passed", "failed", "missing", "error"]


@dataclass(frozen=True)
class GitleaksResult:
    status: GitleaksStatus
    returncode: int
    report_path: str
    command: tuple[str, ...]
    detail: str = ""


def run_gitleaks(
    *,
    repo: Path,
    treeish: str,
    report_path: Path,
    executable: str | None = None,
) -> GitleaksResult:
    tool = executable or shutil.which("gitleaks")
    if tool is None:
        return GitleaksResult(
            status="missing",
            returncode=127,
            report_path=str(report_path),
            command=(),
            detail="gitleaks executable is required for a release decision",
        )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    command = (
        tool,
        "git",
        str(repo.resolve()),
        "--no-banner",
        "--redact",
        "--report-format",
        "json",
        "--report-path",
        str(report_path.resolve()),
        f"--log-opts={treeish}",
    )
    completed = subprocess.run(
        command,
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    detail = (completed.stderr or completed.stdout)[-4000:].strip()
    if completed.returncode == 0:
        status: GitleaksStatus = "passed"
    elif completed.returncode == 1:
        status = "failed"
    else:
        status = "error"
    return GitleaksResult(
        status=status,
        returncode=completed.returncode,
        report_path=str(report_path),
        command=command,
        detail=detail,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run gitleaks without rewriting history")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--treeish", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parsed = parser.parse_args(argv)
    result = run_gitleaks(
        repo=parsed.repo,
        treeish=parsed.treeish,
        report_path=parsed.report,
    )
    sys.stdout.write(json.dumps(asdict(result), sort_keys=True) + "\n")
    return 0 if result.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
