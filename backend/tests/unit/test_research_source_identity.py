"""URL contracts and negative mutations of real run 13 files; no provider/tool doubles."""
from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

import pytest

from app.agents.idea.research import canonical_source, material_errors
from app.agents.idea.research_dossier import dossier_errors
from app.agents.idea.research_gap import STOP_CONTRACT, material_state
from app.agents.idea.research_links import research_link_errors
from app.agents.idea.research_stop import LEAD_STOP_CONTRACT
from app.agents.idea.source_identity import SourceIdentityIndex, document_key, metadata_document_keys, source_reference_keys, verified_read_aliases
from app.harness.schema.frontmatter_parser import parse


def test_publications_deduplicate_versions_without_collapsing_legacy_categories() -> None:
    assert canonical_source("https://arxiv.org/abs/hep-th/9901001v1") == "arxiv:hep-th/9901001"
    assert canonical_source("http://export.arxiv.org/pdf/hep-th/9901001v2.pdf") == "arxiv:hep-th/9901001"
    assert canonical_source("https://arxiv.org/abs/astro-ph/9901001v1") == "arxiv:astro-ph/9901001"
    assert canonical_source("https://www.arxiv.org/abs/2404.19756v1") == canonical_source("https://arxiv.org/pdf/2404.19756v5.pdf")


def test_exact_document_keys_keep_versions_and_unversioned_distinct() -> None:
    assert document_key("https://arxiv.org/abs/hep-th/9901001v1") == document_key("http://export.arxiv.org/pdf/hep-th/9901001v1.pdf")
    assert len({document_key("https://arxiv.org/abs/hep-th/9901001" + suffix)
                for suffix in ("", "v1", "v2")}) == 3
    assert document_key("https://example.org/paper?version=1") != document_key("https://example.org/paper?version=2")
    assert not document_key("") and not document_key("file:///untrusted.pdf")


def test_metadata_aliases_are_explicit_url_relationships_only() -> None:
    # Pure URL-pair inputs; these are not represented as successful search observations.
    versioned = "https://arxiv.org/abs/2404.19756v1"
    pdf = "https://arxiv.org/pdf/2404.19756v1.pdf"
    assert metadata_document_keys({"url": versioned, "pdf_url": pdf}) == {document_key(versioned)}
    assert document_key(versioned.removesuffix("v1")) not in metadata_document_keys({"url": versioned})
    assert not metadata_document_keys({"url": versioned, "pdf_url": pdf.replace("v1", "v2")})
    assert not metadata_document_keys({"url": "https://arxiv.org/abs/hep-th/9901001v1",
                                       "pdf_url": "https://arxiv.org/pdf/astro-ph/9901001v1.pdf"})
    assert not SourceIdentityIndex([]).matching_hits(versioned)


def test_pdf_redirect_cannot_turn_one_publication_into_two_source_declarations() -> None:
    # Pure URL relations, not fabricated retrieval records.
    landing = "https://arxiv.org/abs/2404.19756v1"
    cdn = "https://example.org/archive/paper.pdf"
    keys = frozenset({document_key(landing), document_key(cdn)})
    assert source_reference_keys({"url": landing}, keys) == {document_key(landing)}
    doi = "https://doi.org/10.0000/input"
    assert not source_reference_keys({"url": doi}, keys)
    assert source_reference_keys({"url": doi}, keys | {document_key(doi)}) == {document_key(doi)}


def test_new_identity_validation_cannot_resume_under_old_stop_fingerprints() -> None:
    assert STOP_CONTRACT == "idea.research_evidence_stop.v2"
    assert LEAD_STOP_CONTRACT == "idea.lead_research_stop.v2"


@pytest.fixture
def archive() -> Iterator[tuple[dict[str, Any], list[dict[str, Any]], str]]:
    value = os.environ.get("MARS_TEST_SOURCE_IDENTITY_CHECKPOINT")
    if not value:
        pytest.skip("requires actual run 13 child c873fafdd2d8418d8733d70997d8552b checkpoint")
    checkpoint = Path(value)
    state = json.loads(checkpoint.read_text())
    assert state["status"] == "passed"
    metadata = parse(state["candidate"]).metadata
    paths = {checkpoint, checkpoint.parent / "events.jsonl"}
    for insight in metadata["insights"]:
        receipt = Path(insight["read_receipt"])
        paths.add(receipt)
        paths.add(Path(json.loads(receipt.read_text())["download_path"]))
    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    yield metadata, state["history"], checkpoint.parent.name
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in hashes.items())


def changed_version(url: str) -> str:
    match = re.search(r"v([1-9][0-9]*)$", url)
    assert match is not None
    return url[:match.start()] + "v" + str(int(match[1]) + 1)


