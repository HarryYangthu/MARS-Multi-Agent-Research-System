"""Exact document identities from observed metadata and verified reading receipts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.harness.tools.search.source_fetch import SourceFetchError, resource_aliases, resource_key


def search_metadata_rows(observation: dict[str, Any]) -> list[dict[str, Any]]:
    """CVF identities require archived original metadata, never output assertions."""
    output = observation.get("output")
    if not observation.get("ok") or not isinstance(output, dict):
        return []
    tool = observation.get("tool")
    if tool not in {"search.arxiv_search", "search.web_search", "search.openalex_search", "search.cvf_search"}:
        return []
    hits = output.get("hits", [])
    if not isinstance(hits, list):
        return []
    rows = [hit for hit in hits if isinstance(hit, dict) and isinstance(hit.get("url"), str)
            and isinstance(hit.get("title"), str)]
    if tool != "search.cvf_search":
        return rows
    from app.harness.tools.search.cvf import verified_cvf_hit
    try:
        raw_ref = observation["raw_ref"]
        raw = json.loads(Path(raw_ref).read_text())
        if ({**raw, "raw_ref": raw_ref} != observation or output.get("source") != "cvf"
                or not isinstance(output.get("search_receipt"), str)):
            return []
        args = observation["args"]
        receipt_data = Path(output["search_receipt"]).read_bytes()
        receipt = json.loads(receipt_data)
        expected = {"query": args["query"].strip(), "venue": args["venue"],
                    "year": args["year"], "top_k": args.get("top_k", 3)}
        if (hashlib.sha256(receipt_data).hexdigest() != output.get("search_receipt_sha256")
                or any(receipt["request"].get(key) != value for key, value in expected.items())
                or any(output.get(key) != args.get(key) for key in ("query", "venue", "year"))):
            return []
        return [hit for hit in rows if hit.get("search_receipt") == output["search_receipt"]
                and hit.get("search_receipt_sha256") == output.get("search_receipt_sha256") and verified_cvf_hit(hit)]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return []


def document_key(url: str) -> str:
    """Preserve explicit versions, legacy arXiv categories and generic URL queries."""
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        return resource_key(url.strip())
    except ValueError:
        return ""


def metadata_document_keys(hit: dict[str, Any]) -> frozenset[str]:
    """Only the URL/PDF relationship actually present in this metadata row."""
    url = str(hit.get("url") or "")
    pdf = str(hit.get("pdf_url") or url)
    keys = {document_key(url), document_key(pdf)}
    if "" in keys:
        return frozenset()
    arxiv = [key.rsplit(":", 1) for key in keys if key.startswith("arxiv:")]
    if len({paper for paper, _ in arxiv}) > 1 or len({version for _, version in arxiv if version != "latest"}) > 1:
        return frozenset()  # Contradictory explicit papers/versions are not aliases.
    return frozenset(keys)


def verified_read_aliases(row: dict[str, Any]) -> frozenset[str]:
    """Never trust a supplied alias list or infer a version from a publication ID."""
    try:
        receipt = json.loads(Path(row["read_receipt"]).read_text())
        fields = ("sha256", "download_path", "visible_pages", "url", "download_url",
                  "final_url", "resource_key", "resource_aliases")
        if (not isinstance(receipt, dict) or not receipt.get("ok") or not row.get("ok")
                or receipt.get("source_type") != "pdf" or row.get("source_type") != "pdf"
                or any(receipt.get(field) != row.get(field) for field in fields)):
            return frozenset()
        data = Path(receipt["download_path"]).read_bytes()
        if not data.startswith(b"%PDF-") or hashlib.sha256(data).hexdigest() != receipt["sha256"]:
            return frozenset()
        requested = str(receipt["download_url"])
        final = str(receipt.get("final_url") or requested)
        if not document_key(requested) or not document_key(final):
            return frozenset()
        # Historical receipts without a final URL prove only the requested URL.
        return frozenset(resource_aliases(requested, final))
    except (OSError, ValueError, TypeError, KeyError, SourceFetchError):
        return frozenset()


def source_reference_keys(hit: dict[str, Any], keys: frozenset[str]) -> frozenset[str]:
    """A PDF/CDN redirect cannot relabel a publication as another source."""
    landing = document_key(str(hit.get("url") or ""))
    if not landing or landing not in keys:
        return frozenset()
    if landing.startswith("arxiv:"):
        publication = landing.rsplit(":", 1)[0]
        return frozenset(key for key in keys if key.startswith("arxiv:") and key.rsplit(":", 1)[0] == publication)
    return frozenset({landing})


class SourceIdentityIndex:
    """Match a declaration to one metadata row and, optionally, one exact read."""

    def __init__(self, observations: list[dict[str, Any]]) -> None:
        self.hits: list[dict[str, Any]] = []
        self.reads: dict[str, dict[str, Any]] = {}
        self._aliases: dict[str, frozenset[str]] = {}
        for observation in observations:
            output = observation.get("output")
            if not observation.get("ok") or not isinstance(output, dict):
                continue
            self.hits.extend(search_metadata_rows(observation))
            if observation.get("tool") == "search.fetch_sources":
                for row in output.get("sources", []):
                    if isinstance(row, dict) and row.get("ok") and isinstance(row.get("read_receipt"), str):
                        self.reads[row["read_receipt"]] = row

    def _read_keys(self, hit: dict[str, Any], receipt: str) -> frozenset[str]:
        row = self.reads.get(receipt)
        if row is None:
            return frozenset()
        if receipt not in self._aliases:
            self._aliases[receipt] = verified_read_aliases(row)
        aliases = self._aliases[receipt]
        keys = metadata_document_keys(hit)
        expected = document_key(str(hit.get("pdf_url") or hit.get("url") or ""))
        if not keys or expected not in aliases:
            return frozenset()
        explicit = {key for key in keys if key.startswith("arxiv:") and not key.endswith(":latest")}
        if not explicit <= aliases:
            return frozenset()  # An unversioned download alone cannot verify a versioned landing page.
        joined = keys | aliases
        arxiv = [key.rsplit(":", 1) for key in joined if key.startswith("arxiv:")]
        if len({paper for paper, _ in arxiv}) > 1 or len({version for _, version in arxiv if version != "latest"}) > 1:
            return frozenset()
        source_keys = source_reference_keys(hit, joined)
        if document_key(str(row.get("url") or "")) not in source_keys:
            return frozenset()
        return source_keys

    def matching_hits(self, url: str, *, read_receipt: str | None = None) -> list[dict[str, Any]]:
        key = document_key(url)
        if not key:
            return []
        matched = []
        for hit in self.hits:
            if read_receipt is not None:
                valid = key in self._read_keys(hit, read_receipt)
            else:
                valid = key in source_reference_keys(hit, metadata_document_keys(hit)) or any(
                    key in self._read_keys(hit, receipt) for receipt in self.reads)
            if valid:
                matched.append(hit)
        return matched
