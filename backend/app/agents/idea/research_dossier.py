"""Receipt-bound literature findings; provenance validation is not scientific review."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator

from app.agents.idea.research import canonical_source, title_key
from app.agents.idea.publication_count import PUBLICATION_COUNT_CONTRACT, count_publications
from app.agents.idea.source_identity import SourceIdentityIndex
from app.harness.agent_loop.trace import atomic_json
from app.harness.schema.validator import SCHEMAS_DIR


def dossier_schema() -> dict[str, Any]:
    value: dict[str, Any] = json.loads((SCHEMAS_DIR / "research_report.v1.json").read_text())
    return value


def _normalized_page(value: str) -> tuple[str, frozenset[int], frozenset[int]]:
    """Preserve legacy text while mapping eligible real line-end hyphens into it."""
    normalized = unicodedata.normalize("NFKC", value)
    parts: list[str] = []
    optional: set[int] = set()
    paragraph_boundaries: set[int] = set()
    previous, previous_end, length = "", 0, 0
    for token in re.finditer(r"\S+", normalized):
        word = token.group()
        whitespace = normalized[previous_end:token.start()]
        join_hyphen = bool(re.search(r"[A-Za-z]-$", previous) and re.match(r"[A-Za-z]", word))
        if previous and not join_hyphen:
            parts.append(" ")
            length += 1
        if previous and re.search(r"\n[ \t]*\n", whitespace.replace("\r\n", "\n").replace("\r", "\n")):
            paragraph_boundaries.add(length)
        if (join_hyphen and re.search(r"[a-z]{2}-$", previous) and re.match(r"[a-z]{2}", word)
                and re.fullmatch(r"[ \t]*(?:\r\n|[\r\n])[ \t]*", whitespace)):
            # Only the actual page can authorize an omission. Ordinary inline
            # hyphens, blank lines, single-letter symbols and digits never do.
            optional.add(length - 1)
        parts.append(word)
        length += len(word)
        previous, previous_end = word, token.end()
    return "".join(parts), frozenset(optional), frozenset(paragraph_boundaries)


def normalized_excerpt_text(value: str) -> str:
    """Normalize PDF typography without deleting hyphens or inventing text."""
    return _normalized_page(value)[0]


@dataclass(frozen=True)
class QuoteMatch:
    start: int
    end: int
    normalization_mode: Literal["exact_normalized", "pdf_line_end_hyphen"]
    omitted_hyphen_offsets: tuple[int, ...] = ()


def locate_quote(quote: str, page_text: str) -> QuoteMatch | None:
    """Match one continuous page span, optionally omitting verified line-end hyphens.

    All offsets refer to the original normalized page, which retains its hyphens.
    This is a typographic match, not verification of a finding or a formula.
    """
    needle = normalized_excerpt_text(quote)
    text, optional, paragraph_boundaries = _normalized_page(page_text)
    if not needle:
        return None
    start = text.find(needle)
    if start >= 0:
        return QuoteMatch(start, start + len(needle), "exact_normalized")
    if not optional:
        return None
    start = text.find(needle[0])
    while start >= 0:
        position, matched = start, 0
        omitted: list[int] = []
        while position < len(text) and matched < len(needle):
            if position != start and position in paragraph_boundaries:
                break
            if text[position] == needle[matched]:
                position += 1
                matched += 1
            elif position in optional:
                omitted.append(position)
                position += 1
            else:
                break
        if matched == len(needle):
            return QuoteMatch(start, position, "pdf_line_end_hyphen", tuple(omitted))
        start = text.find(needle[0], start + 1)
    return None


def _quote_location_excerpt(quote: str, page_texts: list[str]) -> str:
    """Locate at most 300 literal normalized page characters; infer no finding."""
    needle = normalized_excerpt_text(quote)
    best_text, best_start, best_size = "", 0, -1
    for raw_text in page_texts:
        text = normalized_excerpt_text(raw_text)
        if not text:
            continue
        match = SequenceMatcher(None, needle, text, autojunk=False).find_longest_match()
        if match.size > best_size:
            best_text, best_start, best_size = text, match.b, match.size
    if not best_text:
        return ""
    # Keep surrounding context without joining disjoint matches or page windows.
    start = max(0, best_start - max(0, (300 - best_size) // 2))
    start = min(start, max(0, len(best_text) - 300))
    return best_text[start:start + 300]


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
        quote = insight["quote"]
        if not any(locate_quote(quote, str(page.get("text", ""))) is not None for page in pages):
            error = (f"quote is absent from visible page {insight['page']}; copy a short contiguous excerpt "
                     "from that page's actual visible text, preserving words and hyphens")
            excerpt = _quote_location_excerpt(quote, [str(page.get("text", "")) for page in pages])
            if excerpt:
                error += (". Untrusted locator only; does not establish support for interpretations; "
                          "candidate remains invalid. visible_page_excerpt="
                          + json.dumps(excerpt, ensure_ascii=False))
            return error
        return None
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return f"read_receipt or archived document is unreadable: {type(exc).__name__}"


def dossier_errors(metadata: dict[str, Any], observations: list[dict[str, Any]], *,
                   min_sources: int = 1,
                   publication_count_contract: str | None = PUBLICATION_COUNT_CONTRACT) -> list[str]:
    # None is only for rereading historical manifests; live validation defaults
    # to the current contract and cannot take this flag from author metadata.
    if publication_count_contract not in (None, PUBLICATION_COUNT_CONTRACT):
        return ["/sources: unknown publication count contract"]
    errors = [f"/{'/'.join(str(p) for p in error.absolute_path)}: {error.message}"
              for error in Draft202012Validator(dossier_schema()).iter_errors(metadata)]
    if errors:
        return errors
    if type(min_sources) is not int or min_sources < 0:
        return ["/sources: min_sources must be a nonnegative integer"]
    documents = SourceIdentityIndex(observations)
    receipts: dict[str, dict[str, Any]] = {}
    for observation in observations:
        output = observation.get("output")
        if not observation.get("ok") or not isinstance(output, dict):
            continue
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
        if title_key(source["title"]) not in {title_key(hit["title"]) for hit in documents.matching_hits(source["url"])}:
            errors.append(prefix + ": title/URL must match a retrieved search result at the declared document version")
        if not set(source["gap_ids"]) <= gaps:
            errors.append(prefix + "/gap_ids: unknown research gap")
    read_sources: set[str] = set()
    read_metadata: list[dict[str, Any]] = []
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
        matches = documents.matching_hits(source["url"], read_receipt=insight["read_receipt"])
        if title_key(source["title"]) not in {title_key(hit["title"]) for hit in matches}:
            errors.append(prefix + ": read receipt download URL does not match the retrieved source PDF at the declared document version; use the observed version and its receipt")
            continue
        receipt_error = _receipt_error(insight, row)
        if receipt_error:
            errors.append(prefix + ": " + receipt_error)
            continue
        read_sources.add(canonical_source(source["url"]))
        read_metadata.extend({"url": source["url"], "title": hit["title"]} for hit in matches
                             if title_key(source["title"]) == title_key(hit["title"]))
    for source in sources.values():
        if source["decision"] == "use" and canonical_source(source["url"]) not in read_sources:
            errors.append(f"/sources/{source['source_id']}: used source requires a verified extracted insight")
    publications = count_publications(read_metadata)
    count = publications.count if publication_count_contract is not None else len(read_sources)
    if count < min_sources:
        errors.append(f"/sources: require {min_sources} distinct read publications; observed {count}"
                      + (publications.diagnostic() if publication_count_contract is not None else ""))
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
