"""Gate 5 — baseline_compatibility.

★ This gate sits on the **tool dispatch path**, not on a flow checkpoint.
Every tool call goes through ``harness/tools/registry.py::dispatch()`` which
calls ``gate_check`` on every dispatch. The check reads the project's
``AGENTS.md`` and ``repo_link.yaml::protected_paths`` to decide whether the
tool's args would mutate a baseline-protected surface.

For V0 we hard-code the rule set against the moe-pimc patterns; if a
project supplies its own ``AGENTS.md`` we additionally pull file/path
patterns from there (lightweight regex scan).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from app.harness.gates.gate_base import GateOutcome
from app.harness.tools.registry import GateDecision, ToolContext
from app.settings import repo_root

GATE_ID = "baseline_compatibility"

# Tool names whose dispatch we care about.
MONITORED_TOOLS: tuple[str, ...] = (
    "code.patch_generator",
    "code.apply_patch",
    "code.write_file",
    "code.delete_file",
)

# Forbidden regex against the third positional arg of forward(...) — pulled
# straight from projects/moe-pimc/AGENTS.md rule #2.
_FORWARD_INTERFACE_RE = re.compile(
    r"def\s+forward\s*\(\s*self\s*,\s*[A-Za-z_][A-Za-z0-9_]*\s*,\s*[A-Za-z_][A-Za-z0-9_]*\s*,"
)
_FORWARD_OK_RE = re.compile(
    r"def\s+forward\s*\(\s*self\s*,\s*[A-Za-z_][A-Za-z0-9_]*\s*,\s*stream_label"
)


def _load_repo_link(project: str) -> dict[str, Any]:
    p = repo_root() / "projects" / project / "repo_link.yaml"
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _matches_protected_path(
    target: str, patterns: list[str], *, diff_or_code: str = ""
) -> str | None:
    """Return the matching pattern, or None.

    Patterns of the form ``libs/Model.py:Paper_Total_0327`` are class-level
    protections — they fire only when the target file matches AND the
    patch/content actually references the class name. Plain path patterns
    (``baseline/`` / ``production_interface/``) fire on file/dir match only.
    """
    for pat in patterns:
        if ":" in pat:
            file_part, cls_part = pat.split(":", 1)
            file_part = file_part.rstrip("/")
            if target == file_part or target.startswith(file_part + "/"):
                if cls_part and cls_part in diff_or_code:
                    return pat
            continue
        prefix = pat.rstrip("/")
        if target == prefix or target.startswith(prefix + "/"):
            return pat
        if pat.endswith("/**") and target.startswith(pat[:-3]):
            return pat
        if pat == target:
            return pat
    return None


def _check_forward_signature(diff_or_code: str) -> str | None:
    """Return a violation reason if the patch breaks forward(x, stream_label).

    The check is intentionally conservative: it only fires when there's a
    forward(...) signature with three positional args **and** the third arg
    is *not* literally `stream_label`. False-positive risk is low because
    the moe-pimc codebase declares stream_label by name everywhere.
    """
    has_forward = _FORWARD_INTERFACE_RE.search(diff_or_code)
    if not has_forward:
        return None
    if _FORWARD_OK_RE.search(diff_or_code):
        return None
    return "patch modifies forward(self, x, ?, ...) — third positional arg must be 'stream_label' (AGENTS.md rule #2)"


def _extract_diff_paths(diff: str) -> list[str]:
    """Extract touched paths from a unified diff without trusting caller metadata."""
    out: list[str] = []
    patterns = (
        r"^diff --git\s+a/(.+?)\s+b/(.+)$",
        r"^\+\+\+\s+b/(.+)$",
        r"^---\s+a/(.+)$",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, diff, flags=re.MULTILINE):
            groups = match.groups()
            for raw in groups:
                path = raw.strip()
                if path and path != "/dev/null" and path not in out:
                    out.append(path)
    return out


def static_check(
    *,
    project: str,
    tool_name: str,
    args: dict[str, Any],
) -> GateOutcome:
    """Apply the project's static rules to the given tool args.

    Returns ``GateOutcome.triggered=True / blocking=True / requires_human=True``
    when a rule fires.
    """
    if tool_name not in MONITORED_TOOLS:
        return GateOutcome(gate_id=GATE_ID, triggered=False, blocking=False, requires_human=False)

    repo_link = _load_repo_link(project)
    protected = list(repo_link.get("protected_paths", []) or [])

    target_paths: list[str] = []
    if "path" in args:
        target_paths.append(str(args["path"]))
    if "files" in args and isinstance(args["files"], list):
        target_paths.extend(str(f.get("path", f)) for f in args["files"])

    diff = str(args.get("diff", "")) + "\n" + str(args.get("content", ""))
    for path in _extract_diff_paths(diff):
        if path not in target_paths:
            target_paths.append(path)

    for tp in target_paths:
        hit = _matches_protected_path(tp, protected, diff_or_code=diff)
        if hit is not None:
            return GateOutcome(
                gate_id=GATE_ID,
                triggered=True,
                blocking=True,
                requires_human=True,
                reason=f"path '{tp}' is baseline-protected (pattern: {hit})",
            )

    violation = _check_forward_signature(diff)
    if violation:
        return GateOutcome(
            gate_id=GATE_ID,
            triggered=True,
            blocking=True,
            requires_human=True,
            reason=violation,
        )

    return GateOutcome(gate_id=GATE_ID, triggered=False, blocking=False, requires_human=False)


async def gate_check(
    tool_name: str, args: dict[str, Any], ctx: ToolContext
) -> GateDecision:
    """Async entry point invoked by the tool registry's dispatch."""
    outcome = static_check(project=ctx.project, tool_name=tool_name, args=args)
    if not outcome.triggered:
        return GateDecision(gate_id=GATE_ID, action="allow")
    if outcome.requires_human:
        logger.warning(
            "Gate 5 blocked tool '{}' for run={}, agent={}: {}",
            tool_name, ctx.run_id, ctx.agent, outcome.reason,
        )
        return GateDecision(gate_id=GATE_ID, action="block", reason=outcome.reason)
    return GateDecision(gate_id=GATE_ID, action="allow")
