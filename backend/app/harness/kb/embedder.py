"""Versioned lexical retrieval and an explicit, real embedding service adapter.

The default is Unicode word/Chinese bigram lexical retrieval, not a neural
embedding. Hash vectors remain a compact storage representation; ranking uses
exact term counts to avoid hash-collision matches. Semantic mode must be
configured explicitly and fails if its actual service is unavailable.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Sequence

import httpx
import numpy as np
import yaml

from app.settings import repo_root

DIM = 256
LEXICAL_VERSION = "unicode_words_cjk_bigrams_v2"
_TOKEN_RE = re.compile(r"([\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+)")
_WORD_RE = re.compile(r"[^\W_]+(?:_[^\W_]+)*", re.UNICODE)
_CJK_RE = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+$")


def tokenize(text: str) -> list[str]:
    terms: list[str] = []
    for token in _TOKEN_RE.split(text.casefold()):
        if _CJK_RE.fullmatch(token):
            terms.extend(token)
            terms.extend(token[i:i + 2] for i in range(len(token) - 1))
        else:
            terms.extend(_WORD_RE.findall(token))
    return terms


def embed(text: str, *, dim: int = DIM) -> np.ndarray:
    """Pure deterministic lexical hash vector (never claimed to be semantic)."""
    if dim <= 0:
        raise ValueError("embedding dimension must be positive")
    vec = np.zeros(dim, dtype=np.float32)
    for token in tokenize(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:8], "little") % dim
        vec[bucket] += 1 if digest[9] % 2 == 0 else -1
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm else vec


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a.ndim != 1 or b.ndim != 1 or a.shape != b.shape:
        raise ValueError("cannot compare embedding dimensions; re-embed with one embedding version")
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if not na or not nb:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def lexical_similarity(left: str, right: str) -> float:
    a, b = Counter(tokenize(left)), Counter(tokenize(right))
    if not a or not b:
        return 0.0
    dot = sum(value * b.get(term, 0) for term, value in a.items())
    norm = math.sqrt(sum(value * value for value in a.values()) * sum(value * value for value in b.values()))
    return dot / norm


def embed_many(texts: Sequence[str], *, dim: int = DIM) -> np.ndarray:
    return np.stack([embed(text, dim=dim) for text in texts]) if texts else np.zeros((0, dim), dtype=np.float32)


@dataclass(frozen=True)
class EmbeddingSpec:
    provider: str = "lexical_unicode"
    model: str = LEXICAL_VERSION
    dim: int = DIM
    base_url: str = ""
    api_key_env: str = "MARS_EMBEDDING_API_KEY"
    timeout_seconds: float = 30.0

    @property
    def version(self) -> str:
        identity = {"provider": self.provider, "model": self.model, "dim": self.dim,
                    "base_url": self.base_url, "tokenizer": LEXICAL_VERSION if self.provider == "lexical_unicode" else "provider"}
        return "embedding:" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]


def embedding_spec() -> EmbeddingSpec:
    path = repo_root() / "configs" / "knowledge.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    cfg = raw.get("embedding", {}) if isinstance(raw, dict) else {}
    provider = str(os.environ.get("MARS_EMBEDDING_PROVIDER", cfg.get("provider", "lexical_unicode")))
    if provider == "hash_local":
        provider = "lexical_unicode"
    if provider not in {"lexical_unicode", "openai_compatible"}:
        raise ValueError(f"unsupported embedding provider: {provider}")
    spec = EmbeddingSpec(
        provider=provider,
        model=LEXICAL_VERSION if provider == "lexical_unicode" else str(os.environ.get("MARS_EMBEDDING_MODEL", cfg.get("model", ""))),
        dim=int(os.environ.get("MARS_EMBEDDING_DIM", cfg.get("dim", DIM))),
        base_url=str(os.environ.get("MARS_EMBEDDING_BASE_URL", cfg.get("base_url", ""))).rstrip("/"),
        api_key_env=str(cfg.get("api_key_env", "MARS_EMBEDDING_API_KEY")),
        timeout_seconds=float(cfg.get("timeout_seconds", 30)),
    )
    if spec.dim <= 0 or spec.timeout_seconds <= 0:
        raise ValueError("embedding dimension and timeout must be positive")
    if provider == "openai_compatible" and (not spec.model or not spec.base_url.startswith(("https://", "http://"))):
        raise ValueError("real embedding service requires model and HTTP(S) base_url")
    return spec


@lru_cache(maxsize=1024)
def _configured_values(text: str, spec: EmbeddingSpec) -> tuple[float, ...]:
    if spec.provider not in {"lexical_unicode", "openai_compatible"}:
        raise ValueError(f"unsupported embedding provider: {spec.provider}")
    if spec.provider == "lexical_unicode":
        return tuple(float(x) for x in embed(text, dim=spec.dim))
    key = os.environ.get(spec.api_key_env, "")
    if not key:
        raise RuntimeError(f"real embedding service requires environment variable {spec.api_key_env}")
    with httpx.Client(timeout=spec.timeout_seconds, follow_redirects=False) as client:
        response = client.post(spec.base_url + "/embeddings", headers={"Authorization": "Bearer " + key},
                               json={"model": spec.model, "input": [text]})
        response.raise_for_status()
        payload = response.json()
    vector = np.asarray(payload["data"][0]["embedding"], dtype=np.float32)
    if vector.shape != (spec.dim,) or not np.isfinite(vector).all() or not np.linalg.norm(vector):
        raise ValueError("real embedding service returned invalid or incompatible vector")
    return tuple(float(x) for x in vector)


def configured_embed(text: str, *, spec: EmbeddingSpec | None = None) -> np.ndarray:
    return np.asarray(_configured_values(text, spec or embedding_spec()), dtype=np.float32)


def embedding_metadata(spec: EmbeddingSpec) -> dict[str, Any]:
    return {"embedding_version": spec.version, "embedding_provider": spec.provider,
            "embedding_model": spec.model, "embedding_dim": spec.dim}


def retrieval_similarity(query: str, text: str, vector: np.ndarray, metadata: dict[str, Any], *,
                         spec: EmbeddingSpec | None = None, query_vector: np.ndarray | None = None) -> float:
    active = spec or embedding_spec()
    if active.provider == "lexical_unicode":
        # Reconstruct from original text: legacy zero/ASCII/different dimensions
        # remain readable without pretending they share the current vector space.
        return lexical_similarity(query, text)
    current = vector if (metadata.get("embedding_version") == active.version and vector.shape == (active.dim,)) else configured_embed(text, spec=active)
    return cosine(query_vector if query_vector is not None else configured_embed(query, spec=active), current)
