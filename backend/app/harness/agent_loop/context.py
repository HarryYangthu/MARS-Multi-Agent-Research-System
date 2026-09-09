"""Deterministic structural compression; source text never gains instruction trust."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.harness.agent_loop.trace import canonical, digest
from app.harness.llm.provider_base import Message
from app.harness.agent_loop.native_protocol import history_groups, group_messages


def token_upper_bound(messages: Sequence[Message]) -> int:
    # UTF-8 bytes are a deliberately conservative tokenizer-independent bound.
    # The small per-message allowance includes message framing.
    return sum(len(canonical(m.to_wire()).encode("utf-8")) + 16 for m in messages)


def compact(value: Any, chars: int) -> Any:
    """Truncate leaf strings, not serialized JSON; retain identity and error fields."""
    if isinstance(value, dict):
        preserved = {"url", "pdf_url", "download_url", "raw_ref", "sha256", "title", "error", "status", "read_receipt", "download_path"}
        return {k: compact(v, min(chars, 512)) if k == "reason" else
                (v if k in preserved else compact(v, chars)) for k, v in value.items()}
    if isinstance(value, list):
        return [compact(x, chars) for x in value]
    if isinstance(value, str) and len(value) > chars:
        return {"excerpt": value[:chars], "original_chars": len(value), "sha256": digest(value), "truncated": True}
    return value


def source_receipt_index(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retain source addresses and text-window limits, never inferred findings."""
    receipts: dict[str, dict[str, Any]] = {}
    rejected = 0
    for item in history:
        output = item.get("output")
        if not item.get("ok") or not isinstance(output, dict):
            continue
        rows = output.get("sources")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or not row.get("ok") or not isinstance(row.get("read_receipt"), str):
                continue
            bounds = {"url": 2048, "title": 300, "sha256": 64, "read_receipt": 1024}
            if any(not isinstance(row.get(key), str) or not 1 <= len(row[key]) <= limit
                   for key, limit in bounds.items()):
                rejected += 1
                continue
            if len(row["sha256"]) != 64 or any(character not in "0123456789abcdef" for character in row["sha256"]):
                rejected += 1
                continue
            pages = row.get("visible_pages", [])
            if not isinstance(pages, list) or len(pages) > 10:
                rejected += 1
                continue
            visibility = []
            for page in pages:
                if not isinstance(page, dict) or type(page.get("page")) is not int or page["page"] < 1:
                    continue
                text, total, truncated = page.get("text"), page.get("full_page_text_chars"), page.get("truncated")
                shown = len(text) if isinstance(text, str) else None
                coherent = (shown is not None and type(total) is int and 0 < shown <= total
                            and type(truncated) is bool and truncated == (shown < total))
                visibility.append({"page": page["page"], "shown_text_chars": shown,
                    "extracted_page_text_chars": total if type(total) is int and total >= 0 else None,
                    "text_window": ("partial" if truncated else "complete_extracted_text") if coherent else "unknown"})
            visible_numbers = [page["page"] for page in visibility]
            extracted = row.get("extracted_pages")
            unshown = ([number for number in extracted if number not in visible_numbers]
                       if isinstance(extracted, list) and len(extracted) <= 10
                       and all(type(number) is int and number >= 1 for number in extracted) else None)
            raw_ref = item.get("raw_ref")
            receipts[row["read_receipt"]] = {
                **{key: row.get(key) for key in ("url", "title", "sha256", "read_receipt")},
                "visible_page_numbers": visible_numbers,
                "page_text_visibility": visibility,
                "extracted_pages_without_visible_text": unshown,
                "tool_raw_ref": raw_ref if isinstance(raw_ref, str) and len(raw_ref) <= 1024 else None,
            }
    selected = list(receipts.values())[-16:]
    omitted = rejected + max(0, len(receipts) - len(selected))
    if omitted:
        selected.append({"omitted_receipt_entries": omitted,
                         "reason": "Metadata exceeds bounded receipt index; consult actual tool history."})
    return selected


