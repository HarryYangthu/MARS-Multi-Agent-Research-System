"""Conservative publication quotas, separate from exact document provenance.

Inputs must be retrieved metadata or sources whose titles and URLs have already
been verified against that metadata. Equal titles with different URL identities
withhold an independence credit; they do not establish document equivalence.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re
from typing import Any
import unicodedata
from urllib.parse import urlparse

from app.agents.idea.source_identity import document_key

PUBLICATION_COUNT_CONTRACT = "idea.publication_count.v1"


def canonical_source(url: str) -> str:
    """Legacy URL-level publication key; never use it to bind PDF evidence."""
    p = urlparse(url.strip())
    host = (p.hostname or "").lower()
    path = p.path.rstrip("/")
    key = document_key(url)
    if key.startswith("arxiv:"):
        return key.rsplit(":", 1)[0]
    if host == "ieeexplore.ieee.org":
        match = re.search(r"(?:document|abstract/document)/([0-9]+)", path)
        if match:
            return "ieee:" + match.group(1)
    return host + path


def publication_title_key(title: str) -> str:
    """Retain Unicode letters/numbers; never turn all non-English titles into ''."""
    return "".join(char for char in unicodedata.normalize("NFKC", title).casefold() if char.isalnum())


@dataclass(frozen=True)
class PublicationCount:
    count: int
    conflicts: tuple[dict[str, Any], ...]

    def diagnostic(self) -> str:
        if not self.conflicts:
            return ""
        detail = "; ".join(repr(row["title"]) + ": " + ", ".join(row["source_urls"]) for row in self.conflicts)
        return (" Source independence unresolved: matching normalized full titles at different source URLs "
                "may identify duplicate publications; they do not receive a second publication credit "
                "without clarification. Document versions, PDF contents and read receipts remain separate. "
                + detail[:1400] + (" [additional diagnostic text omitted]" if len(detail) > 1400 else ""))


def count_publications(sources: Iterable[dict[str, Any]]) -> PublicationCount:
    """Count title-connected URL groups without merging any source/evidence rows.

    Missing titles cannot create a collision. This helper never establishes that
    a source was searched, downloaded, read, or accepted by a scientific review.
    """
    parents: dict[str, str] = {}
    titles: dict[str, list[tuple[str, str, str]]] = {}

    def root(key: str) -> str:
        while parents[key] != key:
            key = parents[key]
        return key

    for source in sources:
        url = str(source.get("url") or "")
        if not document_key(url):
            continue
        identity = canonical_source(url)
        parents.setdefault(identity, identity)
        title = str(source.get("title") or "")
        normalized = publication_title_key(title)
        if normalized:
            titles.setdefault(normalized, []).append((identity, url, title))
    conflicts: list[dict[str, Any]] = []
    for normalized, rows in sorted(titles.items()):
        identities = sorted({row[0] for row in rows})
        if len(identities) < 2:
            continue
        for identity in identities[1:]:
            parents[root(identity)] = root(identities[0])
        conflicts.append({"reason": "duplicate_or_independence_unresolved", "title": min(row[2] for row in rows),
                          "normalized_title": normalized, "source_urls": sorted({row[1] for row in rows})})
    return PublicationCount(len({root(key) for key in parents}), tuple(conflicts))


def count_contract(manifest: dict[str, Any], output: dict[str, Any]) -> str | None:
    """Historical absence keeps the original quota; never infer a new acceptance."""
    key = "publication_count_contract"
    if key not in manifest and key not in output:
        return None
    if manifest.get(key) != PUBLICATION_COUNT_CONTRACT or output.get(key) != PUBLICATION_COUNT_CONTRACT:
        raise ValueError("publication count contract is unknown or differs between manifest and output")
    return PUBLICATION_COUNT_CONTRACT


def count_report_publications(reports: list[dict[str, Any]], *,
                              selected: set[tuple[str, str]] | None = None) -> PublicationCount:
    """Count host-loaded source titles across reports, including historical ones.

    The loader derives publication_metadata from the original search observations,
    not from an author's title alone. The fallback supports pure rendering/input
    contracts; callers must not treat it as a substitute for loading provenance.
    """
    sources: list[dict[str, Any]] = []
    for item in reports:
        for source in item["report"].get("sources", []):
            key = (str(item["delegation_id"]), str(source["source_id"]))
            if source.get("decision") != "use" or (selected is not None and key not in selected):
                continue
            if "publication_metadata" in item:
                rows = [row for row in item["publication_metadata"]
                        if row["source_id"] == source["source_id"] and row["url"] == source["url"]]
                if not rows:
                    raise ValueError("verified publication metadata is missing for a report source")
                sources.extend(rows)
            else:
                sources.append(source)
    return count_publications(sources)
