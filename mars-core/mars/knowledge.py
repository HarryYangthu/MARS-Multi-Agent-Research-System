"""Project knowledge base: heading/code-aware chunks with BM25 retrieval (no vector store needed at this scale)."""
from __future__ import annotations

import json
import math
import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Any

MAX_CHARS = 1800


def tokenize(text: str) -> list[str]:
    """ASCII words plus CJK character bigrams, so Chinese and English queries both match."""
    text = text.lower()
    words = re.findall(r"[a-z0-9_.+\-]{2,}", text)
    cjk = re.findall(r"[一-鿿]+", text)
    bigrams = [run[i:i + 2] for run in cjk for i in range(max(1, len(run) - 1))]
    return words + bigrams


def _split_markdown(text: str) -> list[tuple[str, str]]:
    sections, title, buffer = [], "", []
    for line in text.splitlines():
        if re.match(r"^#{1,4} ", line) and buffer:
            sections.append((title, "\n".join(buffer)))
            buffer = []
        if re.match(r"^#{1,4} ", line):
            title = line.lstrip("# ").strip()
        buffer.append(line)
    if buffer:
        sections.append((title, "\n".join(buffer)))
    return sections


def _split_code(text: str) -> list[tuple[str, str]]:
    parts, title, buffer = [], "module", []
    for line in text.splitlines():
        if re.match(r"^(class|def) ", line) and buffer:
            parts.append((title, "\n".join(buffer)))
            buffer = []
            title = line.split("(")[0].split(":")[0]
        buffer.append(line)
    if buffer:
        parts.append((title, "\n".join(buffer)))
    return parts


class KnowledgeBase:
    def __init__(self, files: list[tuple[Path, str]], extra_records: list[dict[str, Any]] | None = None) -> None:
        self.chunks: list[dict[str, Any]] = []
        for path, kind in files:
            text = path.read_text(errors="replace", encoding="utf-8")
            pieces = _split_code(text) if path.suffix == ".py" else _split_markdown(text)
            for title, body in pieces:
                for start in range(0, len(body), MAX_CHARS):
                    piece = body[start:start + MAX_CHARS].strip()
                    if len(piece) > 20:
                        self.chunks.append({"id": self._id(path.name, title, piece), "source": str(path), "kind": kind,
                                            "title": title, "text": piece})
        for record in extra_records or []:
            text = record["text"][:MAX_CHARS]
            self.chunks.append({"id": self._id(record.get("source", "mars_history"), record.get("title", ""), text),
                                "source": record.get("source", "mars_history"), "kind": record.get("kind", "mars_history"),
                                "title": record.get("title", ""), "text": text})
        self._tokens = [tokenize(c["title"] + " " + c["text"]) for c in self.chunks]
        self._df = Counter(t for tokens in self._tokens for t in set(tokens))
        self._avg = sum(len(t) for t in self._tokens) / max(1, len(self._tokens))

    def _id(self, source: str, title: str, text: str) -> str:
        """Content-derived id (source file name, section, text): editing or reordering knowledge files never makes an
        id cited by an earlier proposal point at different content; a changed chunk gets a new id."""
        base = "K" + hashlib.sha1(f"{source}|{title}|{text}".encode()).hexdigest()[:8]
        taken = {c["id"] for c in self.chunks}
        return base if base not in taken else next(f"{base}-{n}" for n in range(2, 10_000) if f"{base}-{n}" not in taken)

    def get(self, chunk_id: str) -> dict[str, Any] | None:
        return next((c for c in self.chunks if c["id"] == chunk_id), None)

    def search(self, query: str, k: int = 6, kinds: set[str] | None = None) -> list[dict[str, Any]]:
        terms = tokenize(query)
        n = len(self.chunks)
        scored = []
        for chunk, tokens in zip(self.chunks, self._tokens):
            if kinds and chunk["kind"] not in kinds:
                continue
            counts, length, score = Counter(tokens), len(tokens), 0.0
            for term in terms:
                if term not in counts:
                    continue
                idf = math.log(1 + (n - self._df[term] + 0.5) / (self._df[term] + 0.5))
                tf = counts[term]
                score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / self._avg))
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda item: -item[0])
        return [{**chunk, "score": round(score, 2)} for score, chunk in scored[:k]]

    def search_many(self, queries: list[str], k: int = 4, limit: int = 16) -> list[dict[str, Any]]:
        seen, found = set(), []
        for query in queries:
            for chunk in self.search(query, k):
                if chunk["id"] not in seen:
                    seen.add(chunk["id"])
                    found.append(chunk)
        return found[:limit]


def load_history(path: Path) -> list[dict[str, Any]]:
    """MARS's own experiment journal (one JSON record per evaluated candidate / decision)."""
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            records.append({"kind": "mars_history", "source": str(path), "title": item.get("title", ""),
                            "text": json.dumps(item, ensure_ascii=False)})
    return records