def test_actual_run13_passes_and_declared_wrong_version_fails(
    archive: tuple[dict[str, Any], list[dict[str, Any]], str],
) -> None:
    metadata, history, _ = archive
    assert dossier_errors(metadata, history, min_sources=2) == []
    assert material_state(history)["counts"]["distinct_read_publications"] == 2
    altered = deepcopy(metadata)
    altered["sources"][0]["url"] = changed_version(altered["sources"][0]["url"])
    errors = dossier_errors(altered, history, min_sources=2)
    assert any(error.startswith("/sources/0:") and "document version" in error for error in errors)
    assert any(error.startswith("/insights/0:") and "document version" in error for error in errors)


def test_unobserved_latest_or_version_cannot_replace_actual_versioned_search(
    archive: tuple[dict[str, Any], list[dict[str, Any]], str],
) -> None:
    metadata, history, _ = archive
    source = metadata["sources"][0]
    index = SourceIdentityIndex(history)
    assert index.matching_hits(source["url"])
    assert not index.matching_hits(changed_version(source["url"]))
    assert not index.matching_hits(re.sub(r"v[0-9]+$", "", source["url"]))
    insight = next(row for row in metadata["insights"] if row["source_id"] == source["source_id"])
    assert index.matching_hits(source["url"], read_receipt=insight["read_receipt"])
    assert not index.matching_hits(changed_version(source["url"]), read_receipt=insight["read_receipt"])


def test_related_literature_rejects_wrong_version_with_the_original_pdf(
    archive: tuple[dict[str, Any], list[dict[str, Any]], str],
) -> None:
    metadata, history, _ = archive
    material = {**metadata, "related_literature": [{key: source[key] for key in ("title", "url")}
                                                    for source in metadata["sources"]]}
    kwargs: dict[str, Any] = {"min_sources": 2, "min_pdfs": 2, "require_budget": False, "max_ratio": 1.0}
    baseline = material_errors(material, history, **kwargs)
    assert not any(error.startswith(("/related_literature", "/evidence")) for error in baseline)
    material["related_literature"][0]["url"] = changed_version(material["related_literature"][0]["url"])
    errors = material_errors(material, history, **kwargs)
    assert any(error.startswith("/related_literature/0:") and "document version" in error for error in errors)
    assert any(error.startswith("/evidence:") for error in errors)
    # The other real, read PDF cannot satisfy the count for an uncited document.
    material["related_literature"] = [{key: metadata["sources"][0][key] for key in ("title", "url")}]
    errors = material_errors(material, history, min_sources=1, min_pdfs=2, require_budget=False, max_ratio=1.0)
    assert not any(error.startswith("/related_literature/") for error in errors)
    assert any(error.startswith("/evidence:") for error in errors)


def test_persisted_receipt_fields_prevent_invented_version_aliases(
    archive: tuple[dict[str, Any], list[dict[str, Any]], str],
) -> None:
    metadata, history, _ = archive
    receipt_path = metadata["insights"][0]["read_receipt"]
    row = SourceIdentityIndex(history).reads[receipt_path]
    assert verified_read_aliases(row)
    # Negative tampering inputs only: no changed row is dispatched or archived.
    for field, value in (("final_url", changed_version(row["url"])),
                         ("resource_aliases", [document_key(changed_version(row["url"]))]),
                         ("sha256", "0" * 64)):
        assert not verified_read_aliases({**row, field: value})


def test_links_require_the_same_version_as_the_verified_report(
    archive: tuple[dict[str, Any], list[dict[str, Any]], str],
) -> None:
    report, _, delegation = archive
    insight = report["insights"][0]
    source = next(row for row in report["sources"] if row["source_id"] == insight["source_id"])
    # A human-authored link contract around a real report, not a generated proposal.
    metadata: dict[str, Any] = {"method_spec": {"contract_input": "Authored pointer target only."},
                "related_literature": [{"url": source["url"]}],
                "research_links": [{"delegation_id": delegation, "insight_id": insight["id"],
                                    "method_spec_ref": "/method_spec/contract_input",
                                    "adaptation_reason": "Human-authored schema-link input, not a research conclusion."}]}
    reports = [{"delegation_id": delegation, "report": report}]
    assert research_link_errors(metadata, reports, min_sources=1, require_linked_sources=True) == []
    for url in (changed_version(source["url"]), re.sub(r"v[0-9]+$", "", source["url"])):
        metadata["related_literature"][0]["url"] = url
        assert any("same document version" in error for error in research_link_errors(
            metadata, reports, min_sources=1, require_linked_sources=True))
