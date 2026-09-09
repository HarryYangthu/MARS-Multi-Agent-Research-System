"""Verify inherited parent identities before restoring delegated research counts."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ResearchOrigin:
    run_id: str
    invocation_path: str
    root: Path
    files: dict[str, str]

    def verify_request(self, current: Path, relative: str) -> None:
        expected = self.files.get(relative)
        if (not expected or hashlib.sha256(current.read_bytes()).hexdigest() != expected
                or hashlib.sha256((self.root/relative).read_bytes()).hexdigest() != expected):
            raise ValueError("inherited research request does not match verified continuation hashes")


def research_origins(root: Path, *, run_id: str, parent_invocation: str,
                     history: list[dict[str, Any]]) -> list[ResearchOrigin]:
    """Read only fixed run lineage files; never open a path supplied by a model."""
    current = root.resolve()
    invocation = Path(parent_invocation).name
    expected_run = run_id
    origins: list[ResearchOrigin] = []
    seen = {current}
    while (current/"input"/"continuation.json").is_file():
        if len(origins) >= 16:
            raise ValueError("research continuation lineage exceeds validation depth")
        manifest = json.loads((current/"input"/"continuation.json").read_text())
        if (not isinstance(manifest, dict) or manifest.get("schema") != "idea.research_continuation.v1"
                or manifest.get("continuation_run_id") != expected_run or manifest.get("invocation") != invocation
                or manifest.get("budgets_reset") is not False):
            raise ValueError("research continuation lineage identity/budget mismatch")
        source_value = manifest.get("source_run_root")
        if not isinstance(source_value, str) or not Path(source_value).is_absolute():
            raise ValueError("research continuation source root must be absolute")
        source = Path(source_value).resolve()
        if source in seen:
            raise ValueError("research continuation lineage cycle")
        seen.add(source)
        files = manifest.get("source_files_sha256")
        source_run_id = manifest.get("source_run_id")
        if (not isinstance(files, dict) or not all(isinstance(k, str) and isinstance(v, str) for k,v in files.items())
                or not isinstance(source_run_id, str) or not source_run_id):
            raise ValueError("research continuation source hashes/identity missing")
        relative = f"agent_traces/idea/{invocation}/checkpoint.json"
        checkpoint_bytes = (source/relative).read_bytes()
        if hashlib.sha256(checkpoint_bytes).hexdigest() != files.get(relative):
            raise ValueError("research continuation source checkpoint hash mismatch")
        checkpoint = json.loads(checkpoint_bytes)
        prefix = checkpoint.get("history")
        if (checkpoint.get("status") not in {"model_error", "interrupted"} or not isinstance(prefix, list)
                or history[:len(prefix)] != prefix):
            raise ValueError("research continuation source history is not an inherited parent prefix")
        source_request = (source/"input"/"request.json").read_bytes()
        if (hashlib.sha256(source_request).hexdigest() != files.get("input/request.json")
                or json.loads(source_request).get("run_id") != source_run_id):
            raise ValueError("research continuation source run identity is not hash verified")
        origins.append(ResearchOrigin(source_run_id, str(source/"agent_traces"/"idea"/invocation), source, files))
        current, expected_run = source, source_run_id
    return origins
