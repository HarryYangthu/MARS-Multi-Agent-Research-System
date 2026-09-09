"""Explicit child scope; a one-paper report never satisfies the parent's global floor."""
from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

from app.harness.agent_loop.trace import digest
from app.harness.llm.provider_base import Message

RESEARCH_UNIT_CONTRACT = "idea.research_single_publication.v1"
UNIT_TOOL_NOTE = (
    "This run uses a single-publication delegation policy. Explicitly set min_sources=1; "
    "other values are rejected, not clamped. Assign a bounded question answerable using one relevant "
    "publication. Keep the final task's independent-publication total and method-link requirements separate."
)
UNIT_PARENT_GUIDANCE = (
    "\nHost research policy: " + RESEARCH_UNIT_CONTRACT + ". " + UNIT_TOOL_NOTE + " "
    "Write gap and success_criteria consistently with this scope; do not copy the global paper quota "
    "into a child assignment. Combine independently accepted, complementary reports to satisfy the "
    "unchanged final task requirements. Repeated windows or channels of the same publication do not "
    "supply another independent paper. A child still needs real source metadata, visible PDF evidence, "
    "supported interpretations and independent scientific review. Failed reads and rejected drafts "
    "remain recovery leads, never accepted reports. If a child reports an incompatible assignment, "
    "explicitly correct the next assignment; do not reinterpret its failure as success."
)
_UNIT_SCOPE = (
    "Host delegation scope: " + RESEARCH_UNIT_CONTRACT + ". "
    "This child is a single-publication research unit: its evidence minimum is one acceptable relevant "
    "independent publication, not the complete task's aggregate publication minimum. The parent must "
    "still obtain the globally required independent publications from accepted reports and bind them "
    "to real method decisions. This changes work decomposition only; it does not relax source identity, "
    "visible PDF/quote checks, relevance, scientific correctness, limitations or independent review. "
    "Additional searches or readings are allowed when needed to establish a defensible interpretation; "
    "do not pad the report or assert the overall research task is complete. The full original task and "
    "delegated gap remain authoritative about their actual scientific questions and constraints. "
    "A global count appearing only in the overall task is the parent's responsibility. If the delegated "
    "gap or success_criteria itself demands multiple publications or another incompatible scope, "
    "explicitly report that assignment conflict; do not silently ignore it, rewrite it, or claim it resolved. "
)


def configured_research_unit(research: Mapping[str, Any]) -> dict[str, Any] | None:
    """Absence preserves the historical contract; there is no implicit default override."""
    if "per_delegation_min_sources" not in research:
        return None
    value = research["per_delegation_min_sources"]
    if type(value) is not int or value != 1:
        raise ValueError("research.per_delegation_min_sources must be exactly the integer 1 when configured")
    return {"contract_id": RESEARCH_UNIT_CONTRACT, "per_delegation_min_sources": 1}


def validate_research_unit(unit: dict[str, Any]) -> None:
    if (set(unit) != {"contract_id", "per_delegation_min_sources"}
            or unit.get("contract_id") != RESEARCH_UNIT_CONTRACT
            or type(unit.get("per_delegation_min_sources")) is not int
            or unit["per_delegation_min_sources"] != 1):
        raise ValueError("unknown or invalid host research_unit contract")


def validate_unit_arguments(args: dict[str, Any], unit: dict[str, Any] | None) -> None:
    if unit is None:
        return
    validate_research_unit(unit)
    if type(args.get("min_sources")) is not int or args["min_sources"] != 1:
        raise ValueError("this run requires explicit min_sources=1 for each delegation; missing or other "
                         "values are rejected, not clamped. Keep the overall paper quota in the parent task.")


def unit_input_constraint() -> dict[str, Any]:
    return {"type": "object", "required": ["min_sources"],
            "properties": {"min_sources": {"type": "integer", "const": 1}}}


def unit_fields(unit: dict[str, Any] | None) -> dict[str, Any]:
    if unit is None:
        return {}
    validate_research_unit(unit)
    return {"research_unit": dict(unit)}


def unit_messages(unit: dict[str, Any] | None, *, reviewing: bool) -> list[Message]:
    if unit is None:
        return []
    validate_research_unit(unit)
    instruction = (
        "Reject a materially unresolved assignment conflict with a concrete scope issue. Do not reject "
        "solely because this one-paper report does not meet the parent task's global publication total. "
        "Review all of this report's claims and actual evidence under the unchanged scientific rubric."
        if reviewing else
        "If an assignment conflict prevents this unit from answering its delegated question, submit "
        "research_gap.v1 with that explicit conflict. Otherwise deliver a complete, grounded research_report.v1 "
        "for the bounded question; meeting the count alone does not make a report acceptable."
    )
    return [Message("system", _UNIT_SCOPE + instruction)]


