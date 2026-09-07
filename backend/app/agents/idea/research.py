"""Evidence extraction and safe parameter arithmetic, never proposal/recipe generation."""
from __future__ import annotations

import ast
import hashlib
import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.harness.agent_loop.trace import atomic_json


def canonical_source(url: str) -> str:
    p = urlparse(url.strip())
    host = (p.hostname or "").lower()
    path = p.path.rstrip("/")
    if host in {"arxiv.org", "export.arxiv.org"}:
        paper = re.sub(r"v[0-9]+$", "", path.removesuffix(".pdf").split("/")[-1])
        return "arxiv:" + paper
    if host == "ieeexplore.ieee.org":
        match = re.search(r"(?:document|abstract/document)/([0-9]+)", path)
        if match:
            return "ieee:" + match.group(1)
    return host + path


def title_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def evaluate_formula(formula: str, variables: dict[str, Any]) -> float:
    if len(formula) > 512:
        raise ValueError("formula too long")
    root = ast.parse(formula, mode="eval")
    if len(list(ast.walk(root))) > 128:
        raise ValueError("formula too complex")

    def visit(node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            value = float(str(node.value))
        elif isinstance(node, ast.Name) and type(variables.get(node.id)) in {int, float}:
            value = float(variables[node.id])
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                value = left + right
            elif isinstance(node.op, ast.Sub):
                value = left - right
            elif isinstance(node.op, ast.Mult):
                value = left * right
            elif isinstance(node.op, ast.Div):
                value = left / right
            elif isinstance(node.op, ast.Pow) and right.is_integer() and 0 <= right <= 12:
                value = left ** right
            else:
                raise ValueError("only +,-,*,/,bounded integer powers are allowed")
        else:
            raise ValueError("unknown variable or prohibited formula syntax")
        if not math.isfinite(value) or abs(value) > 1e12:
            raise ValueError("formula result is nonfinite or outside bound")
        return value
    try:
        return visit(root.body)
    except (ZeroDivisionError, OverflowError) as exc:
        raise ValueError("invalid arithmetic") from exc


def parameter_errors(raw: Any, *, max_ratio: float) -> list[str]:
    if not isinstance(raw, dict):
        return ["/parameter_budget: required structured object"]
    errors: list[str] = []
    if raw.get("unit") != "real_scalar":
        errors.append("/parameter_budget/unit: count all real trainable scalars; complex values count twice")
    variables = raw.get("variables")
    if not isinstance(variables, dict) or not variables:
        return errors + ["/parameter_budget/variables: nonempty numeric variable map required"]
    if any(type(v) not in {int, float} or not math.isfinite(v) for v in variables.values()):
        return errors + ["/parameter_budget/variables: only finite numeric values allowed"]
    totals: dict[str, float] = {}
    for label in ("baseline", "candidate"):
        try:
            formula = raw[label + "_formula"]
            if not isinstance(formula, str):
                raise ValueError("formula must be a string")
            computed = evaluate_formula(formula, variables)
            count = raw[label + "_parameters"]
            if type(count) is not int or computed <= 0 or not computed.is_integer() or count != computed:
                raise ValueError(f"integer count must match formula, computed {computed}")
            components = raw[label + "_components"]
            if not isinstance(components, list) or not components:
                raise ValueError("list every trainable parameter group in components")
            component_total = 0.0
            names: set[str] = set()
            for component in components:
                if not isinstance(component, dict) or not isinstance(component.get("name"), str):
                    raise ValueError("each component needs name and formula")
                if component["name"] in names:
                    raise ValueError("component names must be unique")
                names.add(component["name"])
                n = evaluate_formula(component["formula"], variables)
                if n < 0 or not n.is_integer():
                    raise ValueError("component count must be a nonnegative integer")
                component_total += n
            if component_total != computed:
                raise ValueError(f"components sum {component_total} != total {computed}")
            totals[label] = computed
        except (ValueError, KeyError, TypeError, SyntaxError) as exc:
            errors.append(f"/parameter_budget/{label}: {exc}")
    if len(totals) == 2 and totals["candidate"] / totals["baseline"] > max_ratio + 1e-12:
        errors.append(f"/parameter_budget: candidate/baseline exceeds evaluation limit {max_ratio}")
    return errors


def evidence_inventory(observations: list[dict[str, Any]]) -> dict[str, Any]:
    papers: dict[str, dict[str, Any]] = {}
    downloads: dict[str, dict[str, Any]] = {}
    reads: list[dict[str, Any]] = []
    for obs in observations:
        output = obs.get("output")
        if not obs.get("ok") or not isinstance(output, dict):
            continue
        if obs.get("tool") in {"search.arxiv_search", "search.web_search"}:
            for hit in output.get("hits", []):
                if isinstance(hit, dict) and hit.get("title") and hit.get("url"):
                    identity = canonical_source(hit["url"])
                    papers[identity] = {**hit, "identity": identity,
                                        "selection_reason": obs.get("reason", ""), "tool": obs["tool"]}
        if obs.get("tool") == "search.fetch_sources":
            for source in output.get("sources", []):
                if not source.get("ok"):
                    continue
                sha = source.get("sha256")
                path = Path(str(source.get("download_path", "")))
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                    continue
                record = {**source, "selection_reason": obs.get("reason", "")}
                downloads[str(sha)] = record
                reads.append(record)
    return {"papers": list(papers.values()), "downloads": list(downloads.values()), "reads": reads,
            "counts": {"unique_search_sources": len(papers),
                       "unique_download_contents": len(downloads),
                       "unique_pdf_contents": sum(x.get("source_type") == "pdf" for x in downloads.values()),
                       "read_windows": len(reads),
                       "network_downloads": sum(bool(x.get("network_download_performed")) for x in reads),
                       "cache_reuses": sum(bool(x.get("reused")) for x in reads)},
            "interpretation": "Search hits and visible excerpts are not proof of full reading or understanding."}


def material_errors(metadata: dict[str, Any], observations: list[dict[str, Any]], *,
                    min_sources: int, min_pdfs: int, require_budget: bool, max_ratio: float) -> list[str]:
    inventory = evidence_inventory(observations)
    papers = {p["identity"]: p for p in inventory["papers"]}
    errors: list[str] = []
    citations = metadata.get("related_literature", [])
    if not isinstance(citations, list):
        citations = []
    cited: set[str] = set()
    for index, citation in enumerate(citations):
        if not isinstance(citation, dict):
            errors.append(f"/related_literature/{index}: object required")
            continue
        identity = canonical_source(str(citation.get("url", "")))
        source = papers.get(identity)
        if source is None or title_key(str(citation.get("title", ""))) != title_key(source["title"]):
            errors.append(f"/related_literature/{index}: URL/title not matched to a real search result")
        else:
            cited.add(identity)
    if len(cited) < min_sources:
        errors.append(f"/related_literature: need {min_sources} distinct retrieved cited sources; observed {len(cited)}")
    valid_pdfs = set()
    for row in inventory["reads"]:
        identity = canonical_source(row["url"])
        source = papers.get(identity)
        download_id = canonical_source(row["download_url"])
        expected = canonical_source(str((source or {}).get("pdf_url") or (source or {}).get("url", "")))
        if source and download_id == expected and row.get("source_type") == "pdf" and row.get("visible_pages"):
            valid_pdfs.add(row["sha256"])
    if len(valid_pdfs) < min_pdfs:
        errors.append(f"/evidence: need {min_pdfs} source-matched downloaded PDFs with visible page excerpts")
    if require_budget:
        errors.extend(parameter_errors(metadata.get("parameter_budget"), max_ratio=max_ratio))
        for name in ("method_spec", "signal_contract"):
            if not isinstance(metadata.get(name), dict) or not metadata[name]:
                errors.append(f"/{name}: explicit structured definition required")
        alternatives = metadata.get("alternatives", [])
        if not isinstance(alternatives, list) or len(alternatives) < 2:
            errors.append("/alternatives: compare at least two feasible methods; justify selection")
        ablations = metadata.get("ablation_plan", [])
        if not isinstance(ablations, list) or len(ablations) < 3:
            errors.append("/ablation_plan: at least three fair comparisons with rejection criteria required")
    if not metadata.get("testable_predictions"):
        errors.append("/testable_predictions: falsifiable predictions required")
    if not metadata.get("risk_register"):
        errors.append("/risk_register: limitations and evidence gaps required")
    return errors


def write_evidence(run_root: Path, observations: list[dict[str, Any]]) -> Path:
    root = run_root / "idea" / "research"
    inventory = evidence_inventory(observations)
    atomic_json(root / "evidence_index.v1.json", inventory)
    atomic_json(root / "tool_results.v1.json", observations)
    return root / "evidence_index.v1.json"
