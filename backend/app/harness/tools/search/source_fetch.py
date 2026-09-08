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
from urllib.parse import urljoin, urlparse, urlunparse
from weakref import WeakValueDictionary

import httpx
from pypdf import PdfReader

from app.harness.agent_loop.trace import atomic_json
from app.harness.tools.registry import ToolContext, ToolResult
from app.settings import get_settings

_archive_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


class SourceFetchError(ValueError):
    """An acquisition failure, never a partial successful source."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def enforce_source_size(size_bytes: int, max_mib: int) -> None:
    """Bound both declared and streamed sizes; no successful partial archives."""
    if type(max_mib) is not int or not 1 <= max_mib <= 64:
        raise ValueError("source size budget must be 1..64 MiB")
    if size_bytes > max_mib * 1024 * 1024:
        raise SourceFetchError("size_limit", f"source exceeds configured {max_mib} MiB limit; observed {size_bytes} bytes; partial file not archived")


def resource_key(url: str) -> str:
    """Identify arXiv link aliases without guessing that two versions are equal."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
        match = re.fullmatch(r"/(?:abs|pdf)/((?:[0-9]{4}\.[0-9]{4,5}|[a-zA-Z.-]+/[0-9]{7}))(v[1-9][0-9]*)?(?:\.pdf)?/?", parsed.path)
        if match:
            return f"arxiv:{match[1]}:{match[2] or 'latest'}"
    # Preserve query parameters for generic resources (they may select content).
    netloc = host + (f":{parsed.port}" if parsed.port not in {None, 443} else "")
    return "url:" + urlunparse((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.params, parsed.query, ""))


def resource_aliases(url: str, final_url: str) -> set[str]:
    requested, final = resource_key(url), resource_key(final_url)
    if requested.startswith("arxiv:") and final.startswith("arxiv:"):
        paper, version = requested.rsplit(":", 1)
        final_paper, final_version = final.rsplit(":", 1)
        if paper != final_paper or (version != "latest" and final_version not in {version, "latest"}):
            raise SourceFetchError("resource_mismatch", "redirect changed the requested paper or explicit version")
        # An explicit version redirected to an unversioned endpoint does not prove
        # that the archived version is also the current/latest version.
        if version != "latest" and final_version == "latest":
            return {requested}
    return {requested, final}


def attempt_policy(previous: list[dict[str, Any]], url: str, *, max_attempts: int,
                   max_mib: int) -> tuple[int, str | None, str | None]:
    """Bound actual attempts across calls and URL aliases within this run."""
    key = resource_key(url)
    attempts: list[dict[str, Any]] = []
    for old in previous:
        if not old.get("network_download_attempted"):
            continue
        requested = str(old.get("download_url", ""))
        try:
            aliases = resource_aliases(requested, str(old.get("final_url") or requested))
        except ValueError:
            aliases = {resource_key(requested)}
        if key in aliases:
            attempts.append(old)
    last_error = str(attempts[-1].get("error_code") or "unknown") if attempts else None
    if len(attempts) >= max_attempts:
        return len(attempts), "resource attempt limit reached", last_error
    for old in reversed(attempts):
        if old.get("ok") or old.get("archive_complete"):
            continue
        if old.get("retryable") is False:
            # A deliberate increase of the host byte budget can resolve this
            # failure; it still consumes the bounded network attempt allowance.
            if old.get("error_code") == "size_limit" and max_mib > old.get("source_max_mib", max_mib):
                continue
            return len(attempts), "previous acquisition failure is not retryable", last_error
    return len(attempts), None, last_error


def cached_source(previous: list[dict[str, Any]], root: Path, url: str) -> tuple[bytes, str, str] | None:
    """Reuse only complete, hash-verified archives bound to an observed URL."""
    key = resource_key(url)
    for old in reversed(previous):
        if not (old.get("archive_complete") or old.get("ok")):
            continue
        try:
            requested = str(old["download_url"])
            final = str(old.get("final_url") or requested)
            if key not in resource_aliases(requested, final):
                continue
            allowed_url(final)
            path = Path(old["download_path"]).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                continue
            enforce_source_size(path.stat().st_size, get_settings().mars_source_max_mib)
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() == old.get("sha256"):
                return data, str(old.get("content_type", "")), final
        except (KeyError, TypeError, ValueError, OSError):
            continue
    return None


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


