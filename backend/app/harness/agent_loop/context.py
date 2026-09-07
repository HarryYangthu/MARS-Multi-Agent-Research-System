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
        preserved = {"url", "pdf_url", "download_url", "raw_ref", "sha256", "title", "error", "reason", "status"}
        return {k: v if k in preserved else compact(v, chars) for k, v in value.items()}
    if isinstance(value, list):
        return [compact(x, chars) for x in value]
    if isinstance(value, str) and len(value) > chars:
        return {"excerpt": value[:chars], "original_chars": len(value), "sha256": digest(value), "truncated": True}
    return value


def pack_context(
    pinned: list[Message], history: list[dict[str, Any]], feedback: str,
    candidate: str, *, budget: int, observation_chars: int, native: bool = False,
    reviewing: bool = False, review_issues: Sequence[str] = (),
) -> tuple[list[Message], dict[str, Any]]:
    required = list(pinned)
    if review_issues and not reviewing:
        # The author needs revision history. A fresh reviewer must judge the
        # current document without treating earlier model criticism as facts.
        required.append(Message(role="user", content=(
            "[unresolved review issues pinned through protocol/schema repairs]\n"
            + canonical(list(review_issues)))))
    if history:
        # The full observations may be compressed/omitted, but the agent must
        # still know which actions really happened and where their receipts live.
        ledger = [{k: item.get(k) for k in ("tool", "ok", "error", "reason", "raw_ref")}
                  for item in history]
        required.append(Message(role="user", content="[untrusted action receipt index; not full source content]\n" + canonical(ledger)))
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
    groups = history_groups(history)
    for index in reversed(range(len(groups))):
        items = groups[index]
        contents = ["[untrusted prior action and host Observation]\n" + canonical(compact(item, observation_chars)) for item in items]
        # A separate reviewer reads evidence documents, not the generator's
        # native assistant/tool conversation. Its configured tool set is empty.
        group = ([Message(role="user", content=content) for content in contents]
                 if reviewing else group_messages(items, contents))
        if token_upper_bound(required + selected + group) > budget:
            contents = ["[compressed evidence reference]\n" + canonical({k: item.get(k) for k in
                        ("tool", "args", "reason", "ok", "error", "raw_ref")}) for item in items]
            group = ([Message(role="user", content=content) for content in contents]
                     if reviewing else group_messages(items, contents))
            compressed.append(index)
        if token_upper_bound(required + selected + group) <= budget:
            selected[0:0] = group
        else:
            omitted.append(index)
    messages = list(pinned) + selected + required[len(pinned):]
    return messages, {"estimated_upper_bound_tokens": token_upper_bound(messages),
                      "estimator": "utf8_byte_upper_bound", "budget": budget,
                      "compressed_history": compressed, "omitted_history": omitted,
                      "reviewing": reviewing,
                      "prior_review_issues_visible": bool(review_issues) and not reviewing,
                      "visible_sha256": digest([m.to_wire() for m in messages])}
