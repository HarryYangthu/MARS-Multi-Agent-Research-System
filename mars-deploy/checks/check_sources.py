"""Check the literature sources from this machine: every enabled engine of the source registry runs one real
query, then the full-text rules are tried on DOIs found by those queries (or given with --doi).

Needs the core's source registry (proposals/mars-core.patch, section 5); without it only the built-in public
engines are tried and that is reported. A PDF counts as fetched only if PyMuPDF can open it and finds text.

    python checks/check_sources.py [--query "..."] [--doi 10.1109/...]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

CORE = Path(os.environ.get("MARS_CORE_ROOT", Path(__file__).resolve().parents[2] / "mars-core")).resolve()
sys.path.insert(0, str(CORE))
from mars import research  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="passive intermodulation cancellation neural network")
    parser.add_argument("--doi", action="append", default=[])
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "local/checks")
    args = parser.parse_args()
    registry = hasattr(research, "load_sources")
    report: dict = {"registry_supported": registry,
                    "proxy": {k: os.environ.get(k) for k in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "DDGS_PROXY")},
                    "engines": {}, "fulltext": []}
    if registry:
        library = research.Library(args.out / "source-cache")
        report["registry_file"] = os.environ.get("MARS_SOURCES_CONFIG") or str(CORE / "configs/sources.yaml")
        engines = library.engines()
    else:
        library = None
        engines = [(name, name == "web", getattr(research, name)) for name in ("arxiv", "openalex", "crossref", "web")]
    dois = list(args.doi)
    for name, _, search in engines:
        started = time.monotonic()
        try:
            hits = search(args.query)
            report["engines"][name] = {"ok": bool(hits), "hits": len(hits), "seconds": round(time.monotonic() - started, 1),
                                       "first": hits[0]["title"][:100] if hits else ""}
            dois += [h["url"].split("doi.org/")[-1] for h in hits if "doi.org/10." in (h.get("url") or "")]
        except Exception as error:  # noqa: BLE001 - the failure is the observation
            report["engines"][name] = {"ok": False, "seconds": round(time.monotonic() - started, 1),
                                       "error": f"{type(error).__name__}: {error}"[:300]}
    rules = (library.sources.get("fulltext") or []) if library else []
    for rule in rules:
        candidates = [d for d in dict.fromkeys(dois) if d.lower().startswith(rule["doi_prefix"].lower())][:2]
        if not candidates:
            report["fulltext"].append({"rule": rule["doi_prefix"], "ok": False, "error": "检索结果里没有这个前缀的 DOI，可用 --doi 指定"})
        for doi in candidates:
            entry: dict = {"rule": rule["doi_prefix"], "doi": doi}
            try:
                entry["pdf_url"] = library.fulltext_url({"url": "https://doi.org/" + doi})
                if entry["pdf_url"]:
                    import fitz
                    data = research._get(entry["pdf_url"], timeout=60, attempts=1)
                    entry["bytes"], entry["starts_with"] = len(data), data[:8].decode("latin-1")
                    with fitz.open(stream=data, filetype="pdf") as document:
                        entry["pages"] = document.page_count
                        entry["text_chars"] = sum(len(page.get_text()) for page in document)
                entry["ok"] = entry.get("text_chars", 0) > 1000
            except Exception as error:  # noqa: BLE001
                entry.update(ok=False, error=f"{type(error).__name__}: {error}"[:300])
            if not entry["ok"] and (entry.get("bytes") == 0 or entry.get("starts_with", "").startswith("<")):
                entry["hint"] = "返回空内容或网页而不是 PDF：通常是没有订阅权限，或请求没有走机构出口（检查 NO_PROXY/HTTPS_PROXY）"
            report["fulltext"].append(entry)
    report["ok"] = any(e["ok"] for e in report["engines"].values())
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"sources-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if not registry:
        print("\n内核尚未合入来源注册表（proposals/mars-core.patch 第 5 节）：只检查了内置的公网引擎")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
