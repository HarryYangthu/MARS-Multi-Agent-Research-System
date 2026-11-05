"""Top-level research graph (LangGraph): Step1 → approval → Step2 rounds → (back to Step1 | finish).
Refine mode (mode="refine") replaces Step1 with structuring the user's own design and never returns to Step1.

State holds only small identifiers; artifacts live in the run directory, so every node is resumable and the
SQLite checkpointer can restart the graph after interruptions or crashes.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from mars import step1
from mars.report import write_report
from mars.run import Run
from mars.step2 import Evolver


class State(TypedDict, total=False):
    round: int
    routes: list[dict[str, Any]]
    approved: list[str]
    feedback: str
    outcome: str
    auto_approve: bool
    mode: str   # "rsi" (default) or "refine"


def _run(config: RunnableConfig) -> Run:
    return Run.open(Path(config["configurable"]["run_dir"]), project_root=config["configurable"].get("project_root"), environment=config["configurable"].get("environment"))


def node_step1(state: State, config: RunnableConfig) -> State:
    run, round_ = _run(config), state.get("round", 0)
    run.event("graph", f"Step1 第 {round_} 轮开始")
    analysis = step1.analyze(run, round_, state.get("feedback", ""))
    cards = step1.research(run, round_, analysis)
    proposals = step1.propose(run, round_, analysis, cards)
    revised = step1.debate(run, round_, proposals, cards)
    step1.judge(run, round_, revised)
    return {}


def node_refine(state: State, config: RunnableConfig) -> State:
    run = _run(config)
    run.event("graph", "方案完善模式：结构化用户方案")
    step1.structure_user_proposal(run, run.path("step1/r0/user_proposal.md").read_text(encoding="utf-8"))
    return {}


def entry(state: State) -> str:
    return "refine" if state.get("mode") == "refine" else "step1"


def node_approve(state: State, config: RunnableConfig) -> State:
    run, round_ = _run(config), state.get("round", 0)
    ranking = run.load(f"step1/r{round_}/ranking.json")
    judged = f"step1/r{round_}/judged_proposals.json"
    revised = run.load(judged if run.has(judged) else f"step1/r{round_}/debate.json")
    if run.contract.get("human_approval", True) and not state.get("auto_approve"):
        answer = interrupt({"question": "请确认进入 Step2 的方案（填写方案 id 列表）",
                            "ranking": step1.summarize_for_approval(ranking, revised)})
        approved = [str(a) for a in answer] if answer else None
    else:
        approved = None
    packages = step1.handoff(run, round_, revised, ranking, approved)
    evolver = Evolver(run)
    run.event("graph", "进入 Step2", routes=[p["id"] for p in packages], approved_by="human" if approved else "judges")
    return {"routes": [evolver.new_route(p) for p in packages], "approved": [p["id"] for p in packages]}


def node_evolve(state: State, config: RunnableConfig) -> State:
    run = _run(config)
    routes = Evolver(run).round(state["routes"])
    return {"routes": routes}


def after_evolve(state: State, config: RunnableConfig) -> str:
    routes = state["routes"]
    if any(r["status"] == "success" for r in routes):
        return "finish"
    if any(r["status"] == "active" for r in routes):
        return "evolve"
    if state.get("mode") == "refine":
        return "finish"
    run = _run(config)
    if state.get("round", 0) + 1 < int(run.contract["budget"]["max_step1_rounds"]):
        return "restart"
    return "finish"


def node_restart(state: State, config: RunnableConfig) -> State:
    run = _run(config)
    summary = []
    for route in state["routes"]:
        last = route["history"][-1] if route["history"] else {}
        summary.append({"route": route["id"], "title": route["proposal"]["title"], "status": route["status"],
                        "iterations": route["iteration"], "best": route.get("best"),
                        "last_assessment": last.get("decision", {}).get("mechanism_assessment", "")[:600]})
    feedback = "上一轮 Step2 各路线均未达标，结果如下；请据此调整方向，避免重复被否定的机制：\n" + json.dumps(summary, ensure_ascii=False, indent=1)
    run.event("graph", "所有路线未达标，回到 Step1 重新提方案")
    return {"round": state.get("round", 0) + 1, "feedback": feedback, "routes": []}


def node_finish(state: State, config: RunnableConfig) -> State:
    run = _run(config)
    success = next((r for r in state.get("routes", []) if r["status"] == "success"), None)
    outcome = "success" if success else "not_reached"
    run.save("step2/routes_final.json", state.get("routes", []))
    write_report(run, state.get("routes", []), outcome)
    run.event("graph", f"结束：{outcome}")
    return {"outcome": outcome}


def build(run_dir: Path) -> tuple[Any, sqlite3.Connection]:
    graph = StateGraph(State)
    graph.add_node("step1", node_step1)
    graph.add_node("refine", node_refine)
    graph.add_node("approve", node_approve)
    graph.add_node("evolve", node_evolve)
    graph.add_node("restart", node_restart)
    graph.add_node("finish", node_finish)
    graph.add_conditional_edges(START, entry, {"step1": "step1", "refine": "refine"})
    graph.add_edge("step1", "approve")
    graph.add_edge("refine", "approve")
    graph.add_edge("approve", "evolve")
    graph.add_conditional_edges("evolve", after_evolve, {"evolve": "evolve", "restart": "restart", "finish": "finish"})
    graph.add_edge("restart", "step1")
    graph.add_edge("finish", END)
    connection = sqlite3.connect(run_dir / "checkpoint.sqlite", check_same_thread=False)
    return graph.compile(checkpointer=SqliteSaver(connection)), connection
