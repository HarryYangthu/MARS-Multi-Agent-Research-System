"""Literature research: paper APIs (arXiv / OpenAlex / Crossref) + best-effort web search, reading, evidence cards.

Every evidence card must carry a verbatim quote that is checked against the fetched text, so a card cannot be
written from the model's memory alone. Which sources exist is environment-specific (configs/sources.yaml): public
engines can be switched off or pointed at mirrors, intranet JSON search APIs added, and full text fetched by DOI
from subscription libraries (institutional IP access). Proxies come from HTTPS_PROXY / NO_PROXY / DDGS_PROXY.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HEADERS = {"User-Agent": "MARS-research/0.1 (mailto:research-tooling@localhost)"}
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = {"engines": {"arxiv": {}, "openalex": {}, "crossref": {}, "web": {}}, "fulltext": [],
                   "on_unavailable": "continue"}


def load_sources(path: Path | None = None) -> dict[str, Any]:
    """configs/sources.yaml or MARS_SOURCES_CONFIG; without a file the four public engines are used."""
    import yaml
    path = path or Path(os.environ.get("MARS_SOURCES_CONFIG") or ROOT / "configs/sources.yaml")
    return {**DEFAULT_SOURCES, **(yaml.safe_load(path.read_text(encoding="utf-8")) or {})} if path.is_file() else DEFAULT_SOURCES


def _get(url: str, timeout: float = 30, attempts: int = 4) -> bytes:
    """GET with backoff on rate limits and transient TLS/proxy failures."""
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as error:  # noqa: BLE001 - retried, then re-raised
            if attempt == attempts - 1 or (getattr(error, "code", None) not in (None, 429, 500, 502, 503, 504)):
                raise
            time.sleep(3 * 2 ** attempt)
    raise AssertionError("unreachable")


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def arxiv(query: str, limit: int = 6, base_url: str = "https://export.arxiv.org/api/query") -> list[dict[str, Any]]:
    words = [w for w in re.findall(r"[A-Za-z0-9\-]+", query) if len(w) > 2][:6]
    terms = " AND ".join(f"all:{w}" for w in words)
    data = _get(base_url + "?" + urllib.parse.urlencode({"search_query": terms, "max_results": limit}))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    items = []
    for entry in ET.fromstring(data).findall("a:entry", ns):
        link = entry.findtext("a:id", "", ns)
        items.append({"source": "arxiv", "title": " ".join(entry.findtext("a:title", "", ns).split()),
                      "abstract": " ".join(entry.findtext("a:summary", "", ns).split()),
                      "year": entry.findtext("a:published", "", ns)[:4], "url": link,
                      "pdf_url": link.replace("/abs/", "/pdf/")})
    return items


def openalex(query: str, limit: int = 6, base_url: str = "https://api.openalex.org/works") -> list[dict[str, Any]]:
    params = {"search": query, "per-page": limit, "mailto": "research-tooling@localhost"}  # polite pool
    data = json.loads(_get(base_url + "?" + urllib.parse.urlencode(params)))
    items = []
    for work in data.get("results", []):
        inverted = work.get("abstract_inverted_index") or {}
        words = sorted(((pos, word) for word, positions in inverted.items() for pos in positions))
        location = work.get("best_oa_location") or {}
        items.append({"source": "openalex", "title": work.get("display_name") or "", "abstract": " ".join(w for _, w in words),
                      "year": str(work.get("publication_year") or ""), "url": work.get("doi") or work.get("id"),
                      "pdf_url": location.get("pdf_url") or ""})
    return items


def crossref(query: str, limit: int = 5, base_url: str = "https://api.crossref.org/works") -> list[dict[str, Any]]:
    data = json.loads(_get(base_url + "?" + urllib.parse.urlencode({"query": query, "rows": limit})))
    items = []
    for item in data["message"]["items"]:
        abstract = re.sub(r"<[^>]+>", " ", item.get("abstract", ""))
        items.append({"source": "crossref", "title": (item.get("title") or [""])[0], "abstract": " ".join(abstract.split()),
                      "year": str((item.get("issued", {}).get("date-parts") or [[""]])[0][0]),
                      "url": "https://doi.org/" + item["DOI"], "pdf_url": ""})
    return items


def web(query: str, limit: int = 5, attempts: int = 3) -> list[dict[str, Any]]:
    """Scraping-based metasearch; unstable by nature, so retried and treated as supplementary."""
    from ddgs import DDGS
    for attempt in range(attempts):
        try:
            results = DDGS(timeout=20).text(query, max_results=limit)
            return [{"source": "web", "title": r.get("title", ""), "abstract": r.get("body", ""), "year": "",
                     "url": r.get("href", ""), "pdf_url": ""} for r in results]
        except Exception:  # noqa: BLE001 - ddgs raises on blocked/empty pages; retry with backoff
            time.sleep(2 * (attempt + 1))
    return []


def json_api(query: str, limit: int = 6, *, url: str, items: str, fields: dict[str, str], source: str) -> list[dict[str, Any]]:
    """An intranet literature system with a JSON search API: URL template ({query}, {limit}), dotted path to the
    result list, and a map from title/abstract/year/url/pdf_url to dotted record fields."""
    def pick(record: Any, path: str) -> Any:
        return functools.reduce(lambda v, k: v.get(k) if isinstance(v, dict) else None, path.split("."), record) if path else record
    records = pick(json.loads(_get(url.format(query=urllib.parse.quote(query), limit=limit))), items) or []
    return [{"source": source, **{k: str(pick(r, fields[k]) or "") if k in fields else "" for k in
                                  ("title", "abstract", "year", "url", "pdf_url")}} for r in records[:limit]]


class Library:
    """Search, fetch and cache sources for one run."""

    def __init__(self, cache: Path, sources: dict[str, Any] | None = None) -> None:
        self.cache = cache
        self.cache.mkdir(parents=True, exist_ok=True)
        self.log: list[dict[str, Any]] = []
        self.sources = sources or load_sources()

    def engines(self) -> list[tuple[str, bool, Any]]:
        """(name, is_web, search function) for every enabled engine in the source registry."""
        builtin, found = {"arxiv": arxiv, "openalex": openalex, "crossref": crossref, "web": web}, []
        for name, spec in self.sources["engines"].items():
            spec = spec or {}
            if spec.get("enabled", True) is False:
                continue
            kind = spec.get("kind", name)
            fn = (functools.partial(json_api, url=spec["url"], items=spec.get("items", ""), fields=spec["fields"], source=name)
                  if kind == "json" else functools.partial(builtin[kind], **({"base_url": spec["base_url"]} if spec.get("base_url") else {})))
            found.append((name, kind == "web", fn))
        return found

    def search(self, paper_queries: list[str], web_queries: list[str]) -> list[dict[str, Any]]:
        def run(name: str, fn: Any, query: str) -> list[dict[str, Any]]:
            try:
                found = fn(query)
                self.log.append({"engine": name, "query": query, "hits": len(found)})
                return [{**item, "query": query} for item in found]
            except Exception as error:  # noqa: BLE001 - one engine failing must not stop research
                self.log.append({"engine": name, "query": query, "error": f"{type(error).__name__}: {error}"[:300]})
                return []

        def engine_queue(engine: tuple[str, bool, Any]) -> list[dict[str, Any]]:  # one engine at a time, politely spaced
            name, is_web, fn = engine
            found = []
            for query in web_queries if is_web else paper_queries:
                found += run(name, fn, query)
                time.sleep(1.0)
            return found

        engines = self.engines()
        with ThreadPoolExecutor(max_workers=max(1, len(engines))) as pool:
            results = [item for batch in pool.map(engine_queue, engines) for item in batch]
        unique: dict[str, dict[str, Any]] = {}
        for item in results:
            key = _norm_title(item["title"])
            if key and (key not in unique or len(item.get("abstract", "")) > len(unique[key].get("abstract", ""))):
                unique[key] = item
        items = list(unique.values())
        for index, item in enumerate(items):
            item["id"] = f"S{index:03d}"
        return items

    def fetch(self, item: dict[str, Any], max_chars: int = 30000) -> str:
        """Full text when reachable (arXiv/OA PDF or web page); otherwise the abstract only."""
        url = item.get("pdf_url") or item.get("url") or ""
        key = hashlib.sha256(url.encode()).hexdigest()[:16]
        cached = self.cache / f"{key}.txt"
        if cached.exists():
            text = cached.read_text(encoding="utf-8")
            item["full_text"] = len(text) > len(item.get("title", "")) + len(item.get("abstract", "")) + 200
            return text
        text = ""
        try:
            item["pdf_url"] = item.get("pdf_url") or self.fulltext_url(item)
            if item.get("pdf_url"):
                import fitz  # PyMuPDF
                data = _get(item["pdf_url"], timeout=60)
                with fitz.open(stream=data, filetype="pdf") as doc:
                    text = "\n".join(page.get_text() for page in doc)[:max_chars]
            elif item.get("source") == "web" and url:
                import trafilatura
                text = (trafilatura.extract(_get(url, timeout=30).decode("utf-8", "replace")) or "")[:max_chars]
        except Exception as error:  # noqa: BLE001 - unreachable full text falls back to the abstract
            self.log.append({"fetch": url, "error": f"{type(error).__name__}: {error}"[:300]})
        if len(text) < 500:
            text = f"{item.get('title', '')}\n\n{item.get('abstract', '')}"
            item["full_text"] = False
        else:
            item["full_text"] = True
        cached.write_text(text, encoding="utf-8")
        return text

    def fulltext_url(self, item: dict[str, Any]) -> str:
        """Subscription full text by DOI (institutional IP access): resolve the DOI, take the id from the landing URL
        with the rule's regex, fill the rule's pdf_url template ({0} = first group, {doi})."""
        doi = re.search(r"10\.\d{4,9}/[^\s?#]+", item.get("url") or "")
        for rule in self.sources.get("fulltext") or []:
            if doi and doi.group(0).lower().startswith(rule["doi_prefix"].lower()):
                request = urllib.request.Request(rule["resolve"].format(doi=doi.group(0)), headers=HEADERS)
                with urllib.request.urlopen(request, timeout=30) as response:
                    found = re.search(rule["match"], response.url)
                if found:
                    return rule["pdf_url"].format(*found.groups(), doi=doi.group(0))
        return ""


LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl"}


def _normalize(text: str) -> str:
    """PDF-robust form: NFKC, ligatures expanded, hyphenated line breaks joined, only lowercase letters/digits kept."""
    import unicodedata
    text = unicodedata.normalize("NFKC", text)
    for key, value in LIGATURES.items():
        text = text.replace(key, value)
    text = re.sub(r"-\s*\n\s*", "", text)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text.lower())


def quote_found(quote: str, text: str, coverage: float = 0.9) -> bool:
    """Verbatim support check: the normalized quote must appear in the source, or at least `coverage` of it must
    match as one contiguous run (tolerates PDF extraction artefacts such as a dropped glyph, not paraphrase)."""
    from difflib import SequenceMatcher
    q, t = _normalize(quote), _normalize(text)
    if len(q) < 25:
        return False
    if q in t:
        return True
    match = SequenceMatcher(None, q, t, autojunk=False).find_longest_match(0, len(q), 0, len(t))
    return match.size >= coverage * len(q)
