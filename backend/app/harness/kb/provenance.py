"""Host-authored retrieval receipts. Model metadata alone never proves provenance."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from app.harness.agent_loop.trace import atomic_json


def record_retrieval(*, text: str, url: str, run_id: str, title: str, base: Path | None = None) -> dict[str, Any]:
    from app.harness.kb.stores import get_stores
    base = (base if base is not None else get_stores().base).resolve()
    target = base / "_retrieval_receipts" / (uuid.uuid4().hex + ".json")
    receipt = {"kind": "real_retrieval", "url": url, "run_id": run_id, "title": title,
               "text": text, "text_sha256": hashlib.sha256(text.encode()).hexdigest()}
    atomic_json(target, receipt)
    return {"origin": "real_retrieval", "retrieval_receipt": str(target),
            "retrieval_receipt_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "url": url, "title": title, "run_id": run_id}


def record_artifact(*, path: Path, run_id: str, project: str,
                    extracted_text: str | None = None, base: Path | None = None) -> dict[str, Any]:
    """Bind a host extraction to actual local bytes, without asserting scientific truth.

    Approval remains a separate write/selection gate. This function is not a tool
    exposed to the model. Keep an immutable source snapshot so later edits do not
    rewrite the provenance of already approved memory.
    """
    from app.harness.kb.stores import get_stores
    source = path.resolve(strict=True)
    source_text = source.read_text(encoding="utf-8")
    text = source_text if extracted_text is None else extracted_text
    if not text.strip():
        raise ValueError("empty artifact extraction")
    root = (base if base is not None else get_stores().base).resolve()
    target = root / "_artifact_receipts" / (uuid.uuid4().hex + ".json")
    receipt = {"kind": "local_artifact", "source_path": str(source),
               "source_text": source_text,
               "source_sha256": hashlib.sha256(source_text.encode()).hexdigest(),
               "run_id": run_id, "project": project, "text": text,
               "text_sha256": hashlib.sha256(text.encode()).hexdigest()}
    atomic_json(target, receipt)
    return {"origin": "local_artifact", "artifact_receipt": str(target),
            "artifact_receipt_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "source_sha256": receipt["source_sha256"], "run_id": run_id, "project": project}


def verified_memory(text: str, metadata: dict[str, Any], *, base: Path | None = None) -> bool:
    origin = metadata.get("origin")
    if metadata.get("is_mock") or origin not in {"real_retrieval", "local_artifact"}:
        return False
    try:
        from app.harness.kb.stores import get_stores
        prefix = "retrieval" if origin == "real_retrieval" else "artifact"
        root = ((base if base is not None else get_stores().base) / f"_{prefix}_receipts").resolve()
        path = Path(str(metadata[f"{prefix}_receipt"])).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return False
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != metadata.get(f"{prefix}_receipt_sha256"):
            return False
        receipt = json.loads(data)
        original = receipt["text"]
        if (receipt["kind"] != origin or not text.strip() or text not in original
                or receipt["text_sha256"] != hashlib.sha256(original.encode()).hexdigest()
                or receipt["run_id"] != metadata.get("run_id")):
            return False
        if origin == "real_retrieval":
            return bool(receipt["url"] == metadata.get("url") and receipt["title"] == metadata.get("title"))
        return bool(receipt["project"] == metadata.get("project")
                and receipt["source_sha256"] == metadata.get("source_sha256")
                and receipt["source_sha256"] == hashlib.sha256(receipt["source_text"].encode()).hexdigest())
    except (OSError, ValueError, KeyError, TypeError):
        return False
