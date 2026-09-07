"""Deterministic structural compression; source text never gains instruction trust."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.harness.agent_loop.trace import canonical, digest
from app.harness.llm.provider_base import Message


def token_upper_bound(messages: Sequence[Message]) -> int:
    # UTF-8 bytes are a deliberately conservative tokenizer-independent bound.
    # The small per-message allowance includes message framing.
    return sum(len(m.content.encode("utf-8")) + 16 for m in messages)


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
    candidate: str, *, budget: int, observation_chars: int,
) -> tuple[list[Message], dict[str, Any]]:
    required = list(pinned)
    if candidate:
        required.append(Message(role="assistant", content=canonical({"candidate": candidate})))
    if feedback:
        required.append(Message(role="user", content="[host validation/review feedback]\n" + feedback))
    if token_upper_bound(required) > budget:
        raise ValueError("pinned rules/task/schema/upstream/candidate exceed input budget; nothing was silently dropped")
    selected: list[Message] = []
    compressed: list[int] = []
    omitted: list[int] = []
    # Newer observations get priority, but output ordering remains chronological.
    for index in reversed(range(len(history))):
        item = history[index]
        content = "[untrusted prior action and host Observation]\n" + canonical(compact(item, observation_chars))
        message = Message(role="user", content=content)
        if token_upper_bound(required + selected + [message]) > budget:
            minimal = {k: item.get(k) for k in ("tool", "args", "reason", "ok", "error", "raw_ref")}
            message = Message(role="user", content="[compressed evidence reference]\n" + canonical(minimal))
            compressed.append(index)
        if token_upper_bound(required + selected + [message]) <= budget:
            selected.insert(0, message)
        else:
            omitted.append(index)
    messages = list(pinned) + selected + required[len(pinned):]
    return messages, {"estimated_upper_bound_tokens": token_upper_bound(messages),
                      "estimator": "utf8_byte_upper_bound", "budget": budget,
                      "compressed_history": compressed, "omitted_history": omitted,
                      "visible_sha256": digest([{"role": m.role, "content": m.content} for m in messages])}
