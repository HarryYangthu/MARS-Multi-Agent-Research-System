"""Tiny deterministic embedder so KB tests don't need network / model files.

Uses a SHA-based hashing trick to map tokens into a fixed-dim vector.
Good enough for the V0 baseline matcher unit tests; replace with a real
sentence-transformers / Chroma embedding when running on hardware.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Sequence

import numpy as np

DIM = 256
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def embed(text: str, *, dim: int = DIM) -> np.ndarray:
    vec = np.zeros(dim, dtype=np.float32)
    for tok in tokenize(text):
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        # Map first 8 bytes to bucket index, sign from byte 9
        bucket = int.from_bytes(h[:8], "little") % dim
        sign = 1 if h[9] % 2 == 0 else -1
        vec[bucket] += sign
    norm = float(math.sqrt(float((vec * vec).sum())))
    if norm == 0:
        return vec
    return vec / norm


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def embed_many(texts: Sequence[str], *, dim: int = DIM) -> np.ndarray:
    out = np.zeros((len(texts), dim), dtype=np.float32)
    for i, t in enumerate(texts):
        out[i] = embed(t, dim=dim)
    return out
