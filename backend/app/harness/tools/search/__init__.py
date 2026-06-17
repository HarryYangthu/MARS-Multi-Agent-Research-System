"""Search tools registered through the generic tool registry."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.harness.kb.memory_writer import write_to_zone
from app.harness.tools.registry import ToolContext, ToolResult
from app.settings import get_settings, repo_root

_ARXIV_MIN_INTERVAL_SECONDS = 3.0
_ARXIV_LOCK = asyncio.Lock()
_LAST_ARXIV_CALL = 0.0


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
    query = str(args.get("q") or args.get("query") or "").strip()
    if not query:
        return ToolResult(ok=False, error="query (q) is required")
    top_k = max(1, min(int(args.get("top_k", 5) or 5), 20))
    categories = args.get("categories", [])
    category_query = ""
    if isinstance(categories, list) and categories:
        category_query = " AND (" + " OR ".join(f"cat:{c}" for c in categories) + ")"
    date_from = str(args.get("date_from", "") or "")
    search_query = query + category_query
    cache_key = _cache_key({"q": search_query, "top_k": top_k, "date_from": date_from})
    cache_path = _cache_dir() / f"{cache_key}.json"
    if cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        return ToolResult(ok=True, output={**payload, "cached": True})
    params = {
        "search_query": search_query,
        "start": "0",
        "max_results": str(top_k),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    try:
        await _respect_arxiv_rate_limit()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, error=f"arXiv request failed: {exc}")
    hits = _parse_arxiv(response.text, date_from=date_from)
    for hit in hits:
        write_to_zone(
            zone="literature",
            text=f"{hit['title']}\n\n{hit['summary']}",
            metadata={
                "source": "arxiv",
                "arxiv_id": hit["id"],
                "title": hit["title"],
                "url": hit["url"],
                "published": hit["published"],
                "run_id": ctx.run_id,
            },
        )
    payload = {
        "query": query,
        "url": url,
        "hits": hits,
        "cached": False,
        "cached_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
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
                "MARS_WEB_SEARCH_PROVIDER=brave|tavily|serper"
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
        return ToolResult(ok=False, error=f"web_search request failed: {exc}")
    filtered = _filter_hits_by_domain(hits, tuple(domains))
    return ToolResult(
        ok=True,
        output={
            "provider": provider,
            "query": query,
            "domains": domains,
            "hits": filtered[:top_k],
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
                "id": arxiv_id.rsplit("/", 1)[-1],
                "title": " ".join(_entry_text(entry, "title", ns).split()),
                "summary": " ".join(_entry_text(entry, "summary", ns).split()),
                "published": published,
                "url": arxiv_id,
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
    return repo_root() / "knowledge" / "literature" / "_arxiv_cache"


def _cache_key(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


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
        async with httpx.AsyncClient(timeout=10.0) as client:
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
        async with httpx.AsyncClient(timeout=10.0) as client:
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
        async with httpx.AsyncClient(timeout=10.0) as client:
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
