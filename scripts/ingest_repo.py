#!/usr/bin/env python3
"""Index a research code repo into the `code_assets` KB zone.

Reads `projects/<name>/repo_link.yaml`, walks the linked path obeying
`allowed_paths` + `ignore_patterns`, chunks each file, and writes records
to `knowledge/code_assets/_index.json` via the same ingestion path the
runtime uses.

Usage:
    python scripts/ingest_repo.py                        # default project = pimc
    python scripts/ingest_repo.py --project other-name
    python scripts/ingest_repo.py --dry-run              # report only
"""
from __future__ import annotations

import argparse
import fnmatch
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

import yaml  # noqa: E402

from app.harness.kb.ingester import ingest  # noqa: E402
from app.harness.kb.stores import get_stores  # noqa: E402


CODE_EXTS = {".py", ".pyx", ".c", ".h", ".cpp", ".hpp", ".cu", ".cuh", ".rs", ".go", ".java"}


def _load_repo_link(project: str) -> dict:
    p = REPO_ROOT / "projects" / project / "repo_link.yaml"
    if not p.exists():
        raise FileNotFoundError(f"missing repo_link.yaml: {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _resolve_path(repo_path: str) -> Path:
    if repo_path.startswith("/"):
        return Path(repo_path)
    return (REPO_ROOT / "projects" / "pimc" / repo_path).resolve()


def _matches_ignore(rel: Path, patterns: list[str]) -> bool:
    s = str(rel).replace("\\", "/")
    for pat in patterns:
        # both directory and filename style
        if pat.endswith("/") and (s.startswith(pat) or f"/{pat}" in f"/{s}/"):
            return True
        if fnmatch.fnmatch(s, pat) or fnmatch.fnmatch(rel.name, pat):
            return True
    return False


def _matches_allowed(rel: Path, allowed: list[str]) -> bool:
    if not allowed:
        return True
    s = str(rel).replace("\\", "/")
    for a in allowed:
        a = a.rstrip("/")
        if s == a or s.startswith(a + "/"):
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="pimc")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-bytes-per-file", type=int, default=200_000)
    args = ap.parse_args(argv)

    cfg = _load_repo_link(args.project)
    repo_root = _resolve_path(str(cfg.get("repo_path", ".")))
    allowed = list(cfg.get("allowed_paths", []) or [])
    ignore = list(cfg.get("ignore_patterns", []) or [])

    print(f"[ingest_repo] project={args.project}")
    print(f"[ingest_repo] repo_root={repo_root}")
    if not repo_root.exists():
        print(f"[ingest_repo] ERROR: repo_root does not exist: {repo_root}", file=sys.stderr)
        print(f"[ingest_repo]   put your real research code at: {repo_root}", file=sys.stderr)
        print(f"[ingest_repo]   or change projects/{args.project}/repo_link.yaml::repo_path", file=sys.stderr)
        return 2
    print(f"[ingest_repo] allowed_paths={allowed}")
    print(f"[ingest_repo] ignore_patterns={ignore}")

    files: list[Path] = []
    for p in repo_root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in CODE_EXTS:
            continue
        rel = p.relative_to(repo_root)
        if _matches_ignore(rel, ignore):
            continue
        if not _matches_allowed(rel, allowed):
            continue
        if p.stat().st_size > args.max_bytes_per_file:
            print(f"[skip too-large] {rel}")
            continue
        files.append(p)

    print(f"[ingest_repo] {len(files)} files matched")
    if args.dry_run:
        for p in files[:50]:
            print("  ", p.relative_to(repo_root))
        if len(files) > 50:
            print(f"  ... ({len(files) - 50} more)")
        return 0

    stores = get_stores()
    chunks_total = 0
    for p in files:
        rel = str(p.relative_to(repo_root))
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = p.read_bytes().decode("utf-8", errors="replace")
        records = ingest(
            zone="code_assets",
            text=text,
            metadata={
                "project": args.project,
                "kind": "code",
                "path": rel,
                "lang": p.suffix.lstrip("."),
            },
            chunk_size=800,
            overlap=80,
            stores=stores,
        )
        chunks_total += len(records)

    print(f"[ingest_repo] wrote {chunks_total} chunks to knowledge/code_assets/_index.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
