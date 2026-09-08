"""A real retrieved title remains valid after a second metadata alias is observed."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.agents.idea.research import evidence_inventory, material_errors
from app.harness.schema.frontmatter_parser import parse


def test_empty_inventory_has_no_authored_aliases() -> None:
    assert evidence_inventory([])["papers"] == []


def test_real_conv2warp_observed_title_alias_and_unobserved_title() -> None:
    archive = os.environ.get("MARS_TEST_TITLE_ALIAS_RUN")
    if not archive:
        pytest.skip("requires explicit actual-run checkpoints")
    root = Path(archive)
    main = next((root / "agent_traces" / "idea").glob("*/checkpoint.json"))
    metadata = parse(json.loads(main.read_text())["candidate"]).metadata
    observations = []
    for checkpoint in (root / "agent_traces" / "idea_research").glob("*/checkpoint.json"):
        observations.extend(json.loads(checkpoint.read_text())["history"])
    papers = evidence_inventory(observations)["papers"]
    paper = next(paper for paper in papers if "Conv2Warp" in paper["title"])
    assert len(paper["observed_titles"]) >= 2
    for title in paper["observed_titles"]:
        metadata["related_literature"] = [{"title": title, "url": paper["url"]}]
        errors = material_errors(metadata, observations, min_sources=2, min_pdfs=0,
                                 require_budget=False, max_ratio=1.2)
        assert not any("URL/title not matched" in error for error in errors)
        assert any("need 2 distinct retrieved cited sources; observed 1" in error for error in errors)
    metadata["related_literature"] = [{"title": "Unobserved invented title", "url": paper["url"]}]
    errors = material_errors(metadata, observations, min_sources=2, min_pdfs=0,
                             require_budget=False, max_ratio=1.2)
    assert any("URL/title not matched" in error for error in errors)