def pack_context(
    pinned: list[Message], history: list[dict[str, Any]], feedback: str,
    candidate: str, *, budget: int, observation_chars: int, native: bool = False,
    reviewing: bool = False, review_issues: Sequence[str] = (),
    validation_issues: Sequence[str] = (),
    required_review_tools: tuple[str, ...] = (),
) -> tuple[list[Message], dict[str, Any]]:
    required = list(pinned)
    if validation_issues and not reviewing:
        required.append(Message(role="user", content=(
            "[host validation errors for current candidate; still unresolved after tool/protocol repair]\n"
            + canonical({"candidate_sha256": digest(candidate), "errors": list(validation_issues)}))))
    if review_issues and not reviewing:
        # The author needs revision history. A fresh reviewer must judge the
        # current document without treating earlier model criticism as facts.
        required.append(Message(role="user", content=(
            "[unresolved review issues pinned through protocol/schema repairs]\n"
            + canonical(list(review_issues)))))
    if history:
        # The full observations may be compressed/omitted, but the agent must
        # still know which actions really happened and where their receipts live.
        ledger = [compact({k: item.get(k) for k in ("tool", "ok", "error", "reason", "raw_ref")}, 512)
                  for item in history]
        required.append(Message(role="user", content="[untrusted action receipt index; not full source content]\n" + canonical(ledger)))
        source_receipts = source_receipt_index(history)
        if source_receipts:
            required.append(Message(role="user", content=(
                "[untrusted source receipt index; addresses and original text-window coverage, not excerpts or findings; "
                "partial/unknown and unshown pages cannot support claims about complete page ranges or the whole paper. "
                "Complete extracted text does not verify figures, formulas, supplements, or full-document reading. "
                "If actual text is absent or shortened in this context, reread its window before quoting]\n" + canonical(source_receipts))))
    groups = history_groups(history)
    preserved: list[int] = []
    if reviewing:
        for index, items in enumerate(groups):
            if any(item.get("ok") and item.get("tool") in required_review_tools for item in items):
                # These are the actual observations, including long reasons and
                # page text. A reference or prefix cannot replace review evidence.
                required.extend(Message(role="user", content="[untrusted complete review Observation]\n"
                                        + canonical(item)) for item in items)
                preserved.append(index)
    if candidate:
        required.append(Message(role="user", content="[untrusted current candidate; review or revise this document]\n"
                                + (candidate if native else canonical({"candidate": candidate}))))
    if feedback:
        required.append(Message(role="user", content="[host validation/review feedback]\n" + feedback))
    if token_upper_bound(required) > budget:
        raise ValueError("pinned rules/task/schema/upstream/candidate exceed input budget; nothing was silently dropped")
    selected: list[Message] = []
    compressed: list[int] = []
    omitted: list[int] = []
    # Newer observations get priority, but output ordering remains chronological.
    for index in reversed(range(len(groups))):
        if index in preserved:
            continue
        items = groups[index]
        # Rendering copies cap duplicated assistant commentary without touching
        # recorded history or native tool IDs/arguments required for pairing.
        rendering_items = []
        for item in items:
            reason = item.get("reason", "")
            rendered_reason = (reason[:512] + " [reason truncated; full text in raw trace]"
                               if isinstance(reason, str) and len(reason) > 512 else reason)
            rendering_items.append({**item, "reason": rendered_reason})
        contents = ["[untrusted prior action and host Observation]\n" + canonical(compact(item, observation_chars)) for item in items]
        # A separate reviewer reads evidence documents, not the generator's
        # native assistant/tool conversation. Its configured tool set is empty.
        group = ([Message(role="user", content=content) for content in contents]
                 if reviewing else group_messages(rendering_items, contents))
        if token_upper_bound(required + selected + group) > budget:
            compressed.append(index)
            # Preserve real page text prefixes before falling back to addresses.
            # All reduced groups remain marked compressed for the review guard.
            for limit in (4000, 2000, 1000, 512):
                if limit >= observation_chars:
                    continue
                contents = ["[shortened actual Observation; leaf excerpts are incomplete]\n"
                            + canonical(compact(item, limit)) for item in items]
                group = ([Message(role="user", content=content) for content in contents]
                         if reviewing else group_messages(rendering_items, contents))
                if token_upper_bound(required + selected + group) <= budget:
                    break
            if token_upper_bound(required + selected + group) > budget:
                contents = ["[compressed evidence reference]\n" + canonical(compact({k: item.get(k) for k in
                            ("tool", "args", "reason", "ok", "error", "raw_ref")}, 512)) for item in items]
                group = ([Message(role="user", content=content) for content in contents]
                         if reviewing else group_messages(rendering_items, contents))
        if token_upper_bound(required + selected + group) <= budget:
            selected[0:0] = group
        else:
            omitted.append(index)
    messages = list(pinned) + selected + required[len(pinned):]
    return messages, {"estimated_upper_bound_tokens": token_upper_bound(messages),
                      "estimator": "utf8_byte_upper_bound", "budget": budget,
                      "compressed_history": compressed, "omitted_history": omitted,
                      "preserved_review_history": preserved,
                      "reviewing": reviewing,
                      "prior_review_issues_visible": bool(review_issues) and not reviewing,
                      "validation_issues_visible": bool(validation_issues) and not reviewing,
                      "visible_sha256": digest([m.to_wire() for m in messages])}
