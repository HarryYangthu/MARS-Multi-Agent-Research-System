"""Search tools registered through the generic tool registry."""
from __future__ import annotations

import asyncio
import html
import hashlib
import re
import json
import time
import urllib.parse
import xml.etree.ElementTree as ET
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx as httpx

from app.harness.kb.memory_writer import write_to_zone
from app.harness.kb.provenance import record_retrieval
from app.harness.tools.search.arxiv_query import exact_arxiv_ids, verify_arxiv_lookup
from app.harness.tools.search.openalex import openalex_search_tool as openalex_search_tool
from app.harness.tools.search.source_fetch import fetch_sources_tool as fetch_sources_tool
from app.harness.tools.registry import ToolContext, ToolResult
from app.settings import get_settings, repo_root

_ARXIV_MIN_INTERVAL_SECONDS = 3.0
_ARXIV_LOCK = asyncio.Lock()
_LAST_ARXIV_CALL = 0.0
_MAX_SOURCE_DOWNLOAD_BYTES = 12 * 1024 * 1024
_MAX_SOURCE_TEXT_CHARS = 16_000
_SOURCE_FETCH_TIMEOUT_SECONDS = 8.0
_SOURCE_FETCH_BUDGET_SECONDS = 24.0


async def local_docs_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Search the literature KB zone and list uploaded documents."""
    query = str(args.get("q") or args.get("query") or "")
    if not query:
        return ToolResult(ok=False, error="query (q) is required")
    top_k = int(args.get("top_k", 3) or 3)

    from app.harness.kb.retriever import query as kb_query

    hits = kb_query(query=query, zones=["literature"], top_k=top_k)
    uploads_dir = repo_root() / "workspace" / "uploads"
    uploaded = (
        sorted(p.name for p in uploads_dir.iterdir() if p.is_file())
        if uploads_dir.exists()
        else []
    )
    return ToolResult(
        ok=True,
        output={
            "query": query,
            "hits": [
                {
                    "score": round(h.score, 4),
                    "excerpt": h.record.text[:280],
                    "metadata": h.record.metadata,
                    "evidence_ref": f"knowledge/literature/{h.record.id}",
                }
                for h in hits
            ],
            "uploaded_docs": uploaded[:20],
        },
    )


async def arxiv_search_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Search arXiv and cache results into the literature KB."""
    settings = get_settings()
    if not settings.mars_enable_network_tools:
        return ToolResult(
            ok=False,
            error="network tools are disabled; set MARS_ENABLE_NETWORK_TOOLS=true",
        )
    try:
        ids = exact_arxiv_ids(args)
    except ValueError as exc:
        return ToolResult(ok=False, error=str(exc))
    query = ",".join(ids) if ids else str(args.get("q") or args.get("query") or "").strip()
    if not query:
        return ToolResult(ok=False, error="query (q) is required")
    top_k = max(1, min(int(args.get("top_k", 5) or 5), 20))
    categories = args.get("categories", [])
    category_query = ""
    if isinstance(categories, list) and categories:
        category_query = " AND (" + " OR ".join(f"cat:{c}" for c in categories) + ")"
    date_from = str(args.get("date_from", "") or "")
    sort_by_raw = str(args.get("sort_by") or "relevance")
    sort_by = (
        sort_by_raw
        if sort_by_raw in {"relevance", "lastUpdatedDate", "submittedDate"}
        else "submittedDate"
    )
    terms = query if re.search(r"\b(AND|OR|ANDNOT)\b|:", query) else " AND ".join(query.split())
    search_query = terms + category_query
    cache_key = _cache_key({"lookup_contract": 1, "arxiv_ids": ids}) if ids else _cache_key(
        {
            "q": search_query,
            "top_k": top_k,
            "date_from": date_from,
            "sort_by": sort_by,
        }
    )
    cache_path = _cache_dir() / f"{cache_key}.json"
    if cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if ids:
            try:
                verify_arxiv_lookup(ids, payload["hits"])
                raw = cache_path.with_suffix(".xml").read_bytes()
                if (hashlib.sha256(raw).hexdigest() != payload.get("metadata_response_sha256")
                        or _parse_arxiv(raw.decode("utf-8"), date_from="") != payload["hits"]):
                    raise ValueError("cached metadata differs from its actual archived response")
            except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
                return ToolResult(ok=False, error=f"invalid exact arXiv lookup cache: {exc}")
        return ToolResult(ok=True, output={**payload, "cached": True})
    params = {
        "search_query": search_query,
        "start": "0",
        "max_results": str(top_k),
        "sortBy": sort_by,
        "sortOrder": "descending",
    }
    if ids:
        params = {"id_list": ",".join(ids), "start": "0", "max_results": str(len(ids))}
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    try:
        await _respect_arxiv_rate_limit()
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, error=f"arXiv request failed: {type(exc).__name__}: {exc}")
    try:
        hits = _parse_arxiv(response.text, date_from=date_from)
        missing = verify_arxiv_lookup(ids, hits) if ids else []
    except (ValueError, ET.ParseError) as exc:
        return ToolResult(ok=False, error=f"arXiv metadata rejected: {exc}")
    for hit in hits:
        receipt = record_retrieval(text=f"{hit['title']}\n\n{hit['summary']}", url=hit["url"], title=hit["title"], run_id=ctx.run_id)
        write_to_zone(
            zone="literature",
            text=f"{hit['title']}\n\n{hit['summary']}",
            metadata={
                **receipt,
                "source": "arxiv",
                "arxiv_id": hit["id"],
                "title": hit["title"],
                "url": hit["url"],
                "pdf_url": hit.get("pdf_url", ""),
                "published": hit["published"],
                "run_id": ctx.run_id,
            },
        )
    payload = {
        "query": query,
        "sort_by": sort_by,
        "url": url,
        "hits": hits,
        "cached": False,
        "cached_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    if ids:
        payload.update(lookup_mode="arxiv_id", requested_arxiv_ids=ids, missing_arxiv_ids=missing)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if ids:
        response_path = cache_path.with_suffix(".xml")
        response_path.write_bytes(response.content)
        payload.update(metadata_response_ref=str(response_path),
                       metadata_response_sha256=hashlib.sha256(response.content).hexdigest())
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return ToolResult(ok=True, output=payload, evidence_refs=[str(cache_path)])


async def web_search_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Web search provider abstraction with a domain allowlist."""
    settings = get_settings()
    if not settings.mars_enable_network_tools:
        return ToolResult(
            ok=False,
            error="network tools are disabled; set MARS_ENABLE_NETWORK_TOOLS=true",
        )
    query = str(args.get("q") or args.get("query") or "").strip()
    if not query:
        return ToolResult(ok=False, error="query (q) is required")
    domains = args.get("domains", [])
    if not isinstance(domains, list) or not all(isinstance(d, str) for d in domains):
        return ToolResult(ok=False, error="domains must be an allowlist of strings")
    allowed = tuple(
        item.strip()
        for item in settings.mars_web_search_allowlist.split(",")
        if item.strip()
    )
    if not allowed:
        return ToolResult(ok=False, error="web_search allowlist is empty")
    if not domains:
        return ToolResult(ok=False, error="domains must include at least one allowlisted domain")
    rejected = [d for d in domains if d not in allowed]
    if rejected:
        return ToolResult(ok=False, error=f"domains are not allowlisted: {rejected}")
    top_k = max(1, min(int(args.get("top_k", 5) or 5), 10))
    provider = settings.mars_web_search_provider
    if not provider:
        return ToolResult(
            ok=False,
            error=(
                "web_search provider is not configured; set "
                "MARS_WEB_SEARCH_PROVIDER=zhipu|brave|tavily|serper"
            ),
        )
    try:
        hits = await _call_web_search_provider(
            provider=provider,
            query=query,
            domains=tuple(domains),
            top_k=top_k,
        )
    except ValueError as exc:
        return ToolResult(ok=False, error=str(exc))
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, error=f"web_search request failed: {type(exc).__name__}: {exc}")
    filtered = _filter_hits_by_domain(hits, tuple(domains))
    return ToolResult(
        ok=True,
        output={
            "provider": provider,
            "query": query,
            "domains": domains,
            "hits": filtered[:top_k],
            "raw_hit_count": len(hits),
            "filtered_hit_count": len(filtered),
            "empty_reason": "no provider hits" if not hits else ("all hits outside requested domains" if not filtered else ""),
        },
        evidence_refs=[hit["url"] for hit in filtered[:top_k] if hit.get("url")],
    )


def _parse_arxiv(xml_text: str, *, date_from: str) -> list[dict[str, Any]]:
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml_text)
    out: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", ns):
        published = _entry_text(entry, "published", ns)
        if date_from and published[:10] < date_from:
            continue
        arxiv_id = _entry_text(entry, "id", ns)
        out.append(
            {
                "id": arxiv_id.split("/abs/", 1)[-1],
                "title": " ".join(_entry_text(entry, "title", ns).split()),
                "summary": " ".join(_entry_text(entry, "summary", ns).split()),
                "published": published,
                "url": arxiv_id,
                "pdf_url": _pdf_url_for_source(arxiv_id),
                "authors": [
                    _entry_text(author, "name", ns)
                    for author in entry.findall("atom:author", ns)
                ],
                "evidence_ref": arxiv_id,
            }
        )
    return out


def _entry_text(node: ET.Element, name: str, ns: dict[str, str]) -> str:
    child = node.find(f"atom:{name}", ns)
    return child.text.strip() if child is not None and child.text else ""


def _cache_dir() -> Path:
    from app.harness.kb.stores import get_stores
    return get_stores().base / "literature" / "_arxiv_cache"


def _cache_key(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _source_download_dir(ctx: ToolContext) -> Path:
    run_root = str(ctx.extra.get("run_root") or "").strip()
    if run_root:
        return Path(run_root) / ctx.agent / "research" / "downloads"
    run = ctx.run_id or "adhoc"
    return repo_root() / "knowledge" / "literature" / "_source_cache" / run


def _pdf_url_for_source(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in {"arxiv.org", "export.arxiv.org"}:
        return ""
    if "/pdf/" in parsed.path:
        return url
    if "/abs/" not in parsed.path:
        return ""
    arxiv_id = parsed.path.split("/abs/", 1)[-1]
    return f"https://arxiv.org/pdf/{arxiv_id}.pdf"


def _looks_like_pdf(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.path.lower().endswith(".pdf") or "/pdf/" in parsed.path


def _safe_source_basename(title: str, *, index: int) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", title).strip("._-").lower()
    if not cleaned:
        cleaned = f"source_{index}"
    return f"{index:02d}_{cleaned[:80]}"


def _extract_pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except Exception:
        return ""
    try:
        reader = PdfReader(BytesIO(data))
        parts: list[str] = []
        for page_index, page in enumerate(reader.pages[:4], 1):
            text = page.extract_text() or ""
            if text.strip():
                parts.append(f"[page {page_index}]\n{text.strip()}")
        return "\n\n".join(parts)[:_MAX_SOURCE_TEXT_CHARS]
    except Exception:
        return ""


def _extract_html_text(data: bytes, encoding: str | None) -> str:
    text = data.decode(encoding or "utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_MAX_SOURCE_TEXT_CHARS]


def _render_source_summary(
    *,
    title: str,
    source_url: str,
    download_url: str,
    source_type: str,
    abstract: str,
    extracted: str,
) -> str:
    evidence = abstract.strip() or extracted.strip()
    if not evidence:
        evidence = "下载成功，但未能抽取正文；使用标题和原始链接作为证据锚点。"
    return "\n".join(
        [
            f"# {title or source_url}",
            "",
            f"- Source: {source_url}",
            f"- Download: {download_url}",
            f"- Type: {source_type}",
            "",
            "## 摘要",
            evidence[:1600],
            "",
            "## 作为上下文的用法",
            "- 作为外部文献/技术资料证据进入 `idea_source_summaries`。",
            "- Proposal 只能引用与任务和代码仓约束一致的结论。",
            "- 若正文抽取为空，后续推理应把该来源标为弱证据。",
            "",
        ]
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


async def _respect_arxiv_rate_limit() -> None:
    global _LAST_ARXIV_CALL
    async with _ARXIV_LOCK:
        elapsed = time.monotonic() - _LAST_ARXIV_CALL
        if elapsed < _ARXIV_MIN_INTERVAL_SECONDS:
            await asyncio.sleep(_ARXIV_MIN_INTERVAL_SECONDS - elapsed)
        _LAST_ARXIV_CALL = time.monotonic()


async def _call_web_search_provider(
    *,
    provider: str,
    query: str,
    domains: tuple[str, ...],
    top_k: int,
) -> list[dict[str, str]]:
    settings = get_settings()
    if provider == "zhipu":
        if not settings.zhipu_api_key:
            raise ValueError("ZHIPU_API_KEY is required for zhipu web_search")
        if len(query) > 70:
            raise ValueError("Zhipu search query must be <=70 characters; use concise terms")
        payload: dict[str, Any] = {"search_query": query, "search_engine": "search_std",
                                   "search_intent": False, "count": top_k, "content_size": "high"}
        if len(domains) == 1:
            payload["search_domain_filter"] = domains[0]
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post("https://open.bigmodel.cn/api/paas/v4/web_search",
                                         headers={"Authorization": "Bearer " + settings.zhipu_api_key},
                                         json=payload)
            response.raise_for_status()
        raw = response.json()
        if not isinstance(raw, dict) or not isinstance(raw.get("search_result"), list):
            raise ValueError("invalid Zhipu search response: search_result array missing")
        return [{"title": str(x.get("title", "")), "url": str(x.get("link", "")),
                 "snippet": str(x.get("content", "")), "source": "zhipu"}
                for x in raw["search_result"] if isinstance(x, dict)]
    if provider == "tavily":
        if not settings.tavily_api_key:
            raise ValueError("TAVILY_API_KEY is required for tavily web_search")
        payload = {
            "api_key": settings.tavily_api_key,
            "query": query,
            "max_results": top_k,
            "include_domains": list(domains),
            "search_depth": "basic",
        }
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post("https://api.tavily.com/search", json=payload)
            response.raise_for_status()
        raw = response.json()
        results = raw.get("results", []) if isinstance(raw, dict) else []
        return [
            {
                "title": str(item.get("title", "")),
                "url": str(item.get("url", "")),
                "snippet": str(item.get("content", "")),
                "source": "tavily",
            }
            for item in results
            if isinstance(item, dict)
        ]
    if provider == "brave":
        if not settings.brave_search_api_key:
            raise ValueError("BRAVE_SEARCH_API_KEY is required for brave web_search")
        headers = {"X-Subscription-Token": settings.brave_search_api_key}
        params = {"q": _domain_query(query, domains), "count": str(top_k)}
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params=params,
                headers=headers,
            )
            response.raise_for_status()
        raw = response.json()
        web = raw.get("web", {}) if isinstance(raw, dict) else {}
        results = web.get("results", []) if isinstance(web, dict) else []
        return [
            {
                "title": str(item.get("title", "")),
                "url": str(item.get("url", "")),
                "snippet": str(item.get("description", "")),
                "source": "brave",
            }
            for item in results
            if isinstance(item, dict)
        ]
    if provider == "serper":
        if not settings.serper_api_key:
            raise ValueError("SERPER_API_KEY is required for serper web_search")
        headers = {"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"}
        payload = {"q": _domain_query(query, domains), "num": top_k}
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                "https://google.serper.dev/search",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
        raw = response.json()
        results = raw.get("organic", []) if isinstance(raw, dict) else []
        return [
            {
                "title": str(item.get("title", "")),
                "url": str(item.get("link", "")),
                "snippet": str(item.get("snippet", "")),
                "source": "serper",
            }
            for item in results
            if isinstance(item, dict)
        ]
    raise ValueError(f"unsupported web_search provider '{provider}'")


def _domain_query(query: str, domains: tuple[str, ...]) -> str:
    domain_expr = " OR ".join(f"site:{domain}" for domain in domains)
    return f"{query} ({domain_expr})" if domain_expr else query


def _filter_hits_by_domain(
    hits: list[dict[str, str]],
    domains: tuple[str, ...],
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for hit in hits:
        url = hit.get("url", "")
        host = urllib.parse.urlparse(url).hostname or ""
        if any(host == domain or host.endswith(f".{domain}") for domain in domains):
            out.append(hit)
    return out
