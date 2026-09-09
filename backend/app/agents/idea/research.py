"""Evidence extraction and safe parameter arithmetic, never proposal/recipe generation."""
from __future__ import annotations

import ast
import hashlib
import math
import re
from pathlib import Path
from typing import Any

from app.harness.agent_loop.trace import atomic_json
from app.agents.idea.protocol import protocol_errors
from app.agents.idea.source_identity import SourceIdentityIndex, search_metadata_rows
from app.agents.idea.publication_count import canonical_source as canonical_source, count_publications


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


def count_component(component: dict[str, Any], variables: dict[str, Any]) -> float:
    """Independently count an explicitly typed parameter tensor in real scalars."""
    dtype = component.get("dtype")
    if dtype not in ("real", "complex"):
        raise ValueError("component dtype must be real or complex")
    shape = component.get("shape")
    if not isinstance(shape, list) or len(shape) > 8:
        raise ValueError("component shape must be a list of up to 8 dimensions; [] denotes a scalar")
    count = 2.0 if dtype == "complex" else 1.0
    for dimension in shape:
        if type(dimension) is int and 0 < dimension <= 1e12:
            size = float(dimension)
        elif isinstance(dimension, str):
            size = evaluate_formula(dimension, variables)
        else:
            raise ValueError("shape dimensions must be positive integer values or arithmetic strings")
        if size <= 0 or not size.is_integer():
            raise ValueError("shape dimensions must evaluate to positive integers")
        count *= size
        if count > 1e12:
            raise ValueError("component size outside bound")
    return count


def parameter_errors(raw: Any, *, max_ratio: float) -> list[str]:
    if not math.isfinite(max_ratio) or max_ratio <= 0:
        return ["/parameter_budget: evaluation max_ratio must be finite and positive"]
    if not isinstance(raw, dict):
        return ["/parameter_budget: required structured object"]
    errors: list[str] = []
    if raw.get("unit") != "real_scalar":
        errors.append("/parameter_budget/unit: count all real trainable scalars; complex values count twice")
    variables = raw.get("variables")
    if not isinstance(variables, dict) or not variables:
        return errors + ["/parameter_budget/variables: nonempty numeric variable map required"]
    if any(type(v) not in {int, float} or abs(v) > 1e12 or not math.isfinite(v) for v in variables.values()):
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
                shape_count = count_component(component, variables)
                if n != shape_count:
                    raise ValueError(f"component {component['name']!r}: formula gives {n} real scalars, "
                                     f"but dtype/shape gives {shape_count}")
                component_total += n
            if component_total != computed:
                raise ValueError(f"components sum {component_total} != total {computed}")
            totals[label] = computed
        except (ValueError, KeyError, TypeError, SyntaxError) as exc:
            errors.append(f"/parameter_budget/{label}: {exc}")
    if len(totals) == 2 and totals["candidate"] / totals["baseline"] > max_ratio + 1e-12:
        errors.append(f"/parameter_budget: candidate/baseline exceeds evaluation limit {max_ratio}")
    if "evaluation_cases" in raw:
        errors.extend(_parameter_case_errors(raw, max_ratio=max_ratio))
    return errors


def _parameter_case_errors(raw: dict[str, Any], *, max_ratio: float) -> list[str]:
    """Check shared or explicitly overridden candidate ledgers against the limit.

    No free-text dimensions are inferred and no passing cases are generated.
    Historical ledgers without evaluation_cases retain their original contract.
    """
    cases = raw["evaluation_cases"]
    prefix = "/parameter_budget/evaluation_cases"
    if not isinstance(cases, list) or not 1 <= len(cases) <= 32:
        return [prefix + ": declare 1..32 configurations, including the primary variables"]
    errors: list[str] = []
    names: set[str] = set()
    includes_primary = False
    base_fields = {"name", "variables", "baseline_parameters", "candidate_parameters"}
    override_fields = {"candidate_formula", "candidate_components"}
    for index, case in enumerate(cases):
        path = f"{prefix}/{index}"
        if not isinstance(case, dict) or set(case) not in (base_fields, base_fields | override_fields):
            errors.append(path + ": require name, variables, baseline_parameters, candidate_parameters; "
                          "a different candidate architecture must supply both candidate_formula and candidate_components")
            continue
        name = case["name"]
        if not isinstance(name, str) or not name.strip() or len(name) > 120 or name in names:
            errors.append(path + "/name: use a nonempty unique configuration name (max 120 characters)")
        else:
            names.add(name)
        variables = case["variables"]
        overridden = override_fields <= set(case)
        if (not isinstance(variables, dict) or not set(raw["variables"]) <= set(variables)
                or (not overridden and set(variables) != set(raw["variables"]))):
            errors.append(path + "/variables: explicitly assign all primary variable names; "
                          "additional variables require a complete candidate formula/components override")
            continue
        same_candidate = (not overridden or all(case[key] == raw.get(key) for key in override_fields))
        includes_primary = includes_primary or (same_candidate and variables == raw["variables"])
        ledger = {key: value for key, value in raw.items() if key != "evaluation_cases"}
        ledger.update({key: case[key] for key in ("variables", "baseline_parameters", "candidate_parameters")})
        if overridden:
            ledger.update({key: case[key] for key in override_fields})
        errors.extend(path + error.removeprefix("/parameter_budget")
                      for error in parameter_errors(ledger, max_ratio=max_ratio))
    if not includes_primary:
        errors.append(prefix + ": primary variables must be included explicitly with the unchanged "
                      "primary candidate formula/components (inherited or exactly repeated); a different candidate ledger "
                      "cannot replace the primary configuration")
    return errors


