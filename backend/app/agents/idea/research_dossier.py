"""Receipt-bound literature findings; provenance validation is not scientific review."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from app.agents.idea.research import canonical_source, title_key
from app.harness.agent_loop.trace import atomic_json
from app.harness.schema.validator import SCHEMAS_DIR


def dossier_schema() -> dict[str, Any]:
    value: dict[str, Any] = json.loads((SCHEMAS_DIR / "research_report.v1.json").read_text())
    return value


def normalized_excerpt_text(value: str) -> str:
    """Normalize PDF typography without deleting hyphens or inventing text."""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"(?<=[A-Za-z])-\s+(?=[A-Za-z])", "-", normalized)
    return " ".join(normalized.split())


def _receipt_error(insight: dict[str, Any], row: dict[str, Any]) -> str | None:
    """Diagnose the exact provenance failure; never replace the model's quote."""
    try:
        receipt = json.loads(Path(insight["read_receipt"]).read_text())
        if not isinstance(receipt, dict):
            return "read_receipt must contain an object"
        for field in ("sha256", "download_path", "visible_pages", "url", "download_url"):
            if receipt.get(field) != row.get(field):
                return f"read_receipt {field} differs from the actual tool observation"
        if not receipt.get("ok") or receipt.get("source_type") != "pdf":
            return "read_receipt is not a successful PDF read"
        document = Path(receipt["download_path"]).read_bytes()
        if hashlib.sha256(document).hexdigest() != insight["document_sha256"]:
            return "document_sha256 does not match the archived document bytes"
        if receipt["sha256"] != insight["document_sha256"]:
            return "document_sha256 does not match the actual read receipt"
        pages = [page for page in receipt.get("visible_pages", []) if page.get("page") == insight["page"]]
        if not pages:
            return f"page {insight['page']} is not visible in this read_receipt; read that page or cite a visible page"
        quote = normalized_excerpt_text(insight["quote"])
        if not any(quote in normalized_excerpt_text(str(page.get("text", ""))) for page in pages):
            return (f"quote is absent from visible page {insight['page']}; copy a short contiguous excerpt "
                    "from that page's actual visible text, preserving words and hyphens")
        return None
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return f"read_receipt or archived document is unreadable: {type(exc).__name__}"


def dossier_errors(metadata: dict[str, Any], observations: list[dict[str, Any]], *,
                   min_sources: int = 1) -> list[str]:
    errors = [f"/{'/'.join(str(p) for p in error.absolute_path)}: {error.message}"
              for error in Draft202012Validator(dossier_schema()).iter_errors(metadata)]
    if errors:
        return errors
    if type(min_sources) is not int or min_sources < 0:
        return ["/sources: min_sources must be a nonnegative integer"]
    hits: dict[str, set[str]] = {}
    download_urls: dict[str, set[str]] = {}
    receipts: dict[str, dict[str, Any]] = {}
    for observation in observations:
        output = observation.get("output")
        if not observation.get("ok") or not isinstance(output, dict):
            continue
        if observation.get("tool") in {"search.arxiv_search", "search.web_search", "search.openalex_search"}:
            for hit in output.get("hits", []):
                if isinstance(hit, dict) and isinstance(hit.get("url"), str) and isinstance(hit.get("title"), str):
                    identity = canonical_source(hit["url"])
                    hits.setdefault(identity, set()).add(title_key(hit["title"]))
                    download_urls.setdefault(identity, set()).add(canonical_source(str(hit.get("pdf_url") or hit["url"])))
        if observation.get("tool") == "search.fetch_sources":
            for row in output.get("sources", []):
                if isinstance(row, dict) and row.get("ok") and isinstance(row.get("read_receipt"), str):
                    receipts[row["read_receipt"]] = row
    gaps = {gap["id"] for gap in metadata["gaps"]}
    if len(gaps) != len(metadata["gaps"]):
        errors.append("/gaps: duplicate id")
    sources: dict[str, dict[str, Any]] = {}
    identities: set[str] = set()
    for index, source in enumerate(metadata["sources"]):
        prefix = f"/sources/{index}"
        identity = canonical_source(source["url"])
        if source["source_id"] in sources or identity in identities:
            errors.append(prefix + ": duplicate source id or publication")
        sources[source["source_id"]] = source
        identities.add(identity)
        if title_key(source["title"]) not in hits.get(identity, set()):
            errors.append(prefix + ": title/URL must match a retrieved search result")
        if not set(source["gap_ids"]) <= gaps:
            errors.append(prefix + "/gap_ids: unknown research gap")
    read_sources: set[str] = set()
    insight_ids: set[str] = set()
    for index, insight in enumerate(metadata["insights"]):
        prefix = f"/insights/{index}"
        if insight["id"] in insight_ids:
            errors.append(prefix + ": duplicate insight id")
        insight_ids.add(insight["id"])
        source = sources.get(insight["source_id"])
        if source is None or source["decision"] != "use":
            errors.append(prefix + "/source_id: insight must refer to a used source")
            continue
        row = receipts.get(insight["read_receipt"])
        if row is None:
            errors.append(prefix + "/read_receipt: receipt is not in actual tool observations; use an actual tool read receipt")
            continue
        if canonical_source(str(row.get("url", ""))) != canonical_source(source["url"]):
            errors.append(prefix + "/source_id: read receipt belongs to a different source URL")
            continue
        if canonical_source(str(row.get("download_url", ""))) not in download_urls.get(canonical_source(source["url"]), set()):
            errors.append(prefix + ": read receipt download URL does not match the retrieved source PDF")
            continue
        receipt_error = _receipt_error(insight, row)
        if receipt_error:
            errors.append(prefix + ": " + receipt_error)
            continue
        read_sources.add(canonical_source(source["url"]))
    for source in sources.values():
        if source["decision"] == "use" and canonical_source(source["url"]) not in read_sources:
            errors.append(f"/sources/{source['source_id']}: used source requires a verified extracted insight")
    if len(read_sources) < min_sources:
        errors.append(f"/sources: require {min_sources} distinct read publications; observed {len(read_sources)}")
    return errors


def write_dossier_report(run_root: Path, metadata: dict[str, Any],
                         observations: list[dict[str, Any]]) -> Path:
    errors = dossier_errors(metadata, observations)
    root = run_root / "idea" / "research"
    atomic_json(root / "research_report.v1.json", {"metadata": metadata, "validation_errors": errors,
                "provenance_valid": not errors, "scientific_validated": False})
    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")
    lines = [str(metadata.get("human_summary", "")), "",
             "选文原则：" + "；".join(metadata.get("selection_principles", [])), "",
             "研究问题：" + "；".join(gap["question"] for gap in metadata.get("gaps", [])), "",
             "| 文章 | 选择 | 为什么选或暂缓 | 提取与迁移 |",
             "|---|---|---|---|"]
    for source in metadata.get("sources", []):
        findings = [f"第 {item['page']} 页：{item['paper_finding']}；迁移：{item['transfer_idea']}；限制：{'；'.join(item['limitations'])}"
                    for item in metadata.get("insights", []) if item["source_id"] == source["source_id"]]
        lines.append("| " + " | ".join(cell(x) for x in (source["title"], source["decision"],
                     source["selection_reason"], "；".join(findings))) + " |")
    lines += ["", "来源凭据通过不等于论文理解正确或方法已验证。", ""]
    target = root / "research_report.v1.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target
