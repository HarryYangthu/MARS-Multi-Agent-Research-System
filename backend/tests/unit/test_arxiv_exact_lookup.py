"""Identifier and schema contracts; no model or transport substitutes."""
from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator

from app.harness.tools.registry import get_registry
from app.harness.tools.search import _pdf_url_for_source
from app.harness.tools.search.arxiv_query import exact_arxiv_ids, verify_arxiv_lookup


def test_exact_lookup_keeps_versions_and_old_identifiers() -> None:
    ids = ["2404.19756v1", "hep-th/9901001v2", "2511.01433"]
    assert exact_arxiv_ids({"arxiv_ids": ids}) == ids
    assert exact_arxiv_ids({"query": "spline knots"}) is None
    assert _pdf_url_for_source("http://arxiv.org/abs/hep-th/9901001v2") == "https://arxiv.org/pdf/hep-th/9901001v2.pdf"


@pytest.mark.parametrize("args", [
    {"arxiv_ids": []}, {"arxiv_ids": "2404.19756"},
    {"arxiv_ids": ["2404.19756v0"]}, {"arxiv_ids": ["https://arxiv.org/abs/2404.19756"]},
    {"arxiv_ids": ["2404.19756", "2404.19756"]},
    {"arxiv_ids": ["2404.19756"], "query": "KAN"},
    {"arxiv_ids": ["2404.19756"], "date_from": "2026-01-01"},
    {"arxiv_ids": ["2404.19756"], "top_k": 1},
])
def test_exact_lookup_rejects_ambiguous_or_invalid_requests(args: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        exact_arxiv_ids(args)


def test_exact_lookup_metadata_identity_is_not_publication_deduplication() -> None:
    # Explicit pure parser inputs, not claimed retrieved sources.
    hit = {"url": "http://arxiv.org/abs/2404.19756v1", "title": "Parser title"}
    assert verify_arxiv_lookup(["2404.19756v1"], [hit]) == []
    assert verify_arxiv_lookup(["2404.19756"], [hit]) == []
    assert verify_arxiv_lookup(["2404.19756v1", "2511.01433v1"], [hit]) == ["2511.01433v1"]
    for ids in (["2404.19756v2"], ["2511.01433v1"]):
        with pytest.raises(ValueError, match="different paper or explicit version"):
            verify_arxiv_lookup(ids, [hit])
    with pytest.raises(ValueError, match="invalid publication metadata"):
        verify_arxiv_lookup(["2404.19756v1"], [{**hit, "url": "https://untrusted.example/abs/2404.19756v1"}])


def test_registered_arxiv_schema_exposes_exclusive_lookup_mode() -> None:
    spec = get_registry().spec("search.arxiv_search")
    assert spec is not None
    validator = Draft202012Validator(spec.input_schema)
    for args in ({"arxiv_ids": ["2404.19756v1"]}, {"query": "spline"}, {"q": "KAN", "top_k": 2}):
        assert not list(validator.iter_errors(args))
    for invalid_args in ({"arxiv_ids": ["2404.19756v1"], "query": "KAN"},
                 {"arxiv_ids": ["2404.19756v1"], "query": "KAN", "sort_by": "relevance"},
                 {"arxiv_ids": ["2404.19756v1"], "q": "KAN", "top_k": 2},
                 {"arxiv_ids": ["2404.19756v1"], "sort_by": "relevance"}):
        assert list(validator.iter_errors(invalid_args))
