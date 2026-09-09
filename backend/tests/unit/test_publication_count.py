"""Pure quota contracts and fixed real archives; no simulated reads or execution."""
from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from functools import partial
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.agents.idea.publication_count import (
    PUBLICATION_COUNT_CONTRACT, canonical_source, count_contract, count_publications,
    count_report_publications, publication_title_key,
)
from app.agents.idea.research import material_errors
from app.agents.idea.research_delegate import TOOL, load_delegated_research
from app.agents.idea.research_dossier import dossier_errors
from app.agents.idea.research_gap import STOP_CONTRACT, evidence_stop, material_state
from app.agents.idea.research_links import research_link_errors
from app.agents.idea.research_stop import LEAD_STOP_CONTRACT, lead_evidence_stop
from app.agents.idea.source_identity import SourceIdentityIndex, search_metadata_rows
from app.harness.agent_loop.stop import LoopStopView, stop_fingerprint
from app.harness.agent_loop.trace import digest
from app.harness.schema.frontmatter_parser import parse


@pytest.mark.parametrize(("left", "right"), [
    ("ＡｄａＩｎｔ: Real-Time", "adaint real time"),
    ("学习自适应：间隔", "学习自适应间隔"), ("Über Größe", "über größe"),
    ("Καλημέρα: κόσμε", "καλημέρα κόσμε"),
])
def test_full_title_normalization_preserves_unicode_letters_and_numbers(left: str, right: str) -> None:
    assert publication_title_key(left) == publication_title_key(right)
    assert publication_title_key(left)


def test_unrelated_unicode_titles_and_empty_titles_are_not_collapsed() -> None:
    # These are hand-authored counting inputs, not assertions of searched/read papers.
    rows = [{"title": title, "url": f"https://example.org/{index}"}
            for index, title in enumerate(("人工资料甲", "人工资料乙", "", "!!!"))]
    assert count_publications(rows).count == 4
    assert not count_publications(rows).conflicts
    assert count_publications([]).count == 0


def test_title_collision_is_conservative_and_does_not_mutate_document_records() -> None:
    rows = [{"url": "https://example.org/a", "title": "人工资料甲"},
            {"url": "https://another.example/b", "title": "人工资料：甲"},
            {"url": "https://example.org/c", "title": "人工资料乙"}]
    original = deepcopy(rows)
    result = count_publications(rows)
    assert result.count == 2 and len(result.conflicts) == 1
    assert result.conflicts[0]["reason"] == "duplicate_or_independence_unresolved"
    assert "without clarification" in result.diagnostic()
    assert "receipts remain separate" in result.diagnostic()
    assert rows == original


def test_existing_arxiv_version_and_legacy_category_rules_remain() -> None:
    rows = [{"url": f"https://arxiv.org/abs/hep-th/9901001v{i}", "title": f"Observed title version {i}"}
            for i in (1, 2)]
    assert count_publications(rows).count == 1
    assert not count_publications(rows).conflicts
    assert canonical_source(rows[0]["url"]) != canonical_source("https://arxiv.org/abs/astro-ph/9901001v1")


def test_alternate_titles_on_one_url_cannot_create_extra_independence_credit() -> None:
    rows = [{"url": "https://example.org/a", "title": "First full title"},
            {"url": "https://example.org/a", "title": "Second full title"},
            {"url": "https://example.org/b", "title": "Second full title"},
            {"url": "https://example.org/c", "title": "First full title"}]
    assert count_publications(rows).count == 1
    assert count_publications(reversed(rows)) == count_publications(rows)


def test_count_diagnostic_fits_stop_reason_budget_without_losing_structured_collisions() -> None:
    rows = [{"title": "X" * 600, "url": "https://example.org/" + str(i) + "x" * 400} for i in range(10)]
    result = count_publications(rows)
    assert result.count == 1 and len(result.diagnostic()) < 1900
    assert len(result.conflicts[0]["source_urls"]) == 10


@pytest.mark.parametrize(("manifest", "output"), [
    ({}, {"publication_count_contract": PUBLICATION_COUNT_CONTRACT}),
    ({"publication_count_contract": PUBLICATION_COUNT_CONTRACT}, {}),
    ({"publication_count_contract": None}, {"publication_count_contract": None}),
    ({"publication_count_contract": "future"}, {"publication_count_contract": "future"}),
])
def test_count_contract_rejects_unknown_or_one_sided_claim(manifest: dict[str, Any], output: dict[str, Any]) -> None:
    # Invalid receipt declarations, never a forged successful tool execution.
    with pytest.raises(ValueError, match="publication count contract"):
        count_contract(manifest, output)