def research_unit_errors(manifest: dict[str, Any], output: dict[str, Any], *,
                         request: dict[str, Any] | None, arguments: object,
                         events: list[dict[str, Any]] | None = None) -> list[str]:
    """Bind a new host claim to the original request and actual author/whole inputs.

    No result is upgraded: passed candidate, evidence and review checks remain in
    the delegated-report loader, independently of this additional scope check.
    """
    records = [manifest, output] + ([request] if request is not None else [])
    if not any("research_unit" in record for record in records):
        return []
    unit = manifest.get("research_unit")
    try:
        if not isinstance(unit, dict):
            raise ValueError("research_unit must be present in the host manifest")
        validate_research_unit(unit)
        if request is None or any(record.get("research_unit") != unit for record in records):
            raise ValueError("research_unit differs between original request, manifest and output")
        for record in records:
            validate_research_unit(record["research_unit"])
        if request.get("arguments") != arguments or not isinstance(arguments, dict):
            raise ValueError("research_unit original arguments differ from the delegate observation")
        validate_unit_arguments(arguments, unit)
        if any(type(record.get("min_sources")) is not int or record["min_sources"] != 1
               for record in (manifest, request)):
            raise ValueError("research_unit evidence minimum differs from its original arguments")
        if any(output.get(key) != manifest.get(key) or not isinstance(manifest.get(key), str)
               for key in ("request_ref", "request_sha256")):
            raise ValueError("research_unit request reference/hash differs between manifest and output")
        if any(record.get("model_review_required") is not True or record.get("model_review_passed") is not True
               for record in (manifest, output)):
            raise ValueError("research_unit requires an independently accepted report review")
        context = request.get("review_context")
        if (not isinstance(context, dict) or set(context) != {"task", "project", "supplied_context"}
                or not isinstance(context["task"], str) or not isinstance(context["project"], str)
                or not isinstance(context["supplied_context"], dict)
                or any(not isinstance(k, str) or not isinstance(v, str)
                       for k, v in context["supplied_context"].items())):
            raise ValueError("research_unit requires the original task, project and supplied context")
        if not events:
            raise ValueError("research_unit requires actual author and whole-review trace inputs")
        counts = {False: 0, True: 0}
        for event in events:
            if event.get("kind") != "model_request":
                continue
            reviewing = event.get("phase") == "reflect"
            if reviewing and event.get("review_unit_id") not in (None, "__whole_report__"):
                continue  # Isolated insight checks retain their original scope and exact inputs.
            visible = event.get("visible")
            expected = unit_messages(unit, reviewing=reviewing)[0].to_wire()
            if (not isinstance(visible, list) or digest(visible) != event.get("visible_sha256")
                    or visible.count(expected) != 1):
                raise ValueError("research_unit is absent, repeated or changed in actual author/whole-review input")
            gap_prefix = ("Delegated evidence gap and completion criteria:\n" if reviewing
                          else "Delegated gap and completion criteria:\n")
            gaps = [item["content"][len(gap_prefix):] for item in visible
                    if isinstance(item, dict) and item.get("role") == "user"
                    and isinstance(item.get("content"), str) and item["content"].startswith(gap_prefix)]
            if len(gaps) != 1 or json.loads(gaps[0]) != arguments:
                raise ValueError("research_unit actual input does not preserve the original delegated gap")
            task_prefix = "Complete research task:\n" if reviewing else "Overall research task:\n"
            expected_context = [Message("user", task_prefix + context["task"]).to_wire(),
                (Message("user", "Project constraints:\n" + context["project"]) if reviewing
                 else Message("system", context["project"])).to_wire()]
            expected_context.extend(Message("user", "[untrusted supplied context:" + name + "]\n" + content).to_wire()
                                    for name, content in context["supplied_context"].items())
            if any(item not in visible for item in expected_context):
                raise ValueError("research_unit actual input does not preserve the original task/project/context")
            counts[reviewing] += 1
        if not all(counts.values()):
            raise ValueError("research_unit requires actual author and whole-review trace inputs")
    except (ValueError, TypeError, KeyError) as exc:
        return [str(exc)]
    return []
