"""Official arXiv HTML metadata fallback when the Atom API rejects a request.

Search ranking is the website's relevance ranking, not an emulated API result.
Only plain, unfiltered keywords are supported; advanced API filters fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
import hashlib
from pathlib import Path
import re
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode, urlparse

import httpx

from app.harness.tools.search.arxiv_query import ARXIV_ID, verify_arxiv_lookup


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    children: list[Node | str] = field(default_factory=list)

    def find(self, tag: str = "", cls: str = "") -> list[Node]:
        found = []
        for child in self.children:
            if isinstance(child, Node):
                if (not tag or child.tag == tag) and (not cls or cls in child.attrs.get("class", "").split()):
                    found.append(child)
                found.extend(child.find(tag, cls))
        return found

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children
                       if not isinstance(c, Node) or c.tag != "a" or "is-size-7" not in c.attrs.get("class", "").split())


class Page(HTMLParser):
    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        if not re.search(r"</html\s*>\s*$", text, re.I):
            raise ValueError("incomplete arXiv HTML response")
        self.root = Node("root", {})
        self.stack = [self.root]
        self.feed(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def _clean(text: str) -> str:
    return " ".join(text.split())


def _identity(url: str, kind: str = "abs") -> str:
    parsed = urlparse(url)
    value = parsed.path.removeprefix(f"/{kind}/")
    if (parsed.scheme != "https" or parsed.netloc != "arxiv.org" or parsed.query or parsed.fragment
            or not parsed.path.startswith(f"/{kind}/") or not re.fullmatch(ARXIV_ID, value)):
        raise ValueError("invalid official arXiv metadata URL")
    return value


def parse_search(text: str) -> list[dict[str, Any]]:
    root = Page(text).root
    rows = root.find("li", "arxiv-result")
    headings = " ".join(_clean(n.text()) for n in root.find("h1"))
    if not rows and not re.search(r"Sorry, your query .* produced no results", _clean(root.text()), re.I):
        raise ValueError("unrecognized arXiv search page; cannot treat it as zero results")
    if rows and not re.search(r"Showing .* results? for", headings):
        raise ValueError("arXiv search result heading missing")
    hits = []
    for row in rows:
        links = row.find("p", "list-title")
        titles, summaries, authors = row.find("p", "title"), row.find("span", "abstract-full"), row.find("p", "authors")
        if not links or not titles or not summaries or not authors:
            raise ValueError("incomplete arXiv search metadata")
        anchors = links[0].find("a")
        url = anchors[0].attrs.get("href", "") if anchors else ""
        paper_id = _identity(url)
        pdf = next((a.attrs.get("href", "") for a in anchors if _clean(a.text()).lower() == "pdf"), "")
        if _identity(pdf, "pdf") != paper_id:
            raise ValueError("arXiv search PDF identity mismatch")
        dates = _clean(row.text())
        submitted = re.search(r"v1 submitted (\d{1,2} \w+, \d{4})", dates) or re.search(r"Submitted (\d{1,2} \w+, \d{4})", dates)
        if not submitted:
            raise ValueError("arXiv original submission date missing")
        hits.append({"id": paper_id, "title": _clean(titles[0].text()), "summary": _clean(summaries[0].text()),
                     "published": datetime.strptime(submitted[1], "%d %B, %Y").date().isoformat(),
                     "url": url, "pdf_url": pdf, "authors": [_clean(a.text()) for a in authors[0].find("a")],
                     "evidence_ref": url})
    return hits


def parse_abstract(text: str, requested_id: str) -> dict[str, Any]:
    root = Page(text).root
    meta: dict[str, list[str]] = {}
    for node in root.find("meta"):
        name = node.attrs.get("name") or node.attrs.get("property", "")
        meta.setdefault(name, []).append(node.attrs.get("content", ""))
    def required(name: str) -> str:
        values = meta.get(name, [])
        if len(values) != 1 or not values[0].strip():
            raise ValueError(f"missing or ambiguous arXiv citation metadata: {name}")
        return values[0]
    url = required("og:url")
    actual_id = _identity(url)
    if not re.search(r"v[1-9][0-9]*$", actual_id):
        raise ValueError("arXiv abstract page does not establish its version")
    declared_id = required("citation_arxiv_id")
    if re.sub(r"v[1-9][0-9]*$", "", actual_id) != re.sub(r"v[1-9][0-9]*$", "", declared_id):
        raise ValueError("arXiv citation identifier mismatch")
    # Pin the PDF to the explicitly observed page version, as the Atom path does.
    pdf_id = _identity(required("citation_pdf_url"), "pdf")
    verify_arxiv_lookup([pdf_id], [{"url": url, "title": required("citation_title")}])
    hit = {"id": actual_id, "url": url, "title": _clean(required("citation_title")),
           "summary": _clean(required("citation_abstract")),
           "published": datetime.strptime(required("citation_date"), "%Y/%m/%d").date().isoformat(),
           "authors": meta.get("citation_author", []), "pdf_url": f"https://arxiv.org/pdf/{actual_id}", "evidence_ref": url}
    verify_arxiv_lookup([requested_id], [hit])
    return hit


def fallback_urls(args: dict[str, Any], ids: list[str] | None, query: str) -> list[str]:
    if ids:
        return ["https://arxiv.org/abs/" + value for value in ids]
    if (args.get("categories") or args.get("date_from") or args.get("sort_by", "relevance") != "relevance"
            or re.search(r"\b(?:AND|OR|ANDNOT)\b|:", query)):
        raise ValueError("arXiv HTML fallback supports only plain keywords without API filters; filters were not relaxed")
    return ["https://arxiv.org/search/?" + urlencode({"query": query, "searchtype": "all", "abstracts": "show", "order": "", "size": 50})]


async def fetch_metadata(args: dict[str, Any], ids: list[str] | None, query: str, top_k: int,
                         cache_path: Path, client: httpx.AsyncClient,
                         rate_limit: Callable[[], Awaitable[None]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    hits: list[dict[str, Any]] = []
    receipts = []
    for index, url in enumerate(fallback_urls(args, ids, query)):
        await rate_limit()
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError("arXiv HTML metadata exceeds 2 MiB")
        path = cache_path.with_suffix(f".{index}.html")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        receipts.append({"url": url, "path": str(path), "sha256": hashlib.sha256(raw).hexdigest()})
        text = raw.decode("utf-8")
        hits.extend([parse_abstract(text, ids[index])] if ids else parse_search(text)[:top_k])
    return hits, receipts


def verify_cached(payload: dict[str, Any], ids: list[str] | None, top_k: int, expected_urls: list[str]) -> None:
    receipts = payload["metadata_responses"]
    if not isinstance(receipts, list) or [row["url"] for row in receipts] != expected_urls:
        raise ValueError("arXiv HTML cache request mismatch")
    hits: list[dict[str, Any]] = []
    for index, row in enumerate(receipts):
        raw = Path(row["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("arXiv HTML cache hash mismatch")
        text = raw.decode("utf-8")
        hits.extend([parse_abstract(text, ids[index])] if ids else parse_search(text)[:top_k])
    if hits != payload["hits"]:
        raise ValueError("cached arXiv metadata differs from archived HTML")
