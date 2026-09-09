"""Human-authored declaration inputs; no model, service, or execution substitutes."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.agents.idea.research_assessment import assessment_errors, assessment_schema


def documents() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Supply parsed declarations only, without claiming verified provenance."""
    metadata: dict[str, Any] = {
        "research_links": [{"delegation_id": "reading_a", "insight_id": "finding_a",
                            "method_spec_ref": "/method_spec/candidate",
                            "adaptation_reason": "A human-authored example of a declared method transfer."}],
        "research_assessment": {
            "version": "idea.research_assessment.v1",
            "task_question": "Which representation can satisfy the specified parameter budget?",
            "selection_principles": [{"id": "budget", "criterion": "Provides explicit parameter accounting",
                                      "task_basis": "The research task limits trainable real scalars."}],
            "stopping_reason": "The available findings define a falsifiable candidate and its limitations.",
            "remaining_gaps": ["Whether the proposed transfer improves held-out prediction remains unknown."],
            "source_decisions": [{
                "delegation_id": "reading_a", "source_id": "paper_a", "decision": "adopt",
                "reason": "The declared finding defines the candidate's parameter allocation.",
                "task_relevance": "Its counting rule addresses the task's explicit resource limit.",
                "criterion_ids": ["budget"], "insight_ids": ["finding_a"],
                "transfer_assumptions": ["The original scalar counting convention applies to this task."],
            }],
        },
    }
    reports: list[dict[str, Any]] = [{"delegation_id": "reading_a", "report": {
        "sources": [{"source_id": "paper_a", "decision": "use"}],
        "insights": [{"id": "finding_a", "source_id": "paper_a"}],
    }}]
    return metadata, reports


def test_valid_declarations_do_not_establish_scientific_usefulness() -> None:
    metadata, reports = documents()
    before = deepcopy((metadata, reports))
    Draft202012Validator.check_schema(assessment_schema())
    assert assessment_errors(metadata, reports, required=True) == []
    assert (metadata, reports) == before


def test_missing_assessment_is_legacy_only() -> None:
    assert assessment_errors({}, []) == []
    assert assessment_errors({}, [], required=True)


@pytest.mark.parametrize("value", [None, [], {}, "text", {"version": "v0"}])
def test_present_malformed_assessment_is_rejected_without_opt_in(value: Any) -> None:
    assert assessment_errors({"research_assessment": value}, [])


@pytest.mark.parametrize("field", ["task_question", "selection_principles", "stopping_reason",
                                  "remaining_gaps", "source_decisions", "version"])
def test_all_assessment_fields_are_required(field: str) -> None:
    metadata, reports = documents()
    del metadata["research_assessment"][field]
    assert assessment_errors(metadata, reports)


@pytest.mark.parametrize("field", ["task_question", "stopping_reason"])
@pytest.mark.parametrize("value", ["", " " * 20, "short", "x" * 1201])
def test_task_explanations_are_nonempty_and_bounded(field: str, value: str) -> None:
    metadata, reports = documents()
    metadata["research_assessment"][field] = value
    assert assessment_errors(metadata, reports)


@pytest.mark.parametrize("field", ["reason", "task_relevance"])
@pytest.mark.parametrize("value", ["", "\n" * 20, "generic", "x" * 1201])
def test_source_explanations_are_nonempty_and_bounded(field: str, value: str) -> None:
    metadata, reports = documents()
    metadata["research_assessment"]["source_decisions"][0][field] = value
    assert assessment_errors(metadata, reports)


def test_schema_rejects_undeclared_fields() -> None:
    metadata, reports = documents()
    metadata["research_assessment"]["scientific_validated"] = True
    assert assessment_errors(metadata, reports)


def test_empty_remaining_gaps_is_an_explicit_declaration() -> None:
    metadata, reports = documents()
    metadata["research_assessment"]["remaining_gaps"] = []
    assert assessment_errors(metadata, reports) == []


def test_criterion_ids_are_unique_and_resolve() -> None:
    metadata, reports = documents()
    metadata["research_assessment"]["selection_principles"] *= 2
    assert any("duplicate criterion" in error for error in assessment_errors(metadata, reports))
    metadata["research_assessment"]["selection_principles"].pop()
    metadata["research_assessment"]["source_decisions"][0]["criterion_ids"] = ["unknown"]
    assert any("unknown selection criterion" in error for error in assessment_errors(metadata, reports))


@pytest.mark.parametrize("field", ["criterion_ids", "insight_ids", "transfer_assumptions"])
def test_adoption_requires_criteria_findings_and_transfer_assumptions(field: str) -> None:
    metadata, reports = documents()
    metadata["research_assessment"]["source_decisions"][0][field] = []
    assert assessment_errors(metadata, reports)


