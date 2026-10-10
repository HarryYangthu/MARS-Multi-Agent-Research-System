"""Read-only inspection of persisted Agent events; prompts are loaded on demand."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator

from app.harness.agent_loop.trace import digest

AGENTS = frozenset({"commander", "idea", "experiment", "coding", "execution", "writing"})


def _trace_events(root: Path, agent: str) -> Iterator[tuple[str, dict[str, Any]]]:
    for path in sorted((root / "agent_traces").glob("*/*/events.jsonl")):
        owner = path.parent.parent.name
        if owner != agent and not owner.startswith(agent + "_"):
            continue
        if not path.resolve().is_relative_to((root / "agent_traces").resolve()):
            continue
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue  # An in-flight final line is not a complete event.
                if isinstance(row, dict):
                    yield f"{owner}:{path.parent.name}:{row.get('event_seq', '')}", row


def _commander_history(root: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    # Commander inputs belong to a conversation, rather than a stage run.
    conversations = root.parent.parent / "conversations"
    for session_path in sorted(conversations.glob("*/session.json")):
        if not session_path.resolve().is_relative_to(conversations.resolve()):
            continue
        try:
            session = json.loads(session_path.read_text())
            if session.get("linked_run_id") != root.name:
                continue
            folder = session_path.parent
            traced_events = list(_trace_events(folder, "commander"))
            yield from traced_events
            manifests_traced = {row.get("manifest_id") for _, row in traced_events}
            for path in sorted((folder / "context/agents/commander/manifests").glob("*.json")):
                if not path.resolve().is_relative_to(folder.resolve()):
                    continue
                manifest = json.loads(path.read_text())
                diagnostics = manifest.get("diagnostics", {})
                ref = diagnostics.get("payload_ref", "")
                if diagnostics.get("source") != "actual_pre_call_payload" or not re.fullmatch("[a-f0-9]{64}", ref):
                    continue
                material = folder / "context/materials" / f"{ref}.json"
                if not material.resolve().is_relative_to(folder.resolve()):
                    continue
                payload = json.loads(json.loads(material.read_text())["message"]["content"])
                if digest(payload) != diagnostics.get("payload_sha256"):
                    continue
                # Avoid repeating a manifest captured in a new durable trace.
                if manifest.get("manifest_id") in manifests_traced:
                    continue
                identifier = f"commander:{folder.name}_{digest(manifest)}"
                common = {"time": manifest.get("created_at", ""), "source": "persisted_context_manifest"}
                yield identifier + ":1", {**manifest, **common, "event_seq": 1, "kind": "context_packed"}
                yield identifier + ":2", {**common, "event_seq": 2, "kind": "model_request", "visible": payload}
        except (OSError, ValueError, KeyError, TypeError):
            continue


def _events(root: Path, agent: str) -> Iterator[tuple[str, dict[str, Any]]]:
    if agent not in AGENTS:
        raise ValueError("unsupported agent")
    yield from _trace_events(root, agent)
    if agent == "commander":
        yield from _commander_history(root)


def inspection_catalog(root: Path, agent: str) -> dict[str, Any]:
    events = []
    requests = []
    for identifier, row in _events(root, agent):
        item = {"id": identifier, "kind": row.get("kind", ""), "time": row.get("time", ""),
                "model": row.get("model"), "tool": row.get("tool"), "status": row.get("status"),
                "ok": row.get("ok"), "request": row.get("request"), "backend": row.get("backend")}
        events.append(item)
        if row.get("kind") == "model_request":
            requests.append(item)
    events.sort(key=lambda item: str(item["time"]))
    requests.sort(key=lambda item: str(item["time"]))
    return {"agent": agent, "events": events, "requests": requests}


def inspection_event(root: Path, agent: str, identifier: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+:[a-zA-Z0-9_-]+:[0-9]+", identifier):
        raise ValueError("invalid event identifier")
    packed: dict[str, Any] | None = None
    found: dict[str, Any] | None = None
    wire: Any = None
    invocation = identifier.rsplit(":", 1)[0]
    for key, row in _events(root, agent):
        if key.rsplit(":", 1)[0] != invocation:
            continue
        if found is None and row.get("kind") == "context_packed":
            packed = row
        if key == identifier:
            found = row
            if row.get("kind") != "model_request":
                break
            continue
        if found is not None:
            if row.get("kind") == "provider_request":
                wire = row.get("visible")
                break
            if row.get("kind") in {"model_request", "model_response", "model_error"}:
                break
    if found is None:
        raise FileNotFoundError(identifier)
    return {"id": identifier, "event": found, "assembly": packed,
            "wire_payload": wire, "prompt_source": "provider_request" if wire is not None else "pre_provider_snapshot"}