def test_historical_absence_is_explicit_and_current_contract_is_host_bound() -> None:
    manifest: dict[str, Any] = {"unrelated_historical_field": "unchanged"}
    output: dict[str, Any] = {}
    original = deepcopy((manifest, output))
    assert count_contract(manifest, output) is None
    assert (manifest, output) == original
    current = {"publication_count_contract": PUBLICATION_COUNT_CONTRACT}
    assert count_contract(current, current) == PUBLICATION_COUNT_CONTRACT
    assert dossier_errors({}, [], publication_count_contract="future") == ["/sources: unknown publication count contract"]


def test_new_quotas_invalidate_old_loop_resume_fingerprints_only() -> None:
    child = partial(evidence_stop, min_sources=2, max_tool_steps=10, tools=("search.fetch_sources",), project="pimc")
    lead = partial(lead_evidence_stop, run_root=Path("."), min_sources=2, max_delegations=3,
                   max_tool_steps=5, can_delegate=True)
    assert STOP_CONTRACT.endswith(".v3") and LEAD_STOP_CONTRACT.endswith(".v3")
    assert stop_fingerprint("base", child, STOP_CONTRACT) != stop_fingerprint("base", child, "idea.research_evidence_stop.v2")
    assert stop_fingerprint("base", lead, LEAD_STOP_CONTRACT) != stop_fingerprint("base", lead, "idea.lead_research_stop.v2")
    assert stop_fingerprint("base", None, None) == "base"


