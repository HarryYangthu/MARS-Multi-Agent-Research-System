"""Official NeurIPS directory/citation metadata; PDF reading remains separate.

Only the observed /paper_files/paper/{year}/hash/*-Abstract.html layout is
supported. Paper URLs come from actual buttons, never slug substitution. An
alternate citation host is metadata, not a verified download or document alias.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import unquote, urljoin, urlparse
from uuid import uuid4

import httpx

from app.harness.agent_loop.trace import atomic_json
from app.harness.tools.registry import ToolContext, ToolResult
from app.settings import get_settings

NEURIPS_HOST = "papers.nips.cc"
CITATION_HOSTS = frozenset({NEURIPS_HOST, "papers.neurips.cc", "proceedings.neurips.cc"})
RECEIPT_SCHEMA = "neurips.search_receipt.v1"
SELECTION_POLICY = "title_tokens_all.v1: all casefolded Unicode query words must occur in the directory title; directory order breaks ties; no semantic score"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _title(value: str) -> str:
    return " ".join(value.split())


def _tokens(value: str) -> list[str]:
    return sorted(set(re.findall(r"[^\W_]+", value.casefold())))


def _parameters(args: dict[str, Any]) -> tuple[str, int, int]:
    if set(args) - {"query", "year", "top_k"}:
        raise ValueError("unexpected NeurIPS search arguments")
    query, year, top_k = args.get("query"), args.get("year"), args.get("top_k", 3)
    if not isinstance(query, str) or not 1 <= len(query) <= 200 or not _tokens(query):
        raise ValueError("query must contain 1..200 characters of title words")
    if type(year) is not int or not 2000 <= year <= 2100:
        raise ValueError("year must be an explicit integer between 2000 and 2100")
    if type(top_k) is not int or not 1 <= top_k <= 3:
        raise ValueError("top_k must be an integer between 1 and 3")
    return query.strip(), year, top_k


def _official_url(url: str, *, citation: bool = False) -> str:
    parsed = urlparse(url)
    hosts = CITATION_HOSTS if citation else {NEURIPS_HOST}
    if (parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username or parsed.password
            or parsed.port not in {None, 443} or parsed.fragment or parsed.params or parsed.query
            or any(part in {".", ".."} for part in unquote(parsed.path).split("/"))
            or "\\" in unquote(url) or any(c.isspace() or ord(c) < 32 for c in unquote(url))):
        raise ValueError("NeurIPS metadata requires an official HTTPS URL without credentials, query or traversal")
    return url


def _paper_id(url: str, year: int, *, pdf: bool = False, citation: bool = False) -> str:
    _official_url(url, citation=citation)
    suffix = r"file/([0-9a-f]{32})-Paper\.pdf" if pdf else r"hash/([0-9a-f]{32})-Abstract\.html"
    match = re.fullmatch(r"/paper_files/paper/" + str(year) + "/" + suffix, urlparse(url).path)
    if match is None:
        raise ValueError("unsupported NeurIPS year/path layout; requires observed Abstract.html/Paper.pdf paths, never guessed replacements")
    return match.group(1)


def _complete_html(text: str) -> None:
    if not text.strip() or not re.search(r"<html(?:\s|>)", text, re.I) or not re.search(r"</html\s*>\s*$", text, re.I):
        raise ValueError("incomplete or empty NeurIPS HTML document")


def _attributes(attrs: list[tuple[str, str | None]]) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    for key, value in attrs:
        if key in values:
            raise ValueError("duplicate HTML attributes in NeurIPS metadata")
        values[key] = value
    return values


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metadata: dict[str, list[str]] = {}
        self.anchors: list[dict[str, str]] = []
        self.captures: dict[str, list[str]] = {}
        self.anchor: dict[str, str] | None = None
        self.capture: tuple[str, str, list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Other page elements can contain unrelated duplicated attributes.
        values = _attributes(attrs) if tag in {"a", "meta", "title", "h1", "p", "span"} else dict(attrs)
        if tag == "meta":
            name = (values.get("name") or "").lower()
            if name.startswith("citation_"):
                value = _title(values.get("content") or "")
                if not value:
                    raise ValueError("empty NeurIPS citation metadata")
                self.metadata.setdefault(name, []).append(value)
        if tag == "a":
            if self.anchor is not None:
                raise ValueError("nested NeurIPS anchor")
            self.anchor = {"href": values.get("href") or "", "text": "", "label": values.get("title") or ""}
        classes = (values.get("class") or "").split()
        key = next((name for name in ("book-meta", "paper-count", "paper-title") if name in classes), "")
        if tag == "title":
            key = "document-title"
        if key:
            if self.capture is not None:
                raise ValueError("nested NeurIPS title/count capture")
            self.capture = (tag, key, [])

    def handle_data(self, data: str) -> None:
        if self.anchor is not None:
            self.anchor["text"] += data
        if self.capture is not None:
            self.capture[2].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.anchor is not None:
            self.anchor["text"] = _title(self.anchor["text"])
            self.anchors.append(self.anchor)
            self.anchor = None
        if self.capture is not None and tag == self.capture[0]:
            self.captures.setdefault(self.capture[1], []).append(_title("".join(self.capture[2])))
            self.capture = None


def _parse(text: str) -> _PageParser:
    _complete_html(text)
    parser = _PageParser()
    parser.feed(text)
    parser.close()
    if parser.anchor is not None or parser.capture is not None:
        raise ValueError("incomplete NeurIPS metadata elements")
    return parser


def parse_neurips_directory(text: str, *, year: int) -> list[dict[str, str]]:
    parser = _parse(text)
    if parser.captures.get("book-meta") != [f"NeurIPS {year}"]:
        raise ValueError("NeurIPS directory year missing or inconsistent")
    counts = parser.captures.get("paper-count", [])
    if len(counts) != 1 or not re.fullmatch(r"[1-9][0-9]* papers", counts[0]):
        raise ValueError("NeurIPS directory paper count missing or unsupported")
    entries = []
    seen: set[str] = set()
    for anchor in parser.anchors:
        if anchor["label"] != "paper title":
            continue
        url = urljoin(f"https://{NEURIPS_HOST}/paper/{year}", anchor["href"])
        _paper_id(url, year)
        if not anchor["text"] or not anchor["href"] or url in seen:
            raise ValueError("empty or duplicate NeurIPS directory entry")
        seen.add(url)
        entries.append({"title": anchor["text"], "href": anchor["href"], "url": url})
    if len(entries) != int(counts[0].split()[0]):
        raise ValueError("NeurIPS directory entries do not match declared complete paper count")
    return entries


def _unique(values: dict[str, list[str]], name: str, *, optional: bool = False) -> str:
    items = values.get(name, [])
    if not items and optional:
        return ""
    if len(items) != 1 or not items[0]:
        raise ValueError(f"NeurIPS {name} must be present and unique")
    return items[0]


def parse_neurips_landing(text: str, *, entry: dict[str, str], year: int) -> dict[str, Any]:
    identifier = _paper_id(entry["url"], year)
    parser = _parse(text)
    title = _unique(parser.metadata, "citation_title")
    if (title.casefold() != _title(entry["title"]).casefold()
            or parser.captures.get("paper-title") != [title]
            or parser.captures.get("document-title") != [title]):
        raise ValueError("NeurIPS directory, page title and citation_title disagree")
    authors = parser.metadata.get("citation_author", [])
    if not authors or len(set(a.casefold() for a in authors)) != len(authors):
        raise ValueError("NeurIPS citation authors missing or duplicated")
    if _unique(parser.metadata, "citation_publication_date") != str(year):
        raise ValueError("NeurIPS citation year differs from directory")
    venue = _unique(parser.metadata, "citation_journal_title")
    if venue != "Advances in Neural Information Processing Systems":
        raise ValueError("NeurIPS citation venue inconsistent")
    buttons = [a for a in parser.anchors if a["text"] == "Paper"]
    if len(buttons) != 1 or not buttons[0]["href"]:
        raise ValueError("NeurIPS actual Paper button missing or ambiguous")
    pdf = urljoin(entry["url"], buttons[0]["href"])
    if _paper_id(pdf, year, pdf=True) != identifier:
        raise ValueError("NeurIPS Paper button identifies a different document")
    citation_pdf = _unique(parser.metadata, "citation_pdf_url")
    if _paper_id(citation_pdf, year, pdf=True, citation=True) != identifier:
        raise ValueError("NeurIPS citation PDF identifies a different document")
    return {"title": title, "url": entry["url"], "pdf_url": pdf, "authors": authors,
            "published": str(year), "venue": venue, "doi": _unique(parser.metadata, "citation_doi", optional=True),
            "source": "neurips", "paper_href": buttons[0]["href"], "citation_pdf_url": citation_pdf,
            "citation_pdf_alias_verified": False}


def select_neurips_entries(entries: list[dict[str, str]], query: str, top_k: int) -> list[dict[str, str]]:
    terms = set(_tokens(query))
    if not terms:
        raise ValueError("query has no title words")
    return [entry for entry in entries if terms <= set(_tokens(entry["title"]))][:top_k]


def validate_html_transfer(*, status_code: int, content_type: str, content_encoding: str,
                           content_length: str | None, content_range: str | None,
                           received_bytes: int, byte_limit: int) -> None:
    if status_code != 200 or content_range is not None:
        raise ValueError("NeurIPS metadata requires full HTTP 200; redirects/partial responses unsupported")
    if content_type.split(";", 1)[0].strip().lower() != "text/html" or content_encoding.lower() not in {"", "identity"}:
        raise ValueError("NeurIPS metadata requires identity-encoded HTML")
    if not 0 < received_bytes <= byte_limit:
        raise ValueError("NeurIPS HTML empty or exceeds byte limit")
    if content_length is not None and (not content_length.isdigit() or int(content_length) != received_bytes):
        raise ValueError("NeurIPS HTML Content-Length mismatch")


async def _read_html(client: httpx.AsyncClient, url: str, path: Path, *, limit: int,
                     responses: list[dict[str, Any]]) -> str:
    _official_url(url)
    record: dict[str, Any] = {"url": url, "final_url": url, "body_ref": str(path), "byte_limit": limit, "complete": False}
    responses.append(record)
    data = bytearray()
    started = time.monotonic()
    try:
        async with client.stream("GET", url, headers={"User-Agent": "MARS-Research/1.0", "Accept-Encoding": "identity"}) as response:
            record.update(status_code=response.status_code, content_type=response.headers.get("content-type", ""),
                          content_encoding=response.headers.get("content-encoding", ""), content_length=response.headers.get("content-length"),
                          content_range=response.headers.get("content-range"), final_url=str(response.url))
            if response.status_code != 200 or str(response.url) != url:
                raise ValueError("NeurIPS requires HTTP 200 at the exact official URL; redirects unsupported")
            declared = record["content_length"]
            if declared is not None and (not declared.isdigit() or int(declared) > limit):
                raise ValueError("NeurIPS declared HTML size invalid or above limit")
            async for block in response.aiter_raw():
                if len(data) + len(block) > limit:
                    raise ValueError("NeurIPS HTML exceeds byte limit")
                data.extend(block)
            validate_html_transfer(status_code=record["status_code"], content_type=record["content_type"],
                content_encoding=record["content_encoding"], content_length=record["content_length"],
                content_range=record["content_range"], received_bytes=len(data), byte_limit=limit)
        text = bytes(data).decode("utf-8", errors="strict")
        _complete_html(text)
        path.write_bytes(data)  # Incomplete transfers are never archived as HTML.
        record["complete"] = True
        return text
    finally:
        record.update(bytes=len(data), sha256=_sha(bytes(data)), elapsed_seconds=time.monotonic() - started)


async def neurips_search_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    settings = get_settings()
    receipt: dict[str, Any] = {"schema": RECEIPT_SCHEMA, "ok": False, "hits": [], "responses": []}
    target: Path | None = None
    started = time.monotonic()
    try:
        query, year, top_k = _parameters(args)
        if not settings.mars_enable_network_tools:
            raise ValueError("network tools are disabled")
        allowed = {d.strip().lower() for d in settings.mars_web_search_allowlist.split(",") if d.strip()}
        if not any(NEURIPS_HOST == d or NEURIPS_HOST.endswith("." + d) for d in allowed):
            raise ValueError("papers.nips.cc is not allowlisted; NeurIPS directory search requires no API key")
        if not ctx.run_id or not ctx.extra.get("run_root") or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", ctx.agent):
            raise ValueError("auditable run_root and valid agent identifier required")
        run_root = Path(str(ctx.extra["run_root"])).resolve()
        target = run_root / ctx.agent / "research/searches/neurips" / uuid4().hex
        if not target.resolve().is_relative_to(run_root):
            raise ValueError("NeurIPS archive escapes run root")
        target.mkdir(parents=True, exist_ok=False)
        archive_root = target
        directory = f"https://{NEURIPS_HOST}/paper/{year}"
        limits = {"total_timeout_seconds": settings.mars_neurips_total_timeout_seconds,
                  "directory_bytes": settings.mars_neurips_directory_max_mib * 1024 * 1024,
                  "landing_bytes": settings.mars_neurips_landing_max_kib * 1024}
        receipt.update(request={"query": query, "year": year, "top_k": top_k, "directory_url": directory},
                       requested_at=datetime.now(timezone.utc).isoformat(), run_id=ctx.run_id, agent=ctx.agent,
                       limits=limits, selection_policy=SELECTION_POLICY, query_tokens=_tokens(query))

        async def query_directory() -> list[dict[str, Any]]:
            timeout = httpx.Timeout(connect=min(settings.mars_source_connect_timeout_seconds, limits["total_timeout_seconds"]),
                                    read=min(settings.mars_source_read_timeout_seconds, limits["total_timeout_seconds"]),
                                    write=limits["total_timeout_seconds"], pool=limits["total_timeout_seconds"])
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                text = await _read_html(client, directory, archive_root / "directory.html", limit=int(limits["directory_bytes"]), responses=receipt["responses"])
                entries = parse_neurips_directory(text, year=year)
                chosen = select_neurips_entries(entries, query, top_k)
                receipt.update(directory_entries=len(entries), matching_entries=sum(set(_tokens(query)) <= set(_tokens(e["title"])) for e in entries), selected_entries=chosen)
                hits = []
                for index, entry in enumerate(chosen):
                    html = await _read_html(client, entry["url"], archive_root / f"landing-{index}.html", limit=int(limits["landing_bytes"]), responses=receipt["responses"])
                    hit = parse_neurips_landing(html, entry=entry, year=year)
                    hit.update(query=query, year=year, directory_entry=entry,
                               directory_response_ref=receipt["responses"][0]["body_ref"], directory_response_sha256=receipt["responses"][0]["sha256"],
                               metadata_response_ref=receipt["responses"][-1]["body_ref"], metadata_response_sha256=receipt["responses"][-1]["sha256"])
                    hits.append(hit)
                return hits

        hits = await asyncio.wait_for(query_directory(), timeout=limits["total_timeout_seconds"])
        if time.monotonic() - started > limits["total_timeout_seconds"]:
            raise ValueError("NeurIPS total query budget exhausted")
        receipt.update(ok=True, hits=hits, empty_reason="no directory title matches all query words" if not hits else "")
    except asyncio.CancelledError:
        receipt.update(error="NeurIPS query cancelled; no metadata accepted", hits=[])
        raise
    except (ValueError, TypeError, KeyError, OSError, httpx.HTTPError, TimeoutError) as exc:
        receipt.update(error=f"{type(exc).__name__}: {exc}", hits=[])
    finally:
        receipt["elapsed_seconds"] = time.monotonic() - started
        if target is not None:
            atomic_json(target / "receipt.json", receipt)
    reference = str(target / "receipt.json") if target is not None else None
    checksum = _sha(Path(reference).read_bytes()) if reference else None
    output = {"source": "neurips", "query": args.get("query"), "year": args.get("year"),
              "hits": [{**hit, "search_receipt": reference, "search_receipt_sha256": checksum} for hit in receipt["hits"]],
              "search_receipt": reference, "search_receipt_sha256": checksum, "selection_policy": SELECTION_POLICY,
              "directory_entries": receipt.get("directory_entries", 0), "matching_entries": receipt.get("matching_entries", 0),
              "metadata_only": True, "pdf_downloaded": False, "empty_reason": receipt.get("empty_reason", ""),
              "elapsed_seconds": receipt["elapsed_seconds"]}
    return ToolResult(ok=receipt["ok"], output=output, error=receipt.get("error"), evidence_refs=[reference] if reference else [])


def verified_neurips_hit(hit: dict[str, Any]) -> bool:
    """Rebuild the exact original metadata chain; never trust supplied aliases."""
    try:
        path = Path(hit["search_receipt"])
        if path.is_symlink() or path.name != "receipt.json" or not path.is_file():
            return False
        settings = get_settings()
        directory_limit = settings.mars_neurips_directory_max_mib * 1024 * 1024
        landing_limit = settings.mars_neurips_landing_max_kib * 1024
        if path.stat().st_size > directory_limit + 3 * landing_limit:
            return False
        data = path.read_bytes()
        if _sha(data) != hit["search_receipt_sha256"]:
            return False
        receipt = json.loads(data)
        if receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("ok") is not True or receipt.get("selection_policy") != SELECTION_POLICY:
            return False
        request = receipt["request"]
        query, year, top_k = _parameters({k:request[k] for k in ("query", "year", "top_k")})
        directory = f"https://{NEURIPS_HOST}/paper/{year}"
        if request["directory_url"] != directory or receipt["query_tokens"] != _tokens(query):
            return False
        responses = receipt["responses"]
        if not isinstance(responses, list) or not 1 <= len(responses) <= top_k + 1:
            return False
        limits = receipt["limits"]
        if (type(limits["directory_bytes"]) is not int or not 0 < limits["directory_bytes"] <= directory_limit
                or type(limits["landing_bytes"]) is not int or not 0 < limits["landing_bytes"] <= landing_limit
                or type(limits["total_timeout_seconds"]) not in {int, float} or not 0 < limits["total_timeout_seconds"] <= 180
                or type(receipt["elapsed_seconds"]) not in {int, float} or not 0 <= receipt["elapsed_seconds"] <= limits["total_timeout_seconds"]):
            return False
        bodies = []
        for index, response in enumerate(responses):
            body = Path(response["body_ref"])
            if body.is_symlink() or body.resolve().parent != path.resolve().parent:
                return False
            limit = limits["directory_bytes"] if index == 0 else limits["landing_bytes"]
            if (response.get("complete") is not True or response["url"] != response["final_url"]
                    or response["byte_limit"] != limit or body.stat().st_size > limit):
                return False
            raw = body.read_bytes()
            if _sha(raw) != response["sha256"] or len(raw) != response["bytes"]:
                return False
            validate_html_transfer(status_code=response["status_code"], content_type=response["content_type"],
                content_encoding=response["content_encoding"], content_length=response["content_length"],
                content_range=response["content_range"], received_bytes=len(raw), byte_limit=limit)
            bodies.append(raw.decode("utf-8", errors="strict"))
        if responses[0]["url"] != directory:
            return False
        entries = parse_neurips_directory(bodies[0], year=year)
        selected = select_neurips_entries(entries, query, top_k)
        if (receipt["selected_entries"] != selected or len(bodies) != len(selected) + 1 or len(receipt["hits"]) != len(selected)
                or receipt["directory_entries"] != len(entries)
                or receipt["matching_entries"] != sum(set(_tokens(query)) <= set(_tokens(e["title"])) for e in entries)):
            return False
        rebuilt = []
        for index, entry in enumerate(selected):
            response = responses[index + 1]
            if response["url"] != entry["url"]:
                return False
            value = parse_neurips_landing(bodies[index + 1], entry=entry, year=year)
            value.update(query=query, year=year, directory_entry=entry,
                         directory_response_ref=responses[0]["body_ref"], directory_response_sha256=responses[0]["sha256"],
                         metadata_response_ref=response["body_ref"], metadata_response_sha256=response["sha256"])
            rebuilt.append(value)
        core = {k:v for k,v in hit.items() if k not in {"search_receipt", "search_receipt_sha256"}}
        return receipt["hits"] == rebuilt and core in rebuilt
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return False
