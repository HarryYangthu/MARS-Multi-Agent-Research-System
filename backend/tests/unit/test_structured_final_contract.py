"""Pure serialization and batch-accounting regressions; no external-service doubles."""
from __future__ import annotations

import json

import pytest

from app.harness.agent_loop.protocol import parse_action, parse_review
from app.harness.schema.frontmatter_parser import parse
from app.harness.tools.search.source_fetch import source_batch


def test_structured_final_roundtrips_colons_dates_and_equations() -> None:
    metadata = {
        "schema": "proposal.v1", "created": "2026-09-07",
        "hypothesis": "boundary: clamp; formula: f(x, y) = a*x + b*y",
        "method_spec": {"equation": "g: R² → C", "knots": [0, 0.5, 1]},
        "citation": "on", "enabled": False, "count": 8,
    }
    body = "## 方案\n只定义待验证的方法，不报告仿真成功。\n"
    document = parse_action(json.dumps({"final": {"metadata": metadata, "body": body}}))["final"]
    assert parse(document).metadata == metadata
    assert document.endswith(body)


@pytest.mark.parametrize("final", [
    {"metadata": {}, "body": "text"},
    {"metadata": {"schema": "proposal.v1"}, "body": ""},
    {"metadata": "not-an-object", "body": "text"},
    {"metadata": {"schema": "proposal.v1"}, "body": "text", "extra": 1},
])
def test_structured_final_never_fills_missing_contract_fields(final: object) -> None:
    with pytest.raises(ValueError):
        parse_action(json.dumps({"final": final}))


@pytest.mark.parametrize("text", [
    '{"final":"first","final":"second"}',
    '{"tool":"search.web_search","args":{"q":"a","q":"b"},"reason":"search"}',
    '{"final":{"metadata":{"count":NaN},"body":"text"}}',
    '{"final":{"metadata":{"count":1e999},"body":"text"}}',
])
def test_ambiguous_or_nonfinite_json_is_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        parse_action(text)


def test_review_duplicate_accept_key_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_review('{"accept":false,"accept":true,"issues":[],"rationale":"review"}')


def test_batch_limit_reports_not_attempted_sources() -> None:
    sources = [{"title": "source A"}, {"title": "source B"}]
    chosen, report = source_batch(sources, 1)
    assert chosen == sources[:1]
    assert report["requested_sources"] == 2
    assert report["selected_sources"] == report["skipped_sources"] == 1
    assert report["skipped"][0]["source"] == sources[1]
    assert "not attempted" in report["skipped"][0]["reason"]
    assert source_batch(sources, 2)[1]["skipped_sources"] == 0


@pytest.mark.parametrize("limit", [0, 6, True])
def test_batch_limit_is_bounded(limit: int) -> None:
    with pytest.raises(ValueError):
        source_batch([{"title": "source"}], limit)
