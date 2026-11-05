"""Evolution replay data: one candidate's lineage, round by round, mapped onto the modules each round names.

Reads only mars.api (route_summaries, iteration_diff) and the run's own record.json files, so it works
on a copied run without git or coding-agent logs once the core serves iteration_diff from its cache.
Pure functions; the caller passes the loaded `api` module and the run directory.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from mars_app import artifacts

DEFAULT_MODULE = "改动"  # a round that names no module of its own
CONFIG_LINE = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*:\s*(\S.*?)\s*$")
HUNK_SYMBOL = re.compile(r"^@@[^@]*@@\s*(class|def)\s+(\w+)", re.M)


def _num(x: Any) -> float | None:
    return x if isinstance(x, (int, float)) else None


def _worst(pairs: list[float | None]) -> float | None:
    values = [p for p in pairs if p is not None]
    return round(max(values), 3) if values else None


def config_changes(diff: dict[str, Any], max_value: int = 40) -> list[dict[str, str | None]]:
    """`key: old -> new` from the YAML files of an iteration_diff; comments and long values are skipped."""
    changes: dict[str, dict[str, str | None]] = {}
    for f in diff.get("files") or []:
        if not str(f.get("path", "")).endswith((".yaml", ".yml")):
            continue
        for line in (f.get("patch") or "").splitlines():
            if line[:1] not in "+-" or line[:3] in ("+++", "---"):
                continue
            m = CONFIG_LINE.match(line[1:].split(" #")[0])
            if not m or len(m[2]) > max_value or m[2].startswith(("\"", "'", "|", ">")):
                continue
            entry = changes.setdefault(m[1], {"key": m[1], "old": None, "new": None})
            entry["new" if line[0] == "+" else "old"] = m[2]
    return [c for c in changes.values() if c["old"] != c["new"]]


def code_files(diff: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for f in diff.get("files") or []:
        patch = (f.get("patch") or "").splitlines()
        out.append({"path": f.get("path"), "added": sum(1 for l in patch if l.startswith("+") and not l.startswith("+++")),
                    "removed": sum(1 for l in patch if l.startswith("-") and not l.startswith("---"))})
    return out


def code_symbols(diff: dict[str, Any], limit: int = 4) -> list[str]:
    """Classes and functions touched by the code changes, from the diff hunk headers."""
    names = []
    for f in diff.get("files") or []:
        for m in HUNK_SYMBOL.finditer(f.get("patch") or ""):
            if f"{m[1]} {m[2]}" not in names:
                names.append(f"{m[1]} {m[2]}")
    return names[:limit] + ([f"等 {len(names)} 处"] if len(names) > limit else [])


def _first_sentence(text: str, limit: int = 140) -> str:
    head = re.split(r"(?<=[。；])", text.strip(), maxsplit=1)[0] if text else ""
    return head if len(head) <= limit else head[:limit - 1] + "…"


def _step(it: dict[str, Any], record: dict[str, Any], diff: dict[str, Any]) -> dict[str, Any]:
    screen = it.get("screen") or {}
    verify = it.get("verify") or {}
    # the round's concrete changes (config keys, else the touched classes/functions) under the first module the
    # proposal names; modules are the proposal's own words, so the view has no fixed taxonomy
    tags = [str(t) for t in (record.get("task") or {}).get("modules") or [] if t] or [DEFAULT_MODULE]
    lines = [f"{c['key']}: {c['old']} → {c['new']}" if c["old"] and c["new"] else f"{c['key']}: {c['new'] or '（删除）'}"
             for c in config_changes(diff)] or code_symbols(diff) or [f["path"] for f in code_files(diff)]
    modules = {tags[0]: lines} if lines else {}
    evidence = record.get("evidence") or {}
    return {
        "iteration": it["iteration"], "parent": it.get("parent_iteration"), "change": it.get("change", ""),
        "modules": modules,
        "files": code_files(diff),
        "check_gates": it.get("check_gates") or {},
        "prequick_passed": (it.get("prequick") or {}).get("passed"),
        "screen_gap": {d: _num(((screen.get(d) or {}).get("worse_by") or {}).get((screen.get(d) or {}).get("primary"))) for d in sorted(screen)},
        "verify_gap": {d: _worst([_num((v.get("worse_by") or {}).get(m)) for m in (v.get("gates") or {})])
                       for d, v in verify.items() if isinstance(v, dict)},
        "stopped_at": it.get("stopped_at"), "decision": it.get("decision"),
        "judgement": _first_sentence(it.get("assessment", "")),
        "plots": evidence.get("plots") or {},
    }


def build_replay(api: Any, run_dir: Path, run_id: str, route: str | None = None, iteration: int | None = None,
                 project_root: str | Path | None = None) -> dict[str, Any] | None:
    """Lineage of one iteration (default: the run's best full verification, else best screen) from the baseline."""
    summaries = api.route_summaries(run_id, project_root=project_root)
    candidates = [(s["route"], it) for s in summaries for it in s["iterations"] if route in (None, s["route"])]
    if not candidates:
        return None
    if iteration is None:
        targets = artifacts.contract(run_dir).get("targets")
        datasets = sorted({d for _, it in candidates for d in it.get("screen", {})})
        route, chosen = min(candidates, key=lambda c: artifacts.iteration_score(c[1], targets, datasets))
    else:
        route, chosen = next(((r, it) for r, it in candidates if it["iteration"] == iteration), (None, None))
        if chosen is None:
            return None
    by_k = {it["iteration"]: it for s in summaries if s["route"] == route for it in s["iterations"]}
    chain, k = [], chosen["iteration"]
    while k in by_k:
        chain.append(by_k[k])
        k = by_k[k].get("parent_iteration") or 0
    steps = []
    for it in reversed(chain):
        path = Path(run_dir) / "step2" / route / f"i{it['iteration']}" / "record.json"
        record = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        try:
            diff = api.iteration_diff(run_id, route, it["iteration"], project_root=project_root)
        except Exception:  # diff not available for this round (no cache, no repository): show the round without it
            diff = {}
        steps.append(_step(it, record, diff))
    return {"run_id": run_id, "route": route, "iteration": chosen["iteration"], "steps": steps,
            "routes": [{"route": s["route"], "iterations": [it["iteration"] for it in s["iterations"]]} for s in summaries]}