async def download(client: httpx.AsyncClient, url: str, *,
                   telemetry: dict[str, Any] | None = None) -> tuple[bytes, str, str]:
    current = allowed_url(url)
    max_mib = get_settings().mars_source_max_mib
    stats = telemetry if telemetry is not None else {}
    started = time.monotonic()
    stats.update(download_bytes_received=0, download_wire_bytes_received=0,
                 download_elapsed_seconds=0.0, download_complete=False)
    try:
        for redirects in range(6):
            stats.update(final_url=current, redirect_count=redirects)
            async with client.stream("GET", current) as response:
                stats["http_status"] = response.status_code
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise SourceFetchError("invalid_content", "redirect is missing Location")
                    current = allowed_url(urljoin(current, location))
                    resource_aliases(url, current)
                    continue
                if not response.is_success:
                    raise SourceFetchError("http_404" if response.status_code == 404 else "http_status",
                                           f"source HTTP status {response.status_code}",
                                           retryable=response.status_code in {408, 429} or response.status_code >= 500)
                if response.status_code == 206:
                    raise SourceFetchError("invalid_content", "unsolicited partial HTTP response; no complete archive")
                declared = int(response.headers.get("content-length", 0))
                stats["download_expected_bytes"] = declared or None
                enforce_source_size(declared, max_mib)
                data = bytearray()
                async for block in response.aiter_bytes():
                    stats["download_bytes_received"] += len(block)
                    stats["download_wire_bytes_received"] = response.num_bytes_downloaded
                    enforce_source_size(len(data) + len(block), max_mib)
                    data.extend(block)
                stats["download_complete"] = True
                return bytes(data), response.headers.get("content-type", ""), current
        raise SourceFetchError("redirect_limit", "source redirect limit exceeded")
    finally:
        stats["download_elapsed_seconds"] = round(time.monotonic() - started, 3)


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


def failure_details(exc: Exception, *, stage: str) -> tuple[str, bool]:
    if isinstance(exc, SourceFetchError):
        return exc.code, exc.retryable
    if isinstance(exc, httpx.ConnectTimeout):
        return "connect_timeout", True
    if isinstance(exc, httpx.ReadTimeout):
        return "read_timeout", True
    if isinstance(exc, httpx.RequestError):
        return "network_error", True
    if stage == "extraction":
        return "extraction_error", False
    if stage == "archive":
        return "archive_error", True
    return "invalid_source", False


async def fetch_sources_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    # Native calls sharing an archive must not race its cache/attempt index.
    # Weak references avoid retaining one lock per historical run forever.
    started = time.monotonic()
    key = str((Path(str(ctx.extra.get("run_root", ""))) / ctx.agent / "research" / "downloads").resolve())
    lock = _archive_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _archive_locks[key] = lock
    try:
        await asyncio.wait_for(lock.acquire(), timeout=get_settings().mars_source_total_timeout_seconds)
    except TimeoutError:
        return ToolResult(ok=False, error="source total budget exhausted waiting for the run archive; no network attempt",
                          output={"error_code": "total_timeout", "retryable": True, "sources": []})
    try:
        return await _fetch_sources_tool(args, ctx, started=started)
    finally:
        lock.release()


