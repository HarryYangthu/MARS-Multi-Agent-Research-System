"""Deterministic excerpt, reference and lexical selection utilities.

These functions do not perform model summarization. The native loop keeps
pinned inputs and full on-disk observations alongside bounded excerpts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Strategy = Literal["hier_summary", "reference", "relevance_prune"]


@dataclass
class CompressedSegment:
    original_chars: int
    compressed_chars: int
    strategy: Strategy
    text: str


def hier_summary(text: str, *, keep_chars: int = 1500) -> CompressedSegment:
    head = text[: keep_chars // 2]
    tail = text[-keep_chars // 2 :]
    body = head + "\n[... EXCERPT: omitted middle ...]\n" + tail
    return CompressedSegment(
        original_chars=len(text),
        compressed_chars=len(body),
        strategy="hier_summary",
        text=body,
    )


def reference(text: str, *, pointer: str) -> CompressedSegment:
    body = f"[reference: {pointer}] (original {len(text)} chars)"
    return CompressedSegment(
        original_chars=len(text),
        compressed_chars=len(body),
        strategy="reference",
        text=body,
    )


def relevance_prune(
    chunks: list[str], *, query: str, top_k: int = 5
) -> list[CompressedSegment]:
    """Trivial keyword overlap scoring; replace with embedding scoring later."""
    q_terms = set(query.lower().split())
    scored = []
    for c in chunks:
        c_terms = set(c.lower().split())
        score = len(q_terms & c_terms)
        scored.append((score, c))
    scored.sort(key=lambda t: t[0], reverse=True)
    pruned = [c for _, c in scored[:top_k]]
    return [
        CompressedSegment(
            original_chars=len(c),
            compressed_chars=len(c),
            strategy="relevance_prune",
            text=c,
        )
        for c in pruned
    ]
