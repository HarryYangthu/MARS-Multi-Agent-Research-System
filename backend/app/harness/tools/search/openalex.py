"""Explicit OpenAlex metadata search with allowlisted original PDF locations."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.harness.agent_loop.trace import atomic_json
from app.harness.tools.registry import ToolContext, ToolResult
from app.settings import get_settings


def parse_openalex(payload: dict[str, Any], *, allowed_domains: tuple[str, ...], top_k: int) -> list[dict[str, Any]]:
    """Normalize actual metadata; only return original PDFs permitted by policy."""
    hits: list[dict[str, Any]] = []
    for work in payload.get("results", []):
        if not isinstance(work, dict):
            continue
        for location in work.get("locations", []):
            if not isinstance(location, dict):
                continue
            pdf = location.get("pdf_url")
            landing = location.get("landing_page_url")
            if not isinstance(pdf, str) or not isinstance(landing, str):
                continue
            parsed = urlparse(pdf)
            host = (parsed.hostname or "").lower()
            if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}
                    or not any(host == domain or host.endswith("." + domain) for domain in allowed_domains)):
                continue
            words: dict[int, str] = {}
            inverted = work.get("abstract_inverted_index")
            if isinstance(inverted, dict):
                for word, positions in inverted.items():
                    if isinstance(word, str) and isinstance(positions, list):
                        for position in positions:
                            if type(position) is int and 0 <= position < 2000:
                                words[position] = word
            hits.append({"title": str(work.get("title") or work.get("display_name") or ""),
                         "url": landing, "pdf_url": pdf, "summary": " ".join(words[p] for p in sorted(words)),
                         "source": "openalex", "metadata_url": str(work.get("id") or ""),
                         "doi": str(work.get("doi") or ""), "published": str(work.get("publication_date") or "")})
            break
        if len(hits) >= top_k:
            break
    return hits


async def openalex_search_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    settings = get_settings()
    if not settings.mars_enable_network_tools:
        return ToolResult(ok=False, error="network tools are disabled")
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolResult(ok=False, error="query is required")
    top_k = args.get("top_k", 5)
    if type(top_k) is not int or not 1 <= top_k <= 10:
        return ToolResult(ok=False, error="top_k must be an integer between 1 and 10")
    if not ctx.extra.get("run_root"):
        return ToolResult(ok=False, error="run_root required for search receipt")
    allowed = tuple(domain.strip().lower() for domain in settings.mars_web_search_allowlist.split(",") if domain.strip())
    if not allowed:
        return ToolResult(ok=False, error="PDF source domain allowlist is empty")
    url = "https://api.openalex.org/works"
    params = {"search": query, "filter": "is_oa:true", "per-page": str(min(50, top_k * 5))}
    receipt: dict[str, Any] = {"source": "openalex", "endpoint": url, "request_params": params,
                               "requested_at": datetime.now(timezone.utc).isoformat(), "ok": False}
    target = Path(str(ctx.extra["run_root"])) / ctx.agent / "research" / "searches"
    request_hash = hashlib.sha256((receipt["requested_at"] + query).encode()).hexdigest()
    receipt_path = target / (request_hash + ".json")
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response = await client.get(url, params=params, headers={"User-Agent": "MARS-Research/1.0"})
            receipt["status_code"] = response.status_code
            response.raise_for_status()
            raw = response.json()
        if not isinstance(raw, dict) or not isinstance(raw.get("results"), list):
            raise ValueError("OpenAlex response missing results array")
        receipt.update(ok=True, response=raw)
        hits = parse_openalex(raw, allowed_domains=allowed, top_k=top_k)
        atomic_json(receipt_path, receipt)
        return ToolResult(ok=True, output={"query": query, "source": "openalex", "hits": hits,
                         "metadata_candidates": len(raw["results"]), "pdf_domain_allowlist": list(allowed),
                         "selection_policy": "First matching HTTPS PDF location within configured source domains; metadata relevance ranking.",
                         "search_receipt": str(receipt_path), "cached": False,
                         "empty_reason": "No returned work had a permitted PDF location" if not hits else ""},
                          evidence_refs=[str(receipt_path)])
    except (httpx.HTTPError, ValueError) as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        atomic_json(receipt_path, receipt)
        return ToolResult(ok=False, error=receipt["error"], output={"source": "openalex", "search_receipt": str(receipt_path)})
