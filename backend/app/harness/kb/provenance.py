"""Host-authored retrieval receipts. Model metadata alone never proves provenance."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from app.harness.agent_loop.trace import atomic_json


def record_retrieval(*, text: str, url: str, run_id: str, title: str) -> dict[str, Any]:
    from app.harness.kb.stores import get_stores
    base = get_stores().base.resolve()
    target = base / "_retrieval_receipts" / (uuid.uuid4().hex + ".json")
    receipt = {"kind": "real_retrieval", "url": url, "run_id": run_id, "title": title,
               "text": text, "text_sha256": hashlib.sha256(text.encode()).hexdigest()}
    atomic_json(target, receipt)
    return {"origin": "real_retrieval", "retrieval_receipt": str(target),
            "retrieval_receipt_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "url": url, "title": title, "run_id": run_id}


def verified_memory(text: str, metadata: dict[str, Any]) -> bool:
    if metadata.get("is_mock") or metadata.get("origin") != "real_retrieval":
        return False
    try:
        from app.harness.kb.stores import get_stores
        root = (get_stores().base / "_retrieval_receipts").resolve()
        path = Path(str(metadata["retrieval_receipt"])).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return False
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != metadata.get("retrieval_receipt_sha256"):
            return False
        receipt = json.loads(data)
        original = receipt["text"]
        return (receipt["kind"] == "real_retrieval" and receipt["url"] == metadata.get("url")
                and receipt["title"] == metadata.get("title")
                and receipt["text_sha256"] == hashlib.sha256(original.encode()).hexdigest()
                and bool(text.strip()) and text in original)
    except (OSError, ValueError, KeyError, TypeError):
        return False
