"""Exact public identifier lookup; keyword discovery remains a separate mode."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

ARXIV_ID = r"(?:[0-9]{4}\.[0-9]{4,5}|[a-zA-Z.-]+/[0-9]{7})(?:v[1-9][0-9]*)?"


def exact_arxiv_ids(args: dict[str, Any]) -> list[str] | None:
    if "arxiv_ids" not in args:
        return None
    if any(key in args for key in ("query", "q", "date_from", "categories", "sort_by", "top_k")):
        raise ValueError("arxiv_ids is an exact lookup; do not combine it with query, filters, sorting or top_k")
    ids = args["arxiv_ids"]
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 20
            or any(not isinstance(item, str) or not re.fullmatch(ARXIV_ID, item) for item in ids)):
        raise ValueError("arxiv_ids must contain 1..20 arXiv identifiers, optionally with an explicit vN version")
    if len(set(ids)) != len(ids):
        raise ValueError("arxiv_ids must be unique")
    return list(ids)


def verify_arxiv_lookup(ids: list[str], hits: list[dict[str, Any]]) -> list[str]:
    """Reject unrelated/version-switched metadata; return genuinely missing IDs."""
    found: set[str] = set()
    for hit in hits:
        parsed = urlparse(str(hit.get("url", "")))
        actual = parsed.path.removeprefix("/abs/")
        if (parsed.hostname not in {"arxiv.org", "export.arxiv.org"}
                or not parsed.path.startswith("/abs/") or not re.fullmatch(ARXIV_ID, actual)
                or not str(hit.get("title", "")).strip()):
            raise ValueError("arXiv exact lookup returned invalid publication metadata")
        matches = [requested for requested in ids if actual == requested or (
            not re.search(r"v[1-9][0-9]*$", requested)
            and re.sub(r"v[1-9][0-9]*$", "", actual) == requested)]
        if not matches:
            raise ValueError("arXiv exact lookup returned a different paper or explicit version")
        found.update(matches)
    return [requested for requested in ids if requested not in found]
