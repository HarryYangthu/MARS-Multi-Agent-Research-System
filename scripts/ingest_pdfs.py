#!/usr/bin/env python3
"""Index PDF research papers into the `literature` KB zone.

Walks `workspace/uploads/papers/`, parses each PDF (pypdf), chunks the text,
and writes records via the same ingestion path the runtime uses.

Usage:
    python scripts/ingest_pdfs.py
    python scripts/ingest_pdfs.py --dir workspace/uploads/papers
    python scripts/ingest_pdfs.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.harness.kb.ingester import ingest  # noqa: E402
from app.harness.kb.stores import get_stores  # noqa: E402


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise SystemExit(
            "pypdf is required. install via `pip install pypdf` or "
            "`pip install -e \".[dev]\"` from the repo root."
        ) from exc
    reader = PdfReader(str(path))
    parts: list[str] = []
    for i, page in enumerate(reader.pages):
        try:
            parts.append(f"[page {i + 1}]\n{page.extract_text() or ''}")
        except Exception as exc:  # pragma: no cover (pdf parsing varies)
            parts.append(f"[page {i + 1} extraction failed: {exc}]")
    return "\n\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dir",
        default=str(REPO_ROOT / "workspace" / "uploads" / "papers"),
        help="directory containing *.pdf files",
    )
    ap.add_argument("--project", default="moe-pimc")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    src = Path(args.dir).expanduser().resolve()
    if not src.exists():
        print(f"[ingest_pdfs] creating empty directory: {src}")
        src.mkdir(parents=True, exist_ok=True)
        print(f"[ingest_pdfs] put your *.pdf files there and re-run", file=sys.stderr)
        return 0

    # rglob does not follow symlinked directories by default; walk manually so
    # users can `ln -s` an external papers folder under workspace/uploads/papers/.
    import os as _os
    pdfs: list[Path] = []
    for root, _dirs, files in _os.walk(src, followlinks=True):
        for fn in files:
            if fn.lower().endswith(".pdf"):
                pdfs.append(Path(root) / fn)
    pdfs.sort()
    print(f"[ingest_pdfs] dir={src}")
    print(f"[ingest_pdfs] {len(pdfs)} pdf(s) found")
    if args.dry_run:
        for p in pdfs[:20]:
            print("  ", p.relative_to(src))
        if len(pdfs) > 20:
            print(f"  ... ({len(pdfs) - 20} more)")
        return 0

    if not pdfs:
        return 0

    stores = get_stores()
    total_chunks = 0
    for pdf in pdfs:
        rel = pdf.relative_to(src)
        text = _read_pdf(pdf)
        if not text.strip():
            print(f"[skip empty] {rel}")
            continue
        records = ingest(
            zone="literature",
            text=text,
            metadata={
                "project": args.project,
                "kind": "paper",
                "title": pdf.stem,
                "source_path": str(rel),
            },
            chunk_size=1500,
            overlap=200,
            stores=stores,
        )
        total_chunks += len(records)
        print(f"  ingested {rel}  -> {len(records)} chunks")

    print(f"[ingest_pdfs] total {total_chunks} chunks written to knowledge/literature/_index.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
