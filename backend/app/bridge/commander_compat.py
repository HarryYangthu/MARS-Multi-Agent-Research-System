"""Decode explicit gateway text commands; execution still uses normal tool admission."""
from __future__ import annotations

import json
from typing import Any

from app.bridge.commander_tools import TOOLS
from app.harness.llm.structured_output import parse_json_object


def parse_text_decision(text: str) -> dict[str, Any] | None:
    try:
        raw = parse_json_object(text)
    except ValueError:
        return None
    actions = raw.get("actions")
    if actions is None and isinstance(raw.get("function_call"), dict):
        actions = [raw["function_call"]]
    if actions is None and isinstance(raw.get("tool") or raw.get("name"), str):
        actions = [raw]
    if not isinstance(actions, list) or not 1 <= len(actions) <= 8:
        return None
    from app.bridge.commander_native import native_tool_names
    names = native_tool_names()
    decoded = []
    for action in actions:
        if not isinstance(action, dict):
            return None
        name = action.get("tool", action.get("name"))
        if not isinstance(name, str):
            return None
        name = names.get(name, name)
        if name not in TOOLS:
            return None
        args = action.get("args", action.get("arguments"))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                return None
        if not isinstance(args, dict):
            return None
        if "args" in action and "arguments" in action and action["args"] != action["arguments"]:
            return None
        decoded.append({"tool": name, "args": args})
    return {"actions": decoded, "reply": "", "native_status": "action", "transport": "text_compatibility"}
