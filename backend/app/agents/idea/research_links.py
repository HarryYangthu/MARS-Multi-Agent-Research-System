"""Bind proposal decisions to verified research reports, without proving their meaning."""
from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator

from app.agents.idea.delivery import resolve_pointer
from app.agents.idea.publication_count import count_report_publications
from app.agents.idea.source_identity import document_key


def research_links_schema() -> dict[str, Any]:
    return {"type": "array", "minItems": 1, "maxItems": 24, "items": {
        "type": "object", "additionalProperties": False,
        "required": ["delegation_id", "insight_id", "method_spec_ref", "adaptation_reason"],
        "properties": {"delegation_id": {"type": "string", "minLength": 1},
                       "insight_id": {"type": "string", "minLength": 1},
                       "method_spec_ref": {"type": "string", "pattern": "^/method_spec/"},
                       "adaptation_reason": {"type": "string", "minLength": 12, "maxLength": 1200}}}}


def research_link_errors(metadata: dict[str, Any], reports: list[dict[str, Any]], *, min_sources: int = 1,
                         require_linked_sources: bool = False) -> list[str]:
    """Reports must come from the trusted delegation loader, never proposal fields."""
    links = metadata.get("research_links")
    errors = ["/research_links/" + "/".join(map(str, e.absolute_path)) + ": " + e.message
              for e in Draft202012Validator(research_links_schema()).iter_errors(links)]
    if errors:
        return errors
    if not isinstance(links, list):
        return ["/research_links: array required"]
    if not reports:
        return ["/research_links: complete a real research delegation before submitting; no verified report exists"]
    indexed = {str(item["delegation_id"]): item["report"] for item in reports}
    read_sources = count_report_publications(reports)
    if not require_linked_sources and read_sources.count < min_sources:
        errors.append(f"/research_links: require findings from {min_sources} distinct read publications; observed {read_sources.count}"
                      + read_sources.diagnostic())
    citations = {document_key(str(item.get("url", "")))
                 for item in metadata.get("related_literature", []) if isinstance(item, dict)}
    seen: set[tuple[str, str, str]] = set()
    linked_sources: set[tuple[str, str]] = set()
    for index, link in enumerate(links):
        errors_before = len(errors)
        prefix = f"/research_links/{index}"
        key = (link["delegation_id"], link["insight_id"], link["method_spec_ref"])
        if key in seen:
            errors.append(prefix + ": duplicate research-to-method link")
        seen.add(key)
        report = indexed.get(link["delegation_id"], {})
        insights = {x["id"]: x for x in report.get("insights", [])}
        insight = insights.get(link["insight_id"])
        if insight is None:
            errors.append(prefix + ": insight does not exist in the named verified delegation")
        else:
            sources = {x["source_id"]: x for x in report.get("sources", [])}
            source = sources.get(insight["source_id"], {})
            if source.get("decision") != "use":
                errors.append(prefix + ": linked source was not selected for use")
            source_key = document_key(str(source.get("url", "")))
            if not source_key or source_key not in citations:
                errors.append(prefix + ": cite the linked insight's original source in related_literature with the same document version; copy the verified report source URL, not a guessed latest/version alias")
        try:
            resolve_pointer(metadata, link["method_spec_ref"])
        except ValueError as exc:
            errors.append(prefix + ": " + str(exc))
        if insight is not None and len(errors) == errors_before:
            linked_sources.add((link["delegation_id"], str(source["source_id"])))
    linked = count_report_publications(reports, selected=linked_sources)
    if require_linked_sources and linked.count < min_sources:
        errors.append(f"/research_links: require {min_sources} distinct publications linked to actual method definitions; "
                      f"observed {linked.count}. Reading or citing an unused paper does not satisfy this requirement."
                      + linked.diagnostic())
    return errors