def test_one_cited_source_does_not_cover_another_used_source() -> None:
    metadata, reports = documents()
    reports[0]["report"]["sources"].append({"source_id": "paper_b", "decision": "use"})
    reports[0]["report"]["insights"].append({"id": "finding_b", "source_id": "paper_b"})
    errors = assessment_errors(metadata, reports)
    assert any("missing decision for reading_a/paper_b" in error for error in errors)
    decision = deepcopy(metadata["research_assessment"]["source_decisions"][0])
    decision.update(source_id="paper_b", decision="exclude", insight_ids=[], transfer_assumptions=[])
    metadata["research_assessment"]["source_decisions"].append(decision)
    assert assessment_errors(metadata, reports) == []


@pytest.mark.parametrize("field", ["source_id", "delegation_id"])
def test_source_must_exist_in_named_delegation(field: str) -> None:
    metadata, reports = documents()
    metadata["research_assessment"]["source_decisions"][0][field] = "unknown"
    assert any("source does not exist" in error for error in assessment_errors(metadata, reports))


def test_no_trusted_reports_cannot_be_replaced_by_proposal_claims() -> None:
    metadata, _ = documents()
    metadata["reports"] = [{"delegation_id": "reading_a", "report": {"sources": []}}]
    assert any("source does not exist" in error for error in assessment_errors(metadata, []))


def test_insight_cannot_cross_source_boundary() -> None:
    metadata, reports = documents()
    reports[0]["report"]["sources"].append({"source_id": "paper_b", "decision": "defer"})
    reports[0]["report"]["insights"][0]["source_id"] = "paper_b"
    assert any("does not belong" in error for error in assessment_errors(metadata, reports))


def test_matching_ids_cannot_cross_delegation_boundary() -> None:
    metadata, reports = documents()
    reports.append({"delegation_id": "reading_b", "report": {
        "sources": [{"source_id": "paper_a", "decision": "use"}], "insights": []}})
    metadata["research_assessment"]["source_decisions"][0]["delegation_id"] = "reading_b"
    assert any("does not belong" in error for error in assessment_errors(metadata, reports))


@pytest.mark.parametrize("decision", ["reject", "defer"])
def test_report_rejected_or_deferred_source_cannot_be_adopted(decision: str) -> None:
    metadata, reports = documents()
    reports[0]["report"]["sources"][0]["decision"] = decision
    assert any("cannot adopt" in error for error in assessment_errors(metadata, reports))


@pytest.mark.parametrize("decision", ["exclude", "defer"])
def test_excluded_and_deferred_sources_have_no_adopted_findings_or_links(decision: str) -> None:
    metadata, reports = documents()
    item = metadata["research_assessment"]["source_decisions"][0]
    item["decision"] = decision
    assert assessment_errors(metadata, reports)
    item["insight_ids"] = []
    item["transfer_assumptions"] = []
    assert any("cannot have research_links" in error for error in assessment_errors(metadata, reports))
    metadata["research_links"] = []
    assert assessment_errors(metadata, reports) == []


def test_assessment_cannot_claim_an_insight_absent_from_method_links() -> None:
    metadata, reports = documents()
    reports[0]["report"]["insights"].append({"id": "finding_b", "source_id": "paper_a"})
    metadata["research_assessment"]["source_decisions"][0]["insight_ids"].append("finding_b")
    assert any("exactly match" in error for error in assessment_errors(metadata, reports))


def test_linked_insight_cannot_be_omitted_from_assessment() -> None:
    metadata, reports = documents()
    reports[0]["report"]["insights"].append({"id": "finding_b", "source_id": "paper_a"})
    link = deepcopy(metadata["research_links"][0])
    link["insight_id"] = "finding_b"
    metadata["research_links"].append(link)
    assert any("exactly match" in error for error in assessment_errors(metadata, reports))
    metadata["research_assessment"]["source_decisions"][0]["insight_ids"].append("finding_b")
    assert assessment_errors(metadata, reports) == []


def test_same_insight_may_inform_multiple_method_fields() -> None:
    metadata, reports = documents()
    link = deepcopy(metadata["research_links"][0])
    link["method_spec_ref"] = "/method_spec/ablation"
    metadata["research_links"].append(link)
    assert assessment_errors(metadata, reports) == []


def test_duplicate_source_decisions_are_rejected() -> None:
    metadata, reports = documents()
    metadata["research_assessment"]["source_decisions"] *= 2
    assert any("duplicate source decision" in error for error in assessment_errors(metadata, reports))


@pytest.mark.parametrize("links", [None, {}, "not an array", [None], [{}],
                                   [{"delegation_id": "reading_a", "insight_id": "unknown"}]])
def test_malformed_or_unresolved_links_are_reported(links: Any) -> None:
    metadata, reports = documents()
    metadata["research_links"] = links
    assert assessment_errors(metadata, reports)


def test_ambiguous_trusted_report_identities_fail_closed() -> None:
    metadata, reports = documents()
    reports *= 2
    assert any("duplicate trusted delegation" in error for error in assessment_errors(metadata, reports))
