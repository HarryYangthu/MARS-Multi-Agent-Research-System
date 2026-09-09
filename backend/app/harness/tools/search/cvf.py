"""Bounded CVF metadata from /content/{VENUE}{YEAR}/; PDF reading is separate.

Legacy /content_... layouts and HTTP redirects are explicitly unsupported. The
year argument identifies a directory, not a promise of coverage for every year.
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

CVF_HOST = "openaccess.thecvf.com"
RECEIPT_SCHEMA = "cvf.search_receipt.v1"
SELECTION_POLICY = "title_tokens_all.v1: all casefolded Unicode query words must occur in the directory title; directory order breaks ties; no semantic score"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _title(value: str) -> str:
    return " ".join(value.split())


def _tokens(value: str) -> list[str]:
    return sorted(set(re.findall(r"[^\W_]+", value.casefold())))


def _parameters(args: dict[str, Any]) -> tuple[str, str, int, int]:
    if set(args) - {"query", "venue", "year", "top_k"}:
        raise ValueError("unexpected CVF search arguments")
    query, venue, year, top_k = args.get("query"), args.get("venue"), args.get("year"), args.get("top_k", 3)
    if not isinstance(query, str) or not 1 <= len(query) <= 200 or not _tokens(query):
        raise ValueError("query must contain 1..200 characters of title words")
    if not isinstance(venue, str) or venue not in {"CVPR", "ICCV", "WACV"}:
        raise ValueError("venue must be CVPR, ICCV or WACV")
    if type(year) is not int or not 2000 <= year <= 2100:
        raise ValueError("year must be an explicit integer between 2000 and 2100")
    if type(top_k) is not int or not 1 <= top_k <= 3:
        raise ValueError("top_k must be an integer between 1 and 3")
    return query.strip(), venue, year, top_k


def _official_url(url: str) -> str:
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname != CVF_HOST or parsed.username or parsed.password
            or parsed.port not in {None, 443} or parsed.fragment or parsed.params
            or any(part in {".", ".."} for part in unquote(parsed.path).split("/"))
            or "\\" in unquote(url) or any(c.isspace() or ord(c) < 32 for c in unquote(url))):
        raise ValueError("CVF metadata requires an official HTTPS URL without credentials or path traversal")
    return url


def _paper_url(url: str, venue: str, year: int, *, pdf: bool) -> str:
    _official_url(url)
    parsed = urlparse(url)
    if parsed.path.startswith("/content_"):
        raise ValueError("unsupported legacy CVF URL layout; this tool requires /content/{VENUE}{YEAR}/{html,papers}/ and never guesses replacement URLs")
    folder, suffix = ("papers", ".pdf") if pdf else ("html", ".html")
    if parsed.query or not parsed.path.startswith(f"/content/{venue}{year}/{folder}/") or not parsed.path.endswith(suffix):
        raise ValueError("CVF citation URL does not belong to the requested conference/year and document type")
    return url


def _complete_html(text: str) -> None:
    if not text.strip() or not re.search(r"<html(?:\s|>)", text, re.I) or not re.search(r"</html\s*>\s*$", text, re.I):
        raise ValueError("incomplete or empty CVF HTML document")


def _attributes(attrs: list[tuple[str, str | None]]) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    for key, value in attrs:
        if key in values:
            raise ValueError("duplicate HTML attributes in CVF metadata")
        values[key] = value
    return values


class _DirectoryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.entries: list[dict[str, str]] = []
        self.in_title = False
        self.href: str | None = None
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = _attributes(attrs) if tag in {"dt", "a"} else dict(attrs)
        if tag == "dt" and "ptitle" in (values.get("class") or "").split():
            if self.in_title:
                raise ValueError("nested or incomplete CVF directory title")
            self.in_title = True
        if self.in_title and tag == "a":
            if self.href is not None or not values.get("href"):
                raise ValueError("ambiguous CVF directory title link")
            self.href = values["href"]
            self.parts = []

    def handle_data(self, data: str) -> None:
        if self.in_title and self.href is not None:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "dt" and self.in_title:
            if self.href is None or not _title("".join(self.parts)):
                raise ValueError("CVF directory entry lacks title or landing link")
            self.entries.append({"title": _title("".join(self.parts)), "href": self.href})
            self.in_title, self.href, self.parts = False, None, []


def parse_cvf_directory(text: str, *, venue: str, year: int) -> list[dict[str, str]]:
    """Parse actual title anchors, preserving directory order and exact URLs."""
    _complete_html(text)
    parser = _DirectoryParser()
    parser.feed(text)
    parser.close()
    if parser.in_title or not parser.entries:
        raise ValueError("CVF directory has no complete paper entries")
    entries: list[dict[str, str]] = []
    seen: dict[str, str] = {}
    for entry in parser.entries:
        url = _paper_url(urljoin(f"https://{CVF_HOST}/", entry["href"]), venue, year, pdf=False)
        if url in seen:
            if seen[url] != entry["title"]:
                raise ValueError("conflicting titles for one CVF directory URL")
            continue
        seen[url] = entry["title"]
        entries.append({**entry, "url": url})
    return entries


class _CitationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metadata: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = _attributes(attrs) if tag == "meta" else dict(attrs)
        name = (values.get("name") or "").lower()
        if tag == "meta" and name.startswith("citation_"):
            value = _title(values.get("content") or "")
            if not value:
                raise ValueError("empty CVF citation metadata")
            self.metadata.setdefault(name, []).append(value)


def parse_cvf_landing(text: str, *, entry: dict[str, str], venue: str, year: int) -> dict[str, Any]:
    """The official citation tags supply the PDF relationship; never edit a slug."""
    _complete_html(text)
    _paper_url(entry["url"], venue, year, pdf=False)
    parser = _CitationParser()
    parser.feed(text)
    parser.close()

    def unique(name: str, *, optional: bool = False) -> str:
        values = set(parser.metadata.get(name, []))
        if not values and optional:
            return ""
        if len(values) != 1:
            raise ValueError(f"CVF {name} must be present and unambiguous")
        return values.pop()

    title = unique("citation_title")
    if _title(title).casefold() != _title(entry["title"]).casefold():
        raise ValueError("CVF directory title and citation_title disagree")
    pdf = _paper_url(unique("citation_pdf_url"), venue, year, pdf=True)
    published = unique("citation_publication_date")
    if not re.fullmatch(str(year) + r"(?:[/\-]\d{1,2}(?:[/\-]\d{1,2})?)?", published):
        raise ValueError("CVF citation year differs from the requested directory")
    date_parts = [int(part) for part in re.split(r"[/\-]", published)]
    date_parts = (date_parts + [1, 1])[:3]
    datetime(date_parts[0], date_parts[1], date_parts[2])
    authors = parser.metadata.get("citation_author", [])
    if not authors or len(set(authors)) != len(authors):
        raise ValueError("CVF citation authors are missing or duplicated")
    return {"title": title, "url": entry["url"], "pdf_url": pdf, "authors": authors,
            "published": published, "doi": unique("citation_doi", optional=True), "source": "cvf"}


def select_cvf_entries(entries: list[dict[str, str]], query: str, top_k: int) -> list[dict[str, str]]:
    terms = set(_tokens(query))
    if not terms:
        raise ValueError("query has no title words")
    return [entry for entry in entries if terms <= set(_tokens(entry["title"]))][:top_k]


def validate_html_transfer(*, status_code: int, content_type: str, content_encoding: str,
                           content_length: str | None, content_range: str | None,
                           received_bytes: int, byte_limit: int) -> None:
    """Pure transport completion check, including unsolicited partial responses."""
    if status_code != 200 or content_range is not None:
        raise ValueError("CVF metadata requires a complete HTTP 200 response; redirects and partial responses are not followed")
    if content_type.split(";", 1)[0].strip().lower() != "text/html" or content_encoding.lower() not in {"", "identity"}:
        raise ValueError("CVF metadata requires identity-encoded HTML")
    if not 0 < received_bytes <= byte_limit:
        raise ValueError("CVF HTML empty or exceeds its byte limit")
    if content_length is not None and (not content_length.isdigit() or int(content_length) != received_bytes):
        raise ValueError("CVF HTML Content-Length mismatch")


async def _read_html(client: httpx.AsyncClient, url: str, path: Path, *, limit: int,
                     responses: list[dict[str, Any]]) -> str:
    _official_url(url)
    record: dict[str, Any] = {"url": url, "final_url": url, "body_ref": str(path), "byte_limit": limit, "complete": False}
    responses.append(record)
    data = bytearray()
    try:
        async with client.stream("GET", url, headers={"User-Agent": "MARS-Research/1.0", "Accept-Encoding": "identity"}) as response:
            record.update(status_code=response.status_code, content_type=response.headers.get("content-type", ""),
                          content_encoding=response.headers.get("content-encoding", ""),
                          content_length=response.headers.get("content-length"), content_range=response.headers.get("content-range"),
                          final_url=str(response.url))
            if response.status_code != 200:
                raise ValueError("CVF metadata requires HTTP 200; redirects are unsupported and never followed")
            if str(response.url) != url:
                raise ValueError("CVF final URL differs from the requested official URL")
            declared = record["content_length"]
            if declared is not None and (not declared.isdigit() or int(declared) > limit):
                raise ValueError("CVF declared HTML size invalid or over limit")
            async for block in response.aiter_raw():
                if len(data) + len(block) > limit:
                    raise ValueError("CVF HTML exceeds byte limit")
                data.extend(block)
            validate_html_transfer(status_code=record["status_code"], content_type=record["content_type"],
                content_encoding=record["content_encoding"], content_length=record["content_length"],
                content_range=record["content_range"], received_bytes=len(data), byte_limit=limit)
        text = bytes(data).decode("utf-8", errors="strict")
        _complete_html(text)
        record["complete"] = True
        return text
    finally:
        path.write_bytes(data)
        record.update(bytes=len(data), sha256=_sha(bytes(data)))


async def cvf_search_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    settings = get_settings()
    receipt: dict[str, Any] = {"schema": RECEIPT_SCHEMA, "ok": False, "hits": [], "responses": []}
    target: Path | None = None
    started = time.monotonic()
    try:
        query, venue, year, top_k = _parameters(args)
        if not settings.mars_enable_network_tools:
            raise ValueError("network tools are disabled")
        allowed = {d.strip().lower() for d in settings.mars_web_search_allowlist.split(",") if d.strip()}
        if not any(CVF_HOST == d or CVF_HOST.endswith("." + d) for d in allowed):
            raise ValueError("openaccess.thecvf.com is not allowlisted; CVF search requires no search API key")
        if not ctx.run_id or not ctx.extra.get("run_root") or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", ctx.agent):
            raise ValueError("auditable run_root and valid agent identifier required")
        run_root = Path(str(ctx.extra["run_root"])).resolve()
        target = run_root / ctx.agent / "research" / "searches" / "cvf" / uuid4().hex
        if not target.resolve().is_relative_to(run_root):
            raise ValueError("CVF archive escapes run root")
        target.mkdir(parents=True, exist_ok=False)
        archive_root = target
        directory = f"https://{CVF_HOST}/{venue}{year}?day=all"
        limits = {"total_timeout_seconds": settings.mars_cvf_total_timeout_seconds,
                  "directory_bytes": settings.mars_cvf_directory_max_mib * 1024 * 1024,
                  "landing_bytes": settings.mars_cvf_landing_max_kib * 1024}
        receipt.update(request={"query": query, "venue": venue, "year": year, "top_k": top_k, "directory_url": directory},
                       requested_at=datetime.now(timezone.utc).isoformat(), run_id=ctx.run_id, limits=limits,
                       selection_policy=SELECTION_POLICY, query_tokens=_tokens(query))

        async def query_directory() -> list[dict[str, Any]]:
            timeout = httpx.Timeout(connect=min(settings.mars_source_connect_timeout_seconds, limits["total_timeout_seconds"]),
                                    read=min(settings.mars_source_read_timeout_seconds, limits["total_timeout_seconds"]),
                                    write=limits["total_timeout_seconds"], pool=limits["total_timeout_seconds"])
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                text = await _read_html(client, directory, archive_root / "directory.html", limit=int(limits["directory_bytes"]), responses=receipt["responses"])
                entries = parse_cvf_directory(text, venue=venue, year=year)
                chosen = select_cvf_entries(entries, query, top_k)
                receipt.update(directory_entries=len(entries), matching_entries=sum(set(_tokens(query)) <= set(_tokens(e["title"])) for e in entries),
                               selected_entries=chosen)
                hits = []
                for index, entry in enumerate(chosen):
                    html = await _read_html(client, entry["url"], archive_root / f"landing-{index}.html",
                                            limit=int(limits["landing_bytes"]), responses=receipt["responses"])
                    hit = parse_cvf_landing(html, entry=entry, venue=venue, year=year)
                    hit.update(query=query, venue=venue, year=year, directory_entry=entry,
                               directory_response_ref=receipt["responses"][0]["body_ref"],
                               directory_response_sha256=receipt["responses"][0]["sha256"],
                               metadata_response_ref=receipt["responses"][-1]["body_ref"],
                               metadata_response_sha256=receipt["responses"][-1]["sha256"])
                    hits.append(hit)
                return hits

        hits = await asyncio.wait_for(query_directory(), timeout=limits["total_timeout_seconds"])
        if time.monotonic() - started > limits["total_timeout_seconds"]:
            raise ValueError("CVF total query budget exhausted")
        receipt.update(ok=True, hits=hits, empty_reason="no directory title matches all query words" if not hits else "")
    except asyncio.CancelledError:
        receipt.update(error="CVF query cancelled; no metadata accepted", hits=[])
        raise
    except (ValueError, TypeError, KeyError, OSError, httpx.HTTPError, TimeoutError) as exc:
        receipt.update(error=f"{type(exc).__name__}: {exc}", hits=[])
    finally:
        receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
        if target is not None:
            atomic_json(target / "receipt.json", receipt)
    reference = str(target / "receipt.json") if target is not None else None
    checksum = _sha(Path(reference).read_bytes()) if reference else None
    output = {"source": "cvf", "query": args.get("query"), "venue": args.get("venue"), "year": args.get("year"),
              "hits": [{**hit, "search_receipt": reference, "search_receipt_sha256": checksum} for hit in receipt["hits"]],
              "search_receipt": reference, "search_receipt_sha256": checksum, "selection_policy": SELECTION_POLICY,
              "directory_entries": receipt.get("directory_entries", 0), "matching_entries": receipt.get("matching_entries", 0),
              "metadata_only": True, "pdf_downloaded": False, "empty_reason": receipt.get("empty_reason", ""),
              "elapsed_seconds": receipt["elapsed_seconds"]}
    return ToolResult(ok=receipt["ok"], output=output, error=receipt.get("error"), evidence_refs=[reference] if reference else [])


def verified_cvf_hit(hit: dict[str, Any]) -> bool:
    """Reconstruct actual directory/citation metadata; malformed input returns False."""
    try:
        path = Path(hit["search_receipt"])
        if path.is_symlink() or path.name != "receipt.json" or not path.is_file():
            return False
        settings = get_settings()
        directory_limit = settings.mars_cvf_directory_max_mib * 1024 * 1024
        landing_limit = settings.mars_cvf_landing_max_kib * 1024
        if path.stat().st_size > directory_limit + 3 * landing_limit:
            return False
        data = path.read_bytes()
        if _sha(data) != hit["search_receipt_sha256"]:
            return False
        receipt = json.loads(data)
        if receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("ok") is not True or receipt.get("selection_policy") != SELECTION_POLICY:
            return False
        request = receipt["request"]
        query, venue, year, top_k = _parameters({k:request[k] for k in ("query", "venue", "year", "top_k")})
        directory = f"https://{CVF_HOST}/{venue}{year}?day=all"
        if request["directory_url"] != directory or receipt["query_tokens"] != _tokens(query):
            return False
        responses = receipt["responses"]
        if not isinstance(responses, list) or not 1 <= len(responses) <= top_k + 1:
            return False
        limits = receipt["limits"]
        if (type(limits["directory_bytes"]) is not int or not 0 < limits["directory_bytes"] <= directory_limit
                or type(limits["landing_bytes"]) is not int or not 0 < limits["landing_bytes"] <= landing_limit
                or type(limits["total_timeout_seconds"]) not in {int, float}
                or not 0 < limits["total_timeout_seconds"] <= 180
                or type(receipt["elapsed_seconds"]) not in {int, float}
                or not 0 <= receipt["elapsed_seconds"] <= limits["total_timeout_seconds"]):
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
        if receipt["responses"][0]["url"] != directory:
            return False
        entries = parse_cvf_directory(bodies[0], venue=venue, year=year)
        selected = select_cvf_entries(entries, query, top_k)
        if (receipt["selected_entries"] != selected or len(bodies) != len(selected) + 1 or len(receipt["hits"]) != len(selected)
                or receipt["directory_entries"] != len(entries)
                or receipt["matching_entries"] != sum(set(_tokens(query)) <= set(_tokens(e["title"])) for e in entries)):
            return False
        rebuilt = []
        for index, entry in enumerate(selected):
            response = receipt["responses"][index + 1]
            if response["url"] != entry["url"]:
                return False
            value = parse_cvf_landing(bodies[index + 1], entry=entry, venue=venue, year=year)
            value.update(query=query, venue=venue, year=year, directory_entry=entry,
                         directory_response_ref=receipt["responses"][0]["body_ref"], directory_response_sha256=receipt["responses"][0]["sha256"],
                         metadata_response_ref=response["body_ref"], metadata_response_sha256=response["sha256"])
            rebuilt.append(value)
        core = {k:v for k,v in hit.items() if k not in {"search_receipt", "search_receipt_sha256"}}
        return receipt["hits"] == rebuilt and core in rebuilt
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return False
