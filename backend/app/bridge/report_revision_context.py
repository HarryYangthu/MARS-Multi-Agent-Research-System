"""Retain explicit review feedback while the same report draft is retried."""
import hashlib
import json

from app.harness.agent_loop.trace import atomic_json
from app.harness.runtime.project_scope import safe_scope_path
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunHandle


def report_revision_reason(run: RunHandle, reason: str) -> str:
    versions = [item for item in ArtifactStore(run).list_versions(agent_dir="writing", stem="research_report")
                if item.version.startswith("v")]
    if not versions:
        return reason
    source = versions[-1]
    relative = source.path.relative_to(run.root).as_posix()
    raw = safe_scope_path(run.root, relative, must_exist=True).read_bytes()
    identity = {"run_id": run.run_id, "project": run.project, "source": relative,
                "sha256": hashlib.sha256(raw).hexdigest()}
    path = safe_scope_path(run.root, "hitl/report_revision_request.json")
    if reason:
        atomic_json(path, {**identity, "reason": reason})
        return reason
    if not path.is_file():
        return ""
    receipt = json.loads(path.read_text())
    if any(receipt.get(key) != value for key, value in identity.items()):
        return ""
    approved = safe_scope_path(run.root, "writing/research_report.approved.md")
    if approved.is_file() and approved.read_bytes() == raw:
        return ""
    return receipt["reason"] if isinstance(receipt.get("reason"), str) else ""
