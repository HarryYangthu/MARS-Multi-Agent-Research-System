"""Atomic, model-authored field edits to an immutable candidate; no content repair."""
from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any

from app.harness.agent_loop.protocol import parse_action
from app.harness.agent_loop.trace import digest
from app.harness.schema.frontmatter_parser import parse


REVISE_DOCUMENT = "mars_revise_document"


def revision_spec() -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": REVISE_DOCUMENT,
        "description": (
            "Revise the current candidate atomically, then validate and independently review the entire document. "
            "Call alone. Copy base_sha256 from the current candidate receipt. Operations use absolute JSON "
            "Pointers rooted at /metadata or /body. Set adds/replaces an object field or replaces an existing "
            "array element; remove deletes an existing field/element. Parents must exist; replace whole arrays "
            "to insert/reorder. Update all related fields, including body if human_summary changes. "
            "Unchanged fields are preserved exactly as data; no content is supplied by the host."),
        "parameters": {"type": "object", "additionalProperties": False,
            "required": ["base_sha256", "operations"], "properties": {
                "base_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                "operations": {"type": "array", "minItems": 1, "maxItems": 64, "items": {
                    "type": "object", "additionalProperties": False, "required": ["op", "path"],
                    "properties": {"op": {"enum": ["set", "remove"]},
                        "path": {"type": "string", "pattern": "^/(metadata/|body$)"}, "value": {}}}}}}}}


def apply_document_revision(candidate: str, revision: Any) -> str:
    if not candidate:
        raise ValueError("no current candidate; submit a complete document first")
    if not isinstance(revision, dict) or set(revision) != {"base_sha256", "operations"}:
        raise ValueError("revision requires exactly base_sha256 and operations")
    if revision["base_sha256"] != digest(candidate):
        raise ValueError("stale revision base_sha256; use the current candidate receipt")
    operations = revision["operations"]
    if not isinstance(operations, list) or not 1 <= len(operations) <= 64:
        raise ValueError("revision requires 1..64 explicit operations")
    parsed = parse(candidate)
    document = deepcopy({"metadata": parsed.metadata, "body": parsed.body})
    for operation in operations:
        if not isinstance(operation, dict) or operation.get("op") not in {"set", "remove"}:
            raise ValueError("revision operation must be set or remove")
        expected = {"op", "path", "value"} if operation["op"] == "set" else {"op", "path"}
        if set(operation) != expected:
            raise ValueError("set requires a value; remove must omit value; unknown keys are forbidden")
        path = operation["path"]
        if (not isinstance(path, str) or not (path.startswith("/metadata/") or path == "/body")
                or re.search(r"~(?![01])", path)):
            raise ValueError("revision path must be a valid pointer below /metadata or exactly /body")
        tokens = [token.replace("~1", "/").replace("~0", "~") for token in path[1:].split("/")]
        parent: Any = document
        try:
            for token in tokens[:-1]:
                if isinstance(parent, list):
                    if not re.fullmatch(r"0|[1-9][0-9]*", token):
                        raise ValueError("array index must be canonical")
                    parent = parent[int(token)]
                elif isinstance(parent, dict):
                    parent = parent[token]
                else:
                    raise ValueError("revision parent is not a container")
            key: Any = tokens[-1]
            if isinstance(parent, list):
                if not re.fullmatch(r"0|[1-9][0-9]*", key):
                    raise ValueError("array index must be canonical")
                key = int(key)
                if key >= len(parent):
                    raise ValueError("set cannot append to an array; replace the array")
            elif not isinstance(parent, dict):
                raise ValueError("revision parent is not a container")
            if operation["op"] == "set":
                parent[key] = deepcopy(operation["value"])
            else:
                del parent[key]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"revision path does not resolve: {path}") from exc
    # Reuse exactly the ordinary submission serializer, including its finite-number
    # and final-envelope checks. The usual whole-document validators run next.
    return str(parse_action(json.dumps({"final": document}, ensure_ascii=False, allow_nan=False))["final"])