async def _fetch_sources_tool(args: dict[str, Any], ctx: ToolContext, *, started: float) -> ToolResult:
    settings = get_settings()
    if not settings.mars_enable_network_tools:
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
    if not isinstance(previous, list) or any(not isinstance(item, dict) for item in previous):
        return ToolResult(ok=False, error="invalid source archive index; cannot verify cache or attempt history")
    rows: list[dict[str, Any]] = []
    deadline = started + settings.mars_source_total_timeout_seconds
    budget = {"connect_timeout_seconds": settings.mars_source_connect_timeout_seconds,
              "read_timeout_seconds": settings.mars_source_read_timeout_seconds,
              "download_timeout_seconds": settings.mars_source_download_timeout_seconds,
              "total_timeout_seconds": settings.mars_source_total_timeout_seconds,
              "max_attempts": settings.mars_source_max_attempts,
              "max_mib": settings.mars_source_max_mib}
    timeout = httpx.Timeout(connect=settings.mars_source_connect_timeout_seconds,
                            read=settings.mars_source_read_timeout_seconds,
                            write=settings.mars_source_connect_timeout_seconds,
                            pool=settings.mars_source_connect_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for source in selected:
            row: dict[str, Any] = {"ok": False, "network_download_attempted": False,
                                  "network_download_performed": False, "reused": False,
                                  "source_max_mib": settings.mars_source_max_mib,
                                  "download_budget": budget, "attempt_count": 0,
                                  "download_bytes_received": 0, "download_elapsed_seconds": 0.0,
                                  "download_complete": False, "archive_complete": False}
            stage = "input"
            rows.append(row)
            previous.append(row)
            try:
                if not isinstance(source, dict):
                    raise ValueError("source entry must be an object")
                title = str(source.get("title") or "").strip()
                source_url = str(source.get("url") or "").strip()
                parsed = urlparse(source_url)
                inferred = ("https://arxiv.org/pdf/" + parsed.path.removeprefix("/abs/")
                            if parsed.hostname in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"} and parsed.path.startswith("/abs/")
                            else source_url)
                url = allowed_url(str(source.get("pdf_url") or inferred))
                if not title:
                    raise ValueError("source requires its retrieved title")
                row.update(title=title, url=source_url, download_url=url, resource_key=resource_key(url))
                attempts, blocked, previous_error = attempt_policy(previous, url,
                    max_attempts=settings.mars_source_max_attempts, max_mib=settings.mars_source_max_mib)
                row["attempt_count"] = attempts
                cached = cached_source(previous, root, url)
                if cached:
                    data, mime, final_url = cached
                    row.update(reused=True, download_complete=True)
                else:
                    if blocked:
                        row.update(retry_blocked_reason=blocked, previous_error_code=previous_error)
                        raise SourceFetchError("retry_exhausted", blocked + "; choose another resource or address the failure")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise SourceFetchError("total_timeout", "total source budget exhausted before network attempt", retryable=True)
                    stage = "download"
                    row.update(network_download_attempted=True, attempt_count=attempts + 1,
                               error_code="in_progress", retryable=True)
                    # Count an interrupted attempt even if this process is stopped
                    # while streaming; no partial source or read receipt is saved.
                    atomic_json(index_path, previous)
                    limit = min(settings.mars_source_download_timeout_seconds, remaining)
                    try:
                        data, mime, final_url = await asyncio.wait_for(download(client, url, telemetry=row), timeout=limit)
                    except TimeoutError as exc:
                        code = "total_timeout" if remaining <= settings.mars_source_download_timeout_seconds else "download_timeout"
                        raise SourceFetchError(code, f"source transfer exceeded {limit:.3f}s budget; partial content not archived", retryable=True) from exc
                    row["network_download_performed"] = True
                enforce_source_size(len(data), settings.mars_source_max_mib)
                aliases = resource_aliases(url, final_url)
                sha = hashlib.sha256(data).hexdigest()
                is_pdf = data.startswith(b"%PDF-")
                if ("pdf" in mime or "/pdf/" in url or url.endswith(".pdf")) and not is_pdf:
                    raise SourceFetchError("invalid_content", "PDF URL returned non-PDF content")
                if not is_pdf and "html" not in mime:
                    raise SourceFetchError("invalid_content", "unsupported source MIME")
                stage = "archive"
                target = root / (sha + (".pdf" if is_pdf else ".html"))
                if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != sha:
                    temporary = root / (uuid.uuid4().hex + ".archive.tmp")
                    try:
                        temporary.write_bytes(data)
                        temporary.replace(target)
                    finally:
                        temporary.unlink(missing_ok=True)
                row.update(download_path=str(target), sha256=sha, bytes=len(data), resource_aliases=sorted(aliases),
                           content_type=mime, source_type="pdf" if is_pdf else "html", final_url=final_url,
                           archive_complete=True)
                stage = "extraction"
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SourceFetchError("total_timeout", "total source budget exhausted before page extraction", retryable=True)
                if is_pdf:
                    try:
                        row.update(await asyncio.wait_for(asyncio.to_thread(extract_pdf, data, start_page=start_page,
                                                       max_pages=max_pages, max_chars=max_chars), timeout=remaining))
                    except TimeoutError as exc:
                        raise SourceFetchError("total_timeout", "total source budget exhausted during page extraction; no read receipt", retryable=True) from exc
                else:
                    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", data.decode("utf-8", errors="replace"))
                    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
                    row.update(excerpt=re.sub(r"\s+", " ", text).strip()[:max_chars], full_document_read=False)
                row.update(ok=True, error_code=None, retryable=False)
                receipt_path = root / (uuid.uuid4().hex + ".read.json")
                atomic_json(receipt_path, row)
                row["read_receipt"] = str(receipt_path)
            except asyncio.CancelledError:
                row.update(error_code="cancelled", error="source fetch cancelled; no read receipt", retryable=True)
                raise
            except Exception as exc:
                code, retryable = failure_details(exc, stage=stage)
                if row["network_download_attempted"] and row["attempt_count"] >= settings.mars_source_max_attempts and not row["archive_complete"]:
                    retryable = False
                    row["retry_blocked_reason"] = "resource attempt limit reached"
                row.update(error=f"{type(exc).__name__}: {exc}", error_code=code, retryable=retryable)
            finally:
                atomic_json(index_path, previous)
    return ToolResult(ok=any(row["ok"] for row in rows),
                      error=None if any(row["ok"] for row in rows) else "source fetch failed; see individual errors",
                      output={"download_dir": str(root), "index_path": str(index_path), "sources": rows,
                              "download_budget": budget, "elapsed_seconds": round(time.monotonic() - started, 3), **batch},
                      evidence_refs=[str(index_path)])
