"""Bounded source archiving with verified cache reuse and explicit read windows."""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import time
import uuid
import warnings
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from pypdf import PdfReader

from app.harness.agent_loop.trace import atomic_json
from app.harness.tools.registry import ToolContext, ToolResult
from app.settings import get_settings

def enforce_source_size(size_bytes: int, max_mib: int) -> None:
    """Bound both declared and streamed sizes; no successful partial archives."""
    if type(max_mib) is not int or not 1 <= max_mib <= 64:
        raise ValueError("source size budget must be 1..64 MiB")
    if size_bytes > max_mib * 1024 * 1024:
        raise ValueError(f"source exceeds configured {max_mib} MiB limit; observed {size_bytes} bytes; partial file not archived")


def source_batch(sources: list[Any], limit: int) -> tuple[list[Any], dict[str, Any]]:
    """Report every omission instead of implying all requested sources were handled."""
    if isinstance(limit, bool) or not 1 <= limit <= 5:
        raise ValueError("max_sources must be between 1 and 5")
    selected = sources[:limit]
    return selected, {
        "requested_sources": len(sources), "selected_sources": len(selected),
        "skipped_sources": len(sources) - len(selected), "max_sources": limit,
        "skipped": [{"source": source, "reason": "max_sources limit; not attempted"}
                    for source in sources[limit:]],
    }


def allowed_url(url: str) -> str:
    parsed = urlparse(url)
    allowed = {x.strip().lower() for x in get_settings().mars_web_search_allowlist.split(",") if x.strip()}
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("source requires HTTPS without credentials or nonstandard port")
    if not host or not any(host == d or host.endswith("." + d) for d in allowed):
        raise ValueError(f"source host is not allowlisted: {host}")
    return url


async def download(client: httpx.AsyncClient, url: str) -> tuple[bytes, str, str]:
    current = allowed_url(url)
    max_mib = get_settings().mars_source_max_mib
    for _ in range(6):
        async with client.stream("GET", current) as response:
            if response.is_redirect:
                current = allowed_url(urljoin(current, response.headers["location"]))
                continue
            response.raise_for_status()
            enforce_source_size(int(response.headers.get("content-length", 0)), max_mib)
            data = bytearray()
            async for block in response.aiter_bytes():
                data.extend(block)
                enforce_source_size(len(data), max_mib)
            return bytes(data), response.headers.get("content-type", ""), current
    raise ValueError("source redirect limit exceeded")


def extract_pdf(data: bytes, *, start_page: int, max_pages: int, max_chars: int) -> dict[str, Any]:
    if not data.startswith(b"%PDF-"):
        raise ValueError("invalid PDF signature")
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        reader = PdfReader(BytesIO(data))
        if not 1 <= start_page <= len(reader.pages):
            raise ValueError(f"start_page outside 1..{len(reader.pages)}")
        extracted: list[int] = []
        visible: list[dict[str, Any]] = []
        remaining = max_chars
        for page_num in range(start_page, min(start_page + max_pages, len(reader.pages) + 1)):
            text = reader.pages[page_num - 1].extract_text() or ""
            extracted.append(page_num)
            if remaining and text.strip():
                excerpt = text[:remaining]
                visible.append({"page": page_num, "text": excerpt,
                                "full_page_text_chars": len(text), "truncated": len(excerpt) < len(text)})
                remaining -= len(excerpt)
        if not visible:
            raise ValueError("PDF downloaded but selected pages have no extractable text")
        return {"pdf_pages": len(reader.pages), "extracted_pages": extracted, "visible_pages": visible,
                "parser_warnings": [str(w.message)[:200] for w in captured],
                "formula_semantics_verified": False, "full_document_read": False}