@pytest.fixture
def real_prefix() -> Iterator[list[dict[str, Any]]]:
    configured = os.environ.get("MARS_TEST_PUBLICATION_COUNT_TRACE")
    if not configured:
        pytest.skip("requires actual run21 b4e93a44 child trace; no second PDF read is fabricated")
    path = Path(configured)
    lines = path.read_bytes().splitlines(keepends=True)
    prefix = b"".join(line for line in lines if json.loads(line)["event_seq"] <= 29)
    assert hashlib.sha256(prefix).hexdigest() == "ff5e6668ffa908babade3c594f46e0258fb5ee510c3243be07c92123e302fcef"
    events = [json.loads(line) for line in prefix.splitlines()]
    observations = [row["visible"] for row in events if row["kind"] == "observation"]
    assert len(observations) == 4
    for row in events:
        if "visible" in row:
            assert digest(row["visible"]) == row["visible_sha256"]
    hashes: dict[Path, str] = {}

    def protect(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                protect(item)
        elif isinstance(value, list):
            for item in value:
                protect(item)
        elif isinstance(value, str) and value.startswith("/") and len(value) < 1000:
            target = Path(value)
            if target.is_file() and target not in hashes:
                data = target.read_bytes()
                hashes[target] = hashlib.sha256(data).hexdigest()
                if target.suffix == ".json":
                    protect(json.loads(data))
    protect(observations)
    yield observations
    assert path.read_bytes().startswith(prefix)  # Later real events may append; this fixed prefix must not change.
    assert all(hashlib.sha256(name.read_bytes()).hexdigest() == sha for name, sha in hashes.items())


def adaint_metadata(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [hit for obs in observations for hit in search_metadata_rows(obs) if "adaint" in hit["title"].lower()]
    assert len(rows) == 2 and {row["source"] for row in rows} == {"openalex", "cvf"}
    return rows


def test_real_search_metadata_channels_withhold_a_second_publication_credit(real_prefix: list[dict[str, Any]]) -> None:
    rows = adaint_metadata(real_prefix)
    assert len({canonical_source(row["url"]) for row in rows}) == 2  # Original URL-level count.
    result = count_publications(rows)
    assert result.count == 1 and len(result.conflicts) == 1
    assert len(result.conflicts[0]["source_urls"]) == 2
    only_metadata = [row for row in real_prefix if row["tool"] != "search.fetch_sources"]
    assert material_state(only_metadata)["counts"]["distinct_read_publications"] == 0


def test_actual_single_cvf_read_still_counts_once_and_cannot_back_arxiv(real_prefix: list[dict[str, Any]]) -> None:
    state = material_state(real_prefix)
    assert state["counts"]["distinct_read_publications"] == state["counts"]["read_windows"] == 1
    assert state["publication_count_contract"] == PUBLICATION_COUNT_CONTRACT
    assert not state["publication_count_conflicts"]  # Only one channel actually has a read.
    rows = adaint_metadata(real_prefix)
    cvf, arxiv = sorted(rows, key=lambda row: row["source"])
    index = SourceIdentityIndex(real_prefix)
    receipt = state["read_sources"][0]["read_receipt"]
    assert index.matching_hits(cvf["url"], read_receipt=receipt)
    assert not index.matching_hits(arxiv["url"], read_receipt=receipt)
    view = LoopStopView("before_model", "", real_prefix, {"tool_dispatches": 10}, "act")
    stop = evidence_stop(view, min_sources=2, max_tool_steps=10, tools=("search.fetch_sources",), project="pimc")
    assert stop is not None and stop.status == "evidence_unavailable"
    assert stop.details["observed_read_publications"] == 1


def test_actual_retrieved_citations_do_not_gain_two_credits_or_fake_a_second_pdf(real_prefix: list[dict[str, Any]]) -> None:
    metadata = {"related_literature": [{key: row[key] for key in ("url", "title")} for row in adaint_metadata(real_prefix)]}
    errors = material_errors(metadata, real_prefix, min_sources=2, min_pdfs=2, require_budget=False, max_ratio=1.0)
    assert any(error.startswith("/related_literature: need 2") and "observed 1" in error
               and "Source independence unresolved" in error for error in errors)
    assert any(error.startswith("/evidence: need 2") for error in errors)


@pytest.mark.parametrize("separate_reports", [False, True])
def test_same_or_different_child_projections_use_identical_count_rule(
    real_prefix: list[dict[str, Any]], separate_reports: bool,
) -> None:
    # Pure field/count input projections from real metadata, not passed dossiers:
    # no synthetic quote, receipt, checkpoint, review or tool result is constructed.
    rows = adaint_metadata(real_prefix)
    sources = [{"source_id": str(i), "title": row["title"], "url": row["url"], "decision": "use"}
               for i, row in enumerate(rows)]
    reports = ([{"delegation_id": str(i), "report": {"sources": [source]}} for i, source in enumerate(sources)]
               if separate_reports else [{"delegation_id": "0", "report": {"sources": sources}}])
    assert count_report_publications(reports).count == 1
    assert count_report_publications(reports, selected={("0", "0")}).count == 1
    assert count_report_publications(reports, selected=set()).count == 0
    errors = research_link_errors({"research_links": [{"delegation_id": "0", "insight_id": "missing",
        "method_spec_ref": "/method_spec/candidate", "adaptation_reason": "Human-authored reference contract only."}]},
        reports, min_sources=2)
    assert any("observed 1" in error and "Source independence unresolved" in error for error in errors)
    assert any("insight does not exist" in error for error in errors)  # No successful report/link is invented.


def test_report_count_uses_host_metadata_and_rejects_missing_binding(real_prefix: list[dict[str, Any]]) -> None:
    rows = adaint_metadata(real_prefix)
    sources = [{"source_id": str(i), "url": row["url"], "title": f"Untrusted title {i}", "decision": "use"}
               for i, row in enumerate(rows)]
    reports: list[dict[str, Any]] = [{"delegation_id": "input", "report": {"sources": sources}, "publication_metadata": [
        {"source_id": str(i), "url": row["url"], "title": row["title"]} for i, row in enumerate(rows)]}]
    assert count_report_publications(reports).count == 1
    reports[0]["publication_metadata"].pop()
    with pytest.raises(ValueError, match="verified publication metadata is missing"):
        count_report_publications(reports)


def test_real_historical_report_keeps_old_contract_and_new_parent_recounts() -> None:
    configured = os.environ.get("MARS_TEST_SOURCE_IDENTITY_CHECKPOINT")
    if not configured:
        pytest.skip("requires actual run13 passed child archive")
    checkpoint = Path(configured)
    root = checkpoint.parents[3]
    original = checkpoint.read_bytes()
    state = json.loads(original)
    metadata = parse(state["candidate"]).metadata
    assert state["status"] == "passed"
    assert dossier_errors(metadata, state["history"], min_sources=2) == []
    assert dossier_errors(metadata, state["history"], min_sources=2, publication_count_contract=None) == []
    receipts = [obs for path in (root / "agent_traces" / "idea").glob("*/checkpoint.json")
                for obs in json.loads(path.read_text())["history"]
                if obs.get("tool") == TOOL and obs.get("ok") and obs.get("output", {}).get("delegation_id") == checkpoint.parent.name]
    assert receipts
    manifest_path = root / receipts[0]["output"]["manifest_ref"]
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    assert count_contract(manifest, receipts[0]["output"]) is None
    report_path = root / manifest["report_ref"]
    report_bytes = report_path.read_bytes()
    reports, _ = load_delegated_research(root, receipts[:1])
    assert reports[0]["report"] == metadata and reports[0]["publication_metadata"]
    assert count_report_publications(reports).count == 2
    assert checkpoint.read_bytes() == original and manifest_path.read_bytes() == manifest_bytes
    assert report_path.read_bytes() == report_bytes
