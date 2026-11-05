"""Evolution story: the Step1 research round that produced a route, then that route's rounds, for the animated replay.

Reads only mars.api (through replay.build_replay) and the run's own artifacts under runs/<id>/: step1/r*/ files,
step2/<route>/i*/record.json, events.jsonl and llm_calls.jsonl (for the screening picks, which no other artifact
keeps in full). Every field is optional in the source files, so missing artifacts give empty parts, never errors.
Pure functions; the caller passes the loaded `api` module and the run directory.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from mars_app import artifacts, replay


def _load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _short(text: Any, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _rebuttal_kind(response: str) -> str:
    head = (response or "").lstrip()[:8]
    if re.match(r"(完全)?接受", head) or head.lower().startswith("accept"):
        return "accept"
    if head.startswith("部分") or head.lower().startswith("partial"):
        return "partial"
    return "rebut"


def screening_picks(run_dir: Path, source_ids: set[str], kept: list[str]) -> list[dict[str, str]]:
    """The screening model's full selection for a round (before the budget cut), from llm_calls.jsonl.

    The right call is the one whose selection is all from this round's sources and whose first picks are the
    sources that became evidence cards, in order. Falls back to the kept sources alone."""
    path = Path(run_dir) / "llm_calls.jsonl"
    found: list[dict[str, str]] = []
    try:
        handle = path.open(encoding="utf-8")
    except OSError:
        return found
    with handle:
        for line in handle:
            if '"step1.screen"' not in line:
                continue
            try:
                call = json.loads(line)
            except ValueError:
                continue
            if call.get("tag") != "step1.screen" or not call.get("ok"):
                continue
            output = call.get("output")
            try:
                output = json.loads(output) if isinstance(output, str) else output
            except ValueError:
                continue
            selected = [s for s in (output or {}).get("selected") or [] if isinstance(s, dict)]
            ids = [s.get("source_id") for s in selected]
            valid = [i for i in ids if i in source_ids]
            if selected and len(valid) == len(ids) and valid[:len(kept)] == kept:
                found = [{"source_id": s["source_id"], "question_id": s.get("question_id", "")} for s in selected]
    return found


def step1_story(run_dir: Path, round_name: str, focus: str | None = None) -> dict[str, Any] | None:
    """One Step1 round: questions → queries → engines → merge → screening → cards → proposals, critiques, scores."""
    d = Path(run_dir) / "step1" / round_name
    if not d.is_dir():
        return None
    analysis = _load(d / "analysis.json", {}) or {}
    log = _load(d / "search_log.json", []) or []
    sources = _load(d / "sources.json", []) or []
    cards = _load(d / "cards.json", []) or []
    ranking = _load(d / "ranking.json", []) or []
    judged = _load(d / "judged_proposals.json") or _load(d / "debate.json", []) or []
    handoff = [h.get("id") for h in _load(d / "handoff.json", []) or [] if isinstance(h, dict)]
    budget = (artifacts.contract(run_dir).get("budget") or {}).get("research_sources")

    rqs = [{"id": q.get("id", ""), "question": _short(q.get("question"), 80),
            "paper_queries": q.get("paper_queries") or [], "web_queries": q.get("web_queries") or []}
           for q in analysis.get("research_questions") or []]
    engines: list[str] = []
    queries: list[str] = []
    for e in log:
        if e.get("engine") not in engines:
            engines.append(e.get("engine"))
        if e.get("query") not in queries and e.get("engine") != "web":
            queries.append(e.get("query"))
    hits = {(e.get("engine"), e.get("query")): (None if "error" in e else e.get("hits", 0)) for e in log}

    proposals = [j.get("proposal") or {} for j in judged]
    focus = focus if focus in {p.get("id") for p in proposals} else (handoff[0] if handoff else (ranking[0]["id"] if ranking else None))
    focus_prop = next((p for p in proposals if p.get("id") == focus), {})
    cited = {e.get("id") for e in focus_prop.get("evidence") or [] if isinstance(e, dict)}
    rq_votes = Counter(c.get("question_id") for c in cards if c.get("id") in cited)
    focus_rq = next((q for q in rqs if q["id"] == (rq_votes.most_common(1)[0][0] if rq_votes else None)), rqs[0] if rqs else None)
    example = (focus_rq or {}).get("paper_queries", [None])[0] if focus_rq and focus_rq["paper_queries"] else (queries[0] if queries else None)
    lanes = [{"engine": eng, "hits": hits.get((eng, example)),
              "titles": [_short(s.get("title"), 90) for s in sources if s.get("query") == example and s.get("source") == eng][:3]}
             for eng in engines if eng != "web"] if example else []

    ids = [s.get("id") for s in sources]
    kept = [c.get("source_id") for c in cards]
    picks = screening_picks(run_dir, set(ids), kept) or [{"source_id": c.get("source_id"), "question_id": c.get("question_id", "")} for c in cards]
    index = {sid: i for i, sid in enumerate(ids)}

    order = [p.get("id") for p in proposals]
    out_props = []
    for i, j in enumerate(judged):
        p = j.get("proposal") or {}
        r = next((x for x in ranking if x.get("id") == p.get("id")), {})
        out_props.append({
            "id": p.get("id"), "title": _short(p.get("title"), 60), "original_title": _short((j.get("original") or {}).get("title"), 60),
            "score": r.get("score"), "judge_scores": [round(s, 2) for s in r.get("judge_scores") or []],
            "precheck": r.get("precheck") or j.get("precheck") or [], "approved": p.get("id") in handoff,
            "peer": order[(i + 1) % len(order)] if len(order) > 1 else None,
            "critiques": [{"role": c.get("role"), "verdict": c.get("verdict"),
                           "issues": [{"severity": x.get("severity"), "criterion": _short(x.get("criterion"), 16), "text": _short(x.get("issue"), 70)}
                                      for x in c.get("issues") or []]} for c in j.get("critiques") or []],
            "rebuttals": [{"kind": _rebuttal_kind(b.get("response", "")), "issue": _short(b.get("issue"), 60), "response": _short(b.get("response"), 70)}
                          for b in j.get("rebuttals") or []],
        })
    return {
        "round": round_name, "focus": focus, "focus_rq": focus_rq["id"] if focus_rq else None,
        "directions": [{"id": x.get("id"), "name": _short(x.get("name"), 40)} for x in analysis.get("directions") or []],
        "questions": rqs, "engines": engines, "queries": queries,
        "hits": {eng: [hits.get((eng, q)) for q in queries] for eng in engines if eng != "web"},
        "web_queries": [{"query": e.get("query"), "hits": e.get("hits")} for e in log if e.get("engine") == "web"],
        "requests": len(log), "hits_total": sum(e.get("hits", 0) for e in log if "error" not in e), "sources": len(sources),
        "example_query": example, "lanes": lanes, "budget": budget,
        "picks": [{"index": index.get(x["source_id"], -1), "source_id": x["source_id"], "question_id": x["question_id"]} for x in picks],
        "cards": [{"id": c.get("id"), "title": _short(c.get("title"), 70), "question_id": c.get("question_id"),
                   "full_text": bool(c.get("full_text")), "verified": bool(c.get("verified"))} for c in cards],
        "proposals": out_props, "approved": handoff,
    }


def human_events(run_dir: Path) -> list[dict[str, Any]]:
    """Researcher interventions: operator entries and human approvals from events.jsonl."""
    out = []
    try:
        lines = (Path(run_dir) / "events.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("stage") == "operator" or e.get("approved_by") == "human":
            out.append({"ts": e.get("ts"), "message": e.get("message", ""), "approve": e.get("approve") or e.get("routes") or [],
                        "kind": "approval" if e.get("approved_by") == "human" or e.get("approve") else "operator"})
    return out


def round_of(run_dir: Path, route: str) -> str | None:
    for path in sorted((Path(run_dir) / "step1").glob("r*/ranking.json")):
        if any(r.get("id") == route for r in _load(path, []) or []):
            return path.parent.name
    return None


def _ladder(record: dict[str, Any]) -> dict[str, Any]:
    ev = record.get("evidence") or {}
    pq = ev.get("prequick") or {}
    screen = ev.get("screen") or {}
    verify = ev.get("verify") or {}
    return {
        "implemented": (record.get("implementation") or {}).get("ok"),
        "prequick": None if not pq else bool(pq.get("passed")),
        "screen": {k: bool(v.get("passed")) for k, v in screen.items() if isinstance(v, dict) and "passed" in v},
        "verify": {k: bool(v.get("passed")) for k, v in verify.items() if isinstance(v, dict) and "passed" in v},
    }


def build_evolution(api: Any, run_dir: Path, run_id: str, route: str | None = None, iteration: int | None = None,
                    project_root: str | Path | None = None) -> dict[str, Any]:
    run_dir = Path(run_dir)
    lineage = replay.build_replay(api, run_dir, run_id, route=route, iteration=iteration, project_root=project_root)
    targets = artifacts.contract(run_dir).get("targets") or {}
    rounds: list[dict[str, Any]] = []
    if lineage:
        for step in lineage["steps"]:
            record = _load(run_dir / "step2" / lineage["route"] / f"i{step['iteration']}" / "record.json", {}) or {}
            task = record.get("task") or {}
            decision = record.get("decision") or {}
            ev = record.get("evidence") or {}
            tensors = next((d["result"]["tensors"] for d in ev.get("auto_diagnostics") or []
                            if isinstance((d.get("result") or {}).get("tensors"), list)), [])
            rounds.append({**step,
                           "summary": _short(task.get("summary"), 160),
                           "assessment": _short(decision.get("mechanism_assessment") or step.get("judgement"), 150),
                           "checks": step.get("check_gates") or {},
                           "tensors": [{"name": c.get("name"), "shape": c.get("shape") or [], "rank": c.get("effective_rank")}
                                       for c in tensors if isinstance(c, dict)],
                           "ladder": _ladder(record)})
    route_name = lineage["route"] if lineage else None
    if route_name:
        round_name = round_of(run_dir, route_name)
    else:
        found = sorted((run_dir / "step1").glob("r*/ranking.json"))
        round_name = found[-1].parent.name if found else None
    return {
        "run_id": run_id, "route": route_name, "iteration": lineage["iteration"] if lineage else None,
        "routes": lineage["routes"] if lineage else [],
        "gates": targets.get("gates") or [], "check_gates": targets.get("checks") or [],
        "step1": step1_story(run_dir, round_name, focus=route_name) if round_name else None,
        "rounds": rounds, "researcher": human_events(run_dir),
    }
