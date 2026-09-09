"""Check declared research choices against trusted reports, not scientific usefulness."""
from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator


def assessment_schema() -> dict[str, Any]:
    """Return the portable proposal research-assessment contract."""
    identifier = {"type": "string", "minLength": 1, "maxLength": 128, "pattern": r"\S"}
    explanation = {"type": "string", "minLength": 12, "maxLength": 1200, "pattern": r"\S"}
    item_text = {"type": "string", "minLength": 1, "maxLength": 1200, "pattern": r"\S"}
    decision = {
        "type": "object", "additionalProperties": False,
        "required": ["delegation_id", "source_id", "decision", "reason", "task_relevance",
                     "criterion_ids", "insight_ids", "transfer_assumptions"],
        "properties": {
            "delegation_id": identifier, "source_id": identifier,
            "decision": {"enum": ["adopt", "exclude", "defer"]},
            "reason": explanation, "task_relevance": explanation,
            "criterion_ids": {"type": "array", "minItems": 1, "maxItems": 16,
                              "uniqueItems": True, "items": identifier},
            "insight_ids": {"type": "array", "maxItems": 24,
                            "uniqueItems": True, "items": identifier},
            "transfer_assumptions": {"type": "array", "maxItems": 16,
                                     "uniqueItems": True, "items": item_text},
        },
        "if": {"properties": {"decision": {"const": "adopt"}}},
        "then": {"properties": {"insight_ids": {"minItems": 1},
                                "transfer_assumptions": {"minItems": 1}}},
        "else": {"properties": {"insight_ids": {"maxItems": 0}}},
    }
    return {
        "type": "object", "additionalProperties": False,
        "required": ["version", "task_question", "selection_principles", "stopping_reason",
                     "remaining_gaps", "source_decisions"],
        "properties": {
            "version": {"const": "idea.research_assessment.v1"},
            "task_question": explanation, "stopping_reason": explanation,
            "selection_principles": {"type": "array", "minItems": 1, "maxItems": 16, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["id", "criterion", "task_basis"],
                "properties": {"id": identifier, "task_basis": explanation,
                    "criterion": {"type": "string", "minLength": 8, "maxLength": 600, "pattern": r"\S"}},
            }},
            "remaining_gaps": {"type": "array", "maxItems": 24,
                               "uniqueItems": True, "items": item_text},
            "source_decisions": {"type": "array", "minItems": 1, "maxItems": 96, "items": decision},
        },
    }


def assessment_errors(metadata: dict[str, Any], reports: list[dict[str, Any]], *,
                      required: bool = False) -> list[str]:
    """Reports must come from the trusted delegation loader, never proposal fields.

    This checks coverage and source/insight identities. It cannot establish that
    stated criteria are relevant, a paper was understood, or a transfer is sound.
    """
    prefix = "/research_assessment"
    if "research_assessment" not in metadata:
        return [prefix + ": required task-grounded research assessment"] if required else []
    assessment = metadata["research_assessment"]
    errors = [prefix + "/" + "/".join(map(str, error.absolute_path)) + ": " + error.message
              for error in Draft202012Validator(assessment_schema()).iter_errors(assessment)]
    if errors:
        return errors

    principles = {item["id"] for item in assessment["selection_principles"]}
    if len(principles) != len(assessment["selection_principles"]):
        errors.append(prefix + "/selection_principles: duplicate criterion id")
    sources: dict[tuple[str, str], dict[str, Any]] = {}
    insights: dict[tuple[str, str], str] = {}
    delegations: set[str] = set()
    for item in reports:
        delegation_id = item["delegation_id"]
        if delegation_id in delegations:
            errors.append(prefix + ": duplicate trusted delegation id")
        delegations.add(delegation_id)
        for source in item["report"].get("sources", []):
            source_key = (delegation_id, source["source_id"])
            if source_key in sources:
                errors.append(prefix + ": duplicate source id in trusted report")
            sources[source_key] = source
        for insight in item["report"].get("insights", []):
            insight_key = (delegation_id, insight["id"])
            if insight_key in insights:
                errors.append(prefix + ": duplicate insight id in trusted report")
            insights[insight_key] = insight["source_id"]

    # Resolve links through their report: proposal-written source ids cannot
    # relabel another paper's insight as evidence for an adopted paper.
    linked: dict[tuple[str, str], set[str]] = {}
    links = metadata.get("research_links", [])
    if not isinstance(links, list):
        errors.append(prefix + ": research_links must be an array to reconcile adopted insights")
        links = []
    for index, link in enumerate(links):
        if (not isinstance(link, dict) or not isinstance(link.get("delegation_id"), str)
                or not isinstance(link.get("insight_id"), str)):
            errors.append(prefix + f": research_links/{index} lacks a delegation/insight identity")
            continue
        source_id = insights.get((link["delegation_id"], link["insight_id"]))
        if source_id is None:
            errors.append(prefix + f": research_links/{index} does not resolve in a trusted report")
            continue
        linked.setdefault((link["delegation_id"], source_id), set()).add(link["insight_id"])

    assessed: set[tuple[str, str]] = set()
    for index, decision in enumerate(assessment["source_decisions"]):
        location = prefix + f"/source_decisions/{index}"
        key = (decision["delegation_id"], decision["source_id"])
        if key in assessed:
            errors.append(location + ": duplicate source decision in the same delegation")
        assessed.add(key)
        if not set(decision["criterion_ids"]) <= principles:
            errors.append(location + "/criterion_ids: unknown selection criterion")
        source = sources.get(key)
        if source is None:
            errors.append(location + ": source does not exist in the named trusted delegation")
        for insight_id in decision["insight_ids"]:
            if insights.get((decision["delegation_id"], insight_id)) != decision["source_id"]:
                errors.append(location + "/insight_ids: insight does not belong to this source and delegation")
        if decision["decision"] == "adopt":
            if source is not None and source.get("decision") != "use":
                errors.append(location + ": cannot adopt a report source rejected or deferred by the researcher")
            if set(decision["insight_ids"]) != linked.get(key, set()):
                errors.append(location + "/insight_ids: adopted insights must exactly match this source's research_links")
        elif linked.get(key):
            errors.append(location + ": excluded or deferred source cannot have research_links")

    required_sources = {key for key, source in sources.items() if source.get("decision") == "use"}
    for delegation_id, source_id in sorted((required_sources | set(linked)) - assessed):
        errors.append(prefix + f"/source_decisions: missing decision for {delegation_id}/{source_id}; "
                      "assess each used report source separately")
    return errors