def evidence_inventory(observations: list[dict[str, Any]]) -> dict[str, Any]:
    papers: dict[str, dict[str, Any]] = {}
    downloads: dict[str, dict[str, Any]] = {}
    reads: list[dict[str, Any]] = []
    for obs in observations:
        output = obs.get("output")
        if not obs.get("ok") or not isinstance(output, dict):
            continue
        for hit in search_metadata_rows(obs):
            if hit.get("title") and hit.get("url"):
                identity = canonical_source(hit["url"])
                previous_titles = papers.get(identity, {}).get("observed_titles", [])
                titles = list(dict.fromkeys([*previous_titles, hit["title"]]))
                papers[identity] = {**hit, "identity": identity, "observed_titles": titles,
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
    identities = SourceIdentityIndex(observations)
    errors: list[str] = protocol_errors(metadata)
    debate = metadata.get("debate_summary")
    if isinstance(debate, dict) and debate.get("rounds", 0) != 0:
        errors.append("/debate_summary/rounds: this research loop has no debate tool receipts; comparisons are not executed debate rounds")
    citations = metadata.get("related_literature", [])
    if not isinstance(citations, list):
        citations = []
    cited_urls: set[str] = set()
    cited_metadata: list[dict[str, Any]] = []
    for index, citation in enumerate(citations):
        if not isinstance(citation, dict):
            errors.append(f"/related_literature/{index}: object required")
            continue
        matches = identities.matching_hits(str(citation.get("url", "")))
        if title_key(str(citation.get("title", ""))) not in {title_key(source["title"]) for source in matches}:
            errors.append(f"/related_literature/{index}: URL/title not matched to a real search result at the declared document version")
        else:
            cited_urls.add(str(citation["url"]))
            cited_metadata.extend({"url": citation["url"], "title": hit["title"]} for hit in matches
                                  if title_key(str(citation.get("title", ""))) == title_key(hit["title"]))
    publications = count_publications(cited_metadata)
    if publications.count < min_sources:
        errors.append(f"/related_literature: need {min_sources} distinct retrieved cited sources; observed {publications.count}"
                      + publications.diagnostic())
    valid_pdfs = set()
    for row in inventory["reads"]:
        cited_read = any(identities.matching_hits(url, read_receipt=str(row.get("read_receipt", "")))
                         for url in cited_urls)
        if cited_read and row.get("source_type") == "pdf" and row.get("visible_pages"):
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
        else:
            feasible_count = 0
            budget = metadata.get("parameter_budget", {})
            variables = budget.get("variables", {}) if isinstance(budget, dict) else {}
            if not isinstance(variables, dict):
                variables = {}
            baseline_count = budget.get("baseline_parameters", 0) if isinstance(budget, dict) else 0
            for i, option in enumerate(alternatives):
                try:
                    if not isinstance(option, dict) or type(option.get("feasible")) is not bool:
                        raise ValueError("feasible boolean required")
                    groups = option.get("components")
                    if not isinstance(groups, list) or not groups or not all(isinstance(g, dict) for g in groups):
                        raise ValueError("typed trainable components required")
                    computed = sum(count_component(g, variables) for g in groups)
                    if any(evaluate_formula(g["formula"], variables) != count_component(g, variables) for g in groups):
                        raise ValueError("component formula does not match dtype/shape count")
                    if type(option.get("parameters")) is not int or option["parameters"] != computed:
                        raise ValueError(f"parameters must equal typed component sum {computed}")
                    if option["feasible"]:
                        if type(baseline_count) is not int or baseline_count <= 0 or computed > max_ratio * baseline_count + 1e-12:
                            raise ValueError("feasible alternative exceeds the same real-scalar baseline budget")
                        feasible_count += 1
                except (ValueError, TypeError, KeyError, SyntaxError) as exc:
                    errors.append(f"/alternatives/{i}: {exc}")
            if feasible_count < 2:
                errors.append("/alternatives: at least two typed, within-budget feasible methods required")
        if not isinstance(metadata.get("decision_rule"), dict) or not metadata["decision_rule"]:
            errors.append("/decision_rule: define one signed metric comparison and exhaustive statistical decision rule")
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