async def fetch_sources_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    if not get_settings().mars_enable_network_tools:
        return ToolResult(ok=False, error="network tools disabled")
    run_root = Path(str(ctx.extra.get("run_root", "")))
    if not str(ctx.extra.get("run_root", "")):
        return ToolResult(ok=False, error="run_root required for auditable source archiving")
    root = (run_root / ctx.agent / "research" / "downloads").resolve()
    root.mkdir(parents=True, exist_ok=True)
    start_page = int(args.get("start_page", 1))
    max_pages = int(args.get("max_pages", 4))
    max_chars = int(args.get("max_chars", 8000))
    if not 1 <= max_pages <= 10 or not 1000 <= max_chars <= 16000 or start_page < 1:
        return ToolResult(ok=False, error="invalid extraction window")
    sources = args.get("sources")
    if not isinstance(sources, list) or not sources:
        return ToolResult(ok=False, error="sources must be a nonempty array")
    selected, batch = source_batch(sources, int(args.get("max_sources", 1)))
    index_path = root / "source_fetch_index.v1.json"
    previous = json.loads(index_path.read_text()) if index_path.exists() else []
    rows: list[dict[str, Any]] = []
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
        for source in selected:
            row: dict[str, Any] = {"ok": False, "network_download_attempted": False,
                                  "network_download_performed": False, "reused": False,
                                  "source_max_mib": get_settings().mars_source_max_mib}
            try:
                if not isinstance(source, dict):
                    raise ValueError("source entry must be an object")
                title = str(source.get("title") or "").strip()
                source_url = str(source.get("url") or "").strip()
                parsed = urlparse(source_url)
                inferred = ("https://arxiv.org/pdf/" + parsed.path.removeprefix("/abs/")
                            if parsed.hostname in {"arxiv.org", "export.arxiv.org"} and parsed.path.startswith("/abs/")
                            else source_url)
                url = allowed_url(str(source.get("pdf_url") or inferred))
                if not title:
                    raise ValueError("source requires its retrieved title")
                row.update(title=title, url=source_url, download_url=url)
                cached = next((old for old in reversed(previous) if old.get("ok") and old.get("download_url") == url), None)
                data: bytes | None = None
                mime = ""
                final_url = url
                if cached:
                    path = Path(cached["download_path"]).resolve()
                    if path.is_relative_to(root) and path.is_file():
                        content = path.read_bytes()
                        if hashlib.sha256(content).hexdigest() == cached.get("sha256"):
                            data = content
                            mime = str(cached.get("content_type", ""))
                            final_url = cached.get("final_url", url)
                            row["reused"] = True
                if data is None:
                    remaining = 120 - (time.monotonic() - started)
                    if remaining <= 0:
                        raise TimeoutError("total source download budget exhausted")
                    row["network_download_attempted"] = True
                    data, mime, final_url = await asyncio.wait_for(download(client, url), timeout=min(45, remaining))
                    row["network_download_performed"] = True
                assert data is not None
                enforce_source_size(len(data), get_settings().mars_source_max_mib)
                sha = hashlib.sha256(data).hexdigest()
                is_pdf = data.startswith(b"%PDF-")
                if ("pdf" in mime or "/pdf/" in url or url.endswith(".pdf")) and not is_pdf:
                    raise ValueError("PDF URL returned non-PDF content")
                if not is_pdf and "html" not in mime:
                    raise ValueError("unsupported source MIME")
                target = root / (sha + (".pdf" if is_pdf else ".html"))
                if not target.exists():
                    target.write_bytes(data)
                row.update(download_path=str(target), sha256=sha, bytes=len(data),
                           content_type=mime, source_type="pdf" if is_pdf else "html", final_url=final_url)
                if is_pdf:
                    row.update(await asyncio.to_thread(extract_pdf, data, start_page=start_page,
                                                       max_pages=max_pages, max_chars=max_chars))
                else:
                    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", data.decode("utf-8", errors="replace"))
                    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
                    row.update(excerpt=re.sub(r"\s+", " ", text).strip()[:max_chars], full_document_read=False)
                row["ok"] = True
                receipt_path = root / (uuid.uuid4().hex + ".read.json")
                atomic_json(receipt_path, row)
                row["read_receipt"] = str(receipt_path)
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)
            previous.append(row)
            atomic_json(index_path, previous)
    return ToolResult(ok=any(row["ok"] for row in rows),
                      error=None if any(row["ok"] for row in rows) else "source fetch failed; see individual errors",
                      output={"download_dir": str(root), "index_path": str(index_path), "sources": rows, **batch},
                      evidence_refs=[str(index_path)])
