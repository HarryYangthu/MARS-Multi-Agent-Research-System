"""Configurable literature coverage backed by archived tool observations."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
import re

from jsonschema import Draft202012Validator

from app.agents.idea.focused_research import reading_sources
from app.harness.agent_loop.context import reading_coverage_index


def publication_key(url: str) -> str:
    """Collapse arXiv abstract/PDF/version aliases without conflating titles."""
    parsed = urlsplit(url)
    if parsed.hostname in {"arxiv.org", "export.arxiv.org"}:
        identifier = re.sub(r"^/(abs|pdf|html)/", "", parsed.path).removesuffix(".pdf")
        return "arxiv:" + re.sub(r"v\d+$", "", identifier)
    return (parsed.hostname or "").lower() + parsed.path.rstrip("/")


def discovered_sources(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for observation in observations:
        if not observation.get("ok") or observation.get("tool") not in {
                "search.arxiv_search", "search.openalex_search", "search.cvf_search",
                "search.neurips_search", "search.web_search"}:
            continue
        output = observation.get("output", {})
        if not isinstance(output, dict):
            continue
        hits = output.get("hits", output.get("results", output.get("papers", [])))
        if not isinstance(hits, list):
            continue
        for row in hits:
            if not isinstance(row, dict) or not row.get("title"):
                continue
            url = str(row.get("pdf_url") or row.get("url") or "")
            if not url.startswith(("https://", "http://")):
                continue
            found.setdefault(publication_key(url), {"title": row["title"], "url": url,
                "source": observation["tool"], "summary": row.get("summary", "")})
    return list(found.values())


def quality_policy(settings: dict[str, Any], requirements: dict[str, Any]) -> dict[str, Any]:
    policy = dict(settings)
    for key in ("min_candidates", "min_read_papers", "min_method_directions"):
        if key in requirements:
            policy[key] = requirements[key]
        if type(policy.get(key)) is not int or not 0 <= policy[key] <= 100:
            raise ValueError(f"research_quality.{key} must be an integer in [0,100]")
    axes = policy.get("coverage_axes")
    if not isinstance(axes, dict) or not axes or any(not isinstance(k, str) or not isinstance(v, str) for k, v in axes.items()):
        raise ValueError("research_quality.coverage_axes must contain named task coverage axes")
    return policy


def quality_schema(policy: dict[str, Any]) -> dict[str, Any]:
    text = {"type": "string", "minLength": 1}
    refs = {"type": "array", "minItems": 1, "uniqueItems": True, "items": text}
    return {"type": "object", "required": ["coverage", "method_comparison", "stop_status"], "properties": {
        "stop_status": {"enum": ["complete", "evidence_gap"]},
        "coverage": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "required": ["axis", "finding", "source_ids", "remaining_gap"], "properties": {
                "axis": {"enum": list(policy["coverage_axes"])}, "finding": text,
                "source_ids": refs, "remaining_gap": {"type": "string"}}}},
        "method_comparison": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "required": ["direction", "source_ids", "mechanism", "compatibility", "tradeoff", "decision"],
            "properties": {"direction": text, "source_ids": refs, "mechanism": text,
                "compatibility": text, "tradeoff": text, "decision": text}}}}}


def verified_method_sources(context: dict[str, Any], observations: list[dict[str, Any]], root: Path) -> set[str]:
    """Reading a rejected method also counts, but a download/abstract never does."""
    readings = reading_sources(observations)
    complete = {doc["source_id"]: {page["page"] for page in doc["pages"] if page["complete_extracted_text"]}
                for doc in reading_coverage_index(observations)}
    verified: set[str] = set()
    publications: set[str] = set()
    for source in context.get("sources", []):
        sid = source.get("source_id")
        rows = readings.get(sid, [])
        if not rows or not all(source.get(key) for key in ("method_summary", "method_sections", "limitations")):
            continue
        if source.get("url") not in {str(row.get(key) or "") for row in rows for key in ("url", "download_url", "final_url", "pdf_url")}:
            continue
        if any(row.get("source_type") == "pdf" for row in rows):
            pages = source.get("method_pages", [])
            if not pages or not set(pages).issubset(complete.get(sid, set())):
                continue
        else:
            windows = sorted((int(row.get("char_start", 0)), int(row.get("char_end", 0)))
                             for row in rows if row.get("excerpt"))
            end = 0
            for start, stop in windows:
                if start > end:
                    break
                end = max(end, stop)
            total = max((int(row.get("full_text_chars", 0)) for row in rows), default=0)
            if not total or end < total:
                continue
        paths = [Path(str(row.get("download_path", ""))) for row in rows]
        if any(not path.resolve().is_relative_to(root.resolve()) or not path.is_file()
               or hashlib.sha256(path.read_bytes()).hexdigest() != row.get("sha256") for path, row in zip(paths, rows)):
            continue
        identity = publication_key(str(rows[0].get("download_url") or rows[0].get("url") or source["url"]))
        if identity in publications:
            continue
        publications.add(identity)
        verified.add(sid)
    return verified


def quality_errors(metadata: dict[str, Any], observations: list[dict[str, Any]], root: Path,
                   policy: dict[str, Any]) -> list[str]:
    context = metadata.get("research_context", {})
    errors = ["/research_context/" + "/".join(map(str, error.absolute_path)) + ": " + error.message
              for error in Draft202012Validator(quality_schema(policy)).iter_errors(context)]
    if errors:
        return errors
    candidates = discovered_sources(observations)
    verified = verified_method_sources(context, observations, root)
    if len(candidates) < policy["min_candidates"]:
        errors.append(f"/research_context: 调研未完成，实际候选 {len(candidates)} < {policy['min_candidates']}")
    if len(verified) < policy["min_read_papers"]:
        errors.append(f"/research_context: 调研未完成，完整方法阅读 {len(verified)} < {policy['min_read_papers']}；未采用但完整阅读的方法也可计入")
    comparisons = context["method_comparison"]
    directions = {row["direction"].strip().casefold() for row in comparisons}
    if len(directions) < policy["min_method_directions"]:
        errors.append("/research_context/method_comparison: 不同方法方向的比较不足")
    seen_axes: set[str] = set()
    for field in ("coverage", "method_comparison"):
        for index, row in enumerate(context[field]):
            if not set(row["source_ids"]).issubset(verified):
                errors.append(f"/research_context/{field}/{index}/source_ids: 只能引用有完整方法阅读凭据的来源")
            if field == "coverage":
                if row["axis"] in seen_axes:
                    errors.append(f"/research_context/coverage/{index}: 重复覆盖项")
                seen_axes.add(row["axis"])
                # Whether an uncertainty blocks this proposal is a semantic
                # review decision. Pending experiments must not become a demand
                # for measured success at the literature stage.
    if seen_axes != set(policy["coverage_axes"]):
        errors.append("/research_context/coverage: 未覆盖所有任务维度 " + ", ".join(sorted(set(policy["coverage_axes"]) - seen_axes)))
    if len({sid for row in comparisons for sid in row["source_ids"]}) < min(policy["min_method_directions"], policy["min_read_papers"]):
        errors.append("/research_context/method_comparison: 不同方向不能仅把同一篇论文换名称重复计数")
    if context["stop_status"] != "complete":
        errors.append("/research_context/stop_status: 证据不足，需保留缺口后停止")
    return errors
