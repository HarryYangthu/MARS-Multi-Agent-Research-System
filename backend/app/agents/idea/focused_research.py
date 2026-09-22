"""Format-neutral research links backed by actual, archived reading windows."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from app.agents.idea.delivery import resolve_pointer


def validate_review_mode(author: tuple[str, str], reviewer: tuple[str, str], mode: object = "cross_model") -> str:
    """Keep historical cross-model admission strict; same-model review is explicit."""
    if mode not in ("cross_model", "independent_session"):
        raise ValueError("review_mode must be cross_model or independent_session")
    if mode == "cross_model" and author == reviewer:
        raise ValueError("generation and review models must differ for cross_model review")
    return str(mode)


def research_schema(*, version: int = 1) -> dict[str, Any]:
    text = {"type": "string", "minLength": 1}
    strings = {"type": "array", "items": text}
    source: dict[str, Any] = {"type": "object", "required": ["source_id", "title", "url", "decision", "reason"],
              "properties": {"source_id": {"type": "string"}, "title": text, "url": text,
                  "decision": {"enum": ["use", "reject", "defer"]}, "reason": text,
                  "method_sections": strings, "method_summary": text, "transfer": text,
                  "limitations": text, "method_spec_ref": text}}
    if version == 2:
        source["properties"]["method_pages"] = {"type": "array", "uniqueItems": True,
                                                "items": {"type": "integer", "minimum": 1}}
        source["allOf"] = [{"if": {"properties": {"decision": {"const": "use"}}},
                            "then": {"required": ["method_pages"]}}]
    return {"type": "object", "required": ["schema", "question", "selection_principles", "sources", "stop_reason", "open_questions"],
            "properties": {"schema": {"const": f"idea.research_context.v{version}"}, "question": text,
                "selection_principles": {**strings, "minItems": 1},
                "sources": {"type": "array", "minItems": 1, "items": source},
                "stop_reason": text, "open_questions": strings}}


def reading_sources(observations: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        if observation.get("tool") != "search.fetch_sources":
            continue
        output = observation.get("output", {})
        if not isinstance(output, dict):
            continue
        for row in output.get("sources", []):
            if not isinstance(row, dict) or not row.get("ok") or not row.get("archive_complete"):
                continue
            source_id = row.get("source_id")
            if isinstance(source_id, str) and source_id:
                result.setdefault(source_id, []).append(row)
    return result


def observed_urls(value: Any) -> set[str]:
    urls: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"url", "pdf_url", "download_url", "final_url", "id", "evidence_ref"} and isinstance(child, str) and child.startswith(("http://", "https://")):
                urls.add(child)
            elif isinstance(child, (dict, list)):
                urls.update(observed_urls(child))
    elif isinstance(value, list):
        for child in value:
            urls.update(observed_urls(child))
    return urls


def focused_research_errors(metadata: dict[str, Any], observations: list[dict[str, Any]], run_root: Path) -> list[str]:
    context = metadata.get("research_context")
    if not isinstance(context, dict):
        return ["/research_context: actual research and method reading are required"]
    from jsonschema import Draft202012Validator
    version = 2 if context.get("schema") == "idea.research_context.v2" else 1
    errors = ["/research_context/" + "/".join(map(str, e.absolute_path)) + ": " + e.message
              for e in Draft202012Validator(research_schema(version=version)).iter_errors(context)]
    if errors:
        return errors
    readings, urls = reading_sources(observations), observed_urls([o.get("output", {}) for o in observations])
    adopted: set[str] = set()
    for i, source in enumerate(context["sources"]):
        prefix = f"/research_context/sources/{i}"
        if source["url"] not in urls:
            errors.append(prefix + "/url: must refer to an actually retrieved source URL")
        if source["decision"] != "use":
            continue
        source_id = source["source_id"]
        if source_id in adopted:
            errors.append(prefix + ": duplicate adopted document")
        adopted.add(source_id)
        rows = readings.get(source_id, [])
        if not rows:
            errors.append(prefix + "/source_id: copy the source_id from successful full-text reading observations; abstracts alone are insufficient")
            continue
        if source["url"] not in observed_urls(rows):
            errors.append(prefix + "/url: does not belong to this read document; use its returned URL")
        for field in ("method_summary", "transfer", "limitations", "method_spec_ref"):
            if not isinstance(source.get(field), str) or not source[field].strip():
                errors.append(prefix + "/" + field + ": explain the actual method and its transfer")
        if not source.get("method_sections"):
            errors.append(prefix + "/method_sections: identify the method sections and dependencies actually read")
        if version == 2 and any(row.get("source_type") == "pdf" for row in rows):
            from app.harness.agent_loop.context import reading_coverage_index
            coverage = [page for document in reading_coverage_index(observations)
                        if document["source_id"] == source_id
                        and document["sha256"] == rows[0].get("sha256") for page in document["pages"]]
            complete = {page["page"] for page in coverage if page["complete_extracted_text"]}
            declared = set(source.get("method_pages", []))
            if not declared or not declared.issubset(complete):
                errors.append(prefix + "/method_pages: declare the operative method pages and finish their actual text windows; "
                              f"fully covered pages={sorted(complete)}, incomplete/missing={sorted(declared - complete)}")
        try:
            ref = str(source.get("method_spec_ref", ""))
            if not ref.startswith("/method_spec/"):
                raise ValueError("must point into /method_spec/")
            resolve_pointer(metadata, ref)
        except ValueError as exc:
            errors.append(prefix + "/method_spec_ref: " + str(exc))
        for row in rows:
            path = Path(str(row.get("download_path", "")))
            if (not path.resolve().is_relative_to(run_root.resolve()) or not path.is_file()
                    or hashlib.sha256(path.read_bytes()).hexdigest() != row.get("sha256")):
                errors.append(prefix + ": source archive is missing or changed")
                break
        if not any(row.get("visible_pages") or row.get("excerpt") for row in rows):
            errors.append(prefix + ": document has no visible full-text reading window")
    if not adopted:
        errors.append("/research_context: no usable method evidence; preserve gaps instead of declaring a researched proposal complete")
    return errors


def render_research_context(context: dict[str, Any]) -> str:
    sources = context.get("sources", [])
    used = sum(s.get("decision") == "use" for s in sources)
    lines = ["## 研究依据", "", f"本次记录 {len(sources)} 个候选，采用 {used} 篇。", "",
             "；".join(context.get("selection_principles", [])), ""]
    for source in sources:
        decision = {"use": "采用", "reject": "未采用", "defer": "待补充"}.get(source.get("decision"), "")
        lines += [f"### {source.get('title', '')}（{decision}）", "", str(source.get("url", "")), "",
                  str(source.get("reason", "")), ""]
        for key, label in (("method_summary", "原方法"), ("transfer", "用于本方案的思路"), ("limitations", "适用条件与局限")):
            if source.get(key):
                lines += [f"**{label}：** {source[key]}", ""]
    lines += ["**结束调研的理由：** " + str(context.get("stop_reason", "")), ""]
    lines.extend("- 待解决：" + str(question) for question in context.get("open_questions", []))
    return "\n".join(lines)


def focused_requirement_errors(metadata: dict[str, Any], observations: list[dict[str, Any]], requirements: dict[str, Any]) -> list[str]:
    from app.agents.idea.research import parameter_errors
    from app.agents.idea.protocol import protocol_errors
    from app.agents.idea.performance_contract import performance_errors
    errors: list[str] = []
    errors += performance_errors(metadata, requirements)
    sources = metadata.get("research_context", {}).get("sources", [])
    used = {s.get("source_id") for s in sources if s.get("decision") == "use"}
    readings = reading_sources(observations)
    if len(used) < int(requirements.get("min_sources", 0)):
        errors.append("/research_context: user-specified minimum sources not met")
    pdfs = {sid for sid in used if any(r.get("source_type") == "pdf" for r in readings.get(sid, []))}
    if len(pdfs) < int(requirements.get("min_pdfs", 0)):
        errors.append("/research_context: user-specified minimum PDFs not met")
    if requirements.get("require_parameter_budget") or "parameter_budget" in metadata or requirements.get("max_parameter_ratio"):
        errors += parameter_errors(metadata.get("parameter_budget"), max_ratio=float(requirements.get("max_parameter_ratio", 1.2)))
    if requirements.get("require_evaluation_protocol"):
        errors += protocol_errors(metadata, required=True)
    return errors


def focused_handoff(run_root: Path, proposal_text: str, project: str) -> dict[str, Any]:
    import json
    from app.harness.agent_loop.trace import digest
    from app.harness.schema.frontmatter_parser import parse
    from app.harness.schema.validator import validate_document
    metadata = parse(proposal_text).metadata
    if not validate_document(proposal_text, expected_schema="proposal.v1").valid or metadata.get("project") != project:
        raise ValueError("focused handoff requires a valid project proposal")
    profile = run_root / "input/idea_focused.v1.json"
    if not profile.is_file():
        raise ValueError("focused handoff has no original runtime profile")
    configuration = json.loads(profile.read_text())
    observations: list[dict[str, Any]] = []
    checkpoints: list[str] = []
    reviewed = False
    review_receipt: dict[str, Any] | None = None
    for path in (run_root / "agent_traces/idea").glob("*/checkpoint.json"):
        state = json.loads(path.read_text())
        observations.extend(state.get("history", []))
        checkpoints.append(str(path))
        if (state.get("candidate") == proposal_text and state.get("status") == "passed"
                and state.get("reflection_accepted") and state.get("reviewed_candidate_sha") == digest(proposal_text)):
            review_receipt = verify_review_trace(path.parent, configuration)
            reviewed = True
    errors = focused_research_errors(metadata, observations, run_root)
    if errors:
        raise ValueError("focused handoff evidence failed: " + "; ".join(errors))
    used = {s["source_id"] for s in metadata["research_context"]["sources"] if s["decision"] == "use"}
    return {"schema": "idea.research_handoff.v2", "proposal_sha256": digest(proposal_text),
            "research_context": metadata["research_context"],
            "source_readings": {key: rows for key, rows in reading_sources(observations).items() if key in used},
            "source_checkpoint_refs": checkpoints, "model_review_passed": reviewed,
            "review_receipt": review_receipt,
            "scientific_validated": False}


def verify_review_trace(trace_root: Path, configuration: dict[str, Any]) -> dict[str, Any]:
    """Require real matching provider responses, not just configured model names."""
    import json
    from app.harness.agent_loop.trace import audit_trace
    if not audit_trace(trace_root)["consistent"]:
        raise ValueError("model review trace is inconsistent")
    author = configuration["author"]["model"]
    reviewer = configuration["reviewer"]["model"]
    expected = {"act": (author["provider"], author["name"]), "reflect": (reviewer["provider"], reviewer["name"])}
    mode = validate_review_mode(expected["act"], expected["reflect"], configuration.get("review_mode", "cross_model"))
    requests: dict[int, tuple[str, str, str]] = {}
    responses: dict[str, list[int]] = {"act": [], "reflect": []}
    for line in (trace_root / "events.jsonl").read_text().splitlines():
        event = json.loads(line)
        if event["kind"] == "model_request":
            phase = event["phase"]
            if (event.get("provider"), event.get("model")) != expected.get(phase):
                raise ValueError("actual model request differs from the frozen profile")
            requests[event["request"]] = (phase, event["provider"], event["model"])
        elif event["kind"] == "model_response" and not event.get("rejected"):
            request = requests.get(event.get("request"))
            if request and (event.get("provider"), event.get("model")) == request[1:]:
                responses[request[0]].append(event["request"])
    if not all(responses.values()):
        raise ValueError("no completed generation and independent review responses")
    return {"author": author["name"], "reviewer": reviewer["name"], "completed_requests": responses,
            "trace_root": str(trace_root), "review_mode": mode,
            "cross_model": expected["act"] != expected["reflect"]}
