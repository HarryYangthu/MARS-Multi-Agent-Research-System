"""Read-only replay of the first actual API run; never a replacement execution."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from typing import Iterator

import pytest

from app.agents.idea.protocol import protocol_errors
from app.agents.idea.research_delegate import load_delegated_research
from app.agents.idea.research_review_plan import (
    LEGACY_REVIEW_PLAN_CONTRACT, REVIEW_PLAN_CONTRACT, build_research_review_plan,
    research_plan_errors,
)
from app.harness.agent_loop.review_plan import plan_payload
from app.harness.schema.frontmatter_parser import parse


@pytest.fixture
def archive() -> Iterator[Path]:
    configured = os.environ.get("MARS_TEST_IDEA_API_ARCHIVE")
    if not configured:
        pytest.skip("requires the actual first API failure archive")
    root = Path(configured)
    originals = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in root.rglob("*") if path.is_file()}
    yield root
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == sha for path, sha in originals.items())


def test_old_primary_omission_and_new_explicit_binding_do_not_false_reject(archive: Path) -> None:
    state = json.loads(next((archive / "agent_traces/idea").glob("*/checkpoint.json")).read_text())
    metadata = parse(state["candidate"]).metadata
    original = deepcopy(metadata)
    assert "decision_rule_ref" not in metadata["evaluation_protocol"]["comparison"]
    assert protocol_errors(metadata, required=True) == []
    # A schema-only edit to a copy, not a corrected/accepted scientific proposal.
    derived = deepcopy(metadata)
    derived["evaluation_protocol"]["comparison"]["decision_rule_ref"] = "/decision_rule"
    assert protocol_errors(derived, required=True) == []
    derived["evaluation_protocol"]["comparison"]["decision_rule_ref"] = "/decision_rule/missing"
    assert any("unresolved" in error for error in protocol_errors(derived))
    assert metadata == original and state["status"] != "passed"


def test_v2_review_remains_bound_to_original_inputs_without_claiming_v3(archive: Path) -> None:
    lead = json.loads(next((archive / "agent_traces/idea").glob("*/checkpoint.json")).read_text())
    reports, evidence = load_delegated_research(archive, lead["history"])
    assert len(reports) == 1 and evidence
    manifests = list((archive / "idea/research_delegations").glob("*/manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text())
    assert manifest["review_plan"]["contract_id"] == LEGACY_REVIEW_PLAN_CONTRACT
    checkpoint = json.loads((archive / manifest["checkpoint_ref"]).read_text())
    request = json.loads((archive / manifest["request_ref"]).read_text())
    context = {**request["review_context"], "gap": request["arguments"], "min_sources": request["min_sources"]}
    old = build_research_review_plan(checkpoint["candidate"], checkpoint["history"],
                                    contract_id=LEGACY_REVIEW_PLAN_CONTRACT, **context)
    assert all(checkpoint["review_plan"][key] == value for key, value in plan_payload(old).items())
    new = build_research_review_plan(checkpoint["candidate"], checkpoint["history"], **context)
    assert new.contract_id == REVIEW_PLAN_CONTRACT != old.contract_id
    assert plan_payload(new) != plan_payload(old)
    for old_unit, new_unit in zip(old.units, new.units, strict=True):
        assert old_unit.messages[1:] == new_unit.messages[1:]
        assert old_unit.evidence_bindings == new_unit.evidence_bindings
    # Tampering with matching claims cannot upgrade the actual old model review.
    observation = next(item for item in lead["history"] if item.get("ok")
                       and item.get("tool") == "idea.research_delegate")
    changed_manifest, changed_output = deepcopy(manifest), deepcopy(observation["output"])
    for record in (changed_manifest, changed_output):
        record["review_plan"]["contract_id"] = REVIEW_PLAN_CONTRACT
    assert research_plan_errors(changed_manifest, changed_output, checkpoint, checkpoint["candidate"],
                                trace_root=(archive / manifest["checkpoint_ref"]).parent, request_record=request)
    # Historical model acceptance does not override the separately recorded semantic failure.
    assert checkpoint["reflection_accepted"] is True
    assert (archive / "manual_research_review.json").is_file()
