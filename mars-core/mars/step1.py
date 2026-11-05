"""Step 1 — proposal generation: analysis → literature research → independent proposals → debate → judging → handoff.

Each stage writes its artifact under step1/r{round}/ and is skipped when the artifact exists (resumable).
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from mars.knowledge import tokenize
from mars.research import Library, quote_found
from mars.run import Run, render
from mars.schemas import Critique, EvidenceCard, Judgement, ProblemAnalysis, Proposal, Revision, Screening

CRITERIA = ("innovation", "evidence", "constraints_feasible", "mechanism_clarity", "history_distinct")


def _dir(round_: int) -> str:
    return f"step1/r{round_}"


def baseline_facts(run: Run) -> dict[str, Any]:
    """Measured facts about the bound baseline: the project's measure-time checks and cached baseline evaluations."""
    facts: dict[str, Any] = {"checks": run.kernel.baseline_facts()}
    local = {}
    for result in run.kernel.evals.glob("baseline-*/d*/mars_result.json"):
        item = json.loads(result.read_text(encoding="utf-8"))
        if item.get("ok") and item.get("binding_id") == run.kernel.context.binding["id"]:
            local[f"{item['dataset']} steps={item['steps'] or 'full'} seed={item['seed']}"] = item["metrics"]
    if local:
        facts["baseline_evaluations"] = local
    return facts


def _kb_excerpts(run: Run, queries: list[str], limit: int = 14) -> list[dict[str, str]]:
    return [{"id": c["id"], "source": Path(c["source"]).name, "kind": c["kind"], "title": c["title"],
             "text": c["text"][:900]} for c in run.kb.search_many(queries, k=4, limit=limit)]


def _common_context(run: Run) -> dict[str, Any]:
    cfg = run.kernel.cfg
    return {"contract": run.contract, "baseline_code": run.kernel.baseline_code(),
            "domain": cfg["prompts"].get("domain") or "（项目未提供领域说明，请依据基线代码、背景资料与研究约定理解问题）",
            "metrics": run.kernel.metrics, "lessons": run.reference(),
            "baseline_facts": baseline_facts(run), "goal": goal(run)}


def goal(run: Run) -> str:
    """The research goal in words, from the objective and the contract gates."""
    targets, fields = run.contract.get("targets") or {}, run.kernel.metrics["fields"]
    parts = [run.contract.get("objective") or run.kernel.cfg["objective"] or "（未写目标）"]
    for gate in targets.get("gates") or [{"metric": run.kernel.metrics["primary"], "max_worse": 0.0}]:
        meta, limit = fields[gate["metric"]], float(gate.get("max_worse", 0.0))
        name = f"{meta.get('label', gate['metric'])}（{gate['metric']}，{'越低越好' if meta['better'] == 'lower' else '越高越好'}）"
        parts.append(f"完整验收：{name}" + (f" 至少比基线好 {-limit:g}{meta.get('unit', '')}" if limit < 0 else
                                        f" 不比基线差超过 {limit:g}{meta.get('unit', '')}" if limit > 0 else " 不劣于基线"))
    for gate in targets.get("checks") or []:
        bounds = "，".join(f"{k} {v}" for k, v in (("≤", gate.get("max")), ("≥", gate.get("min"))) if v is not None)
        parts.append(f"检查 {gate['check']}.{gate['field']} {bounds}" + ("（不满足则不训练）" if gate.get("stop") else ""))
    return "；".join(parts)


def analyze(run: Run, round_: int, feedback: str = "") -> ProblemAnalysis:
    target = f"{_dir(round_)}/analysis.json"
    if run.has(target):
        return ProblemAnalysis.model_validate(run.load(target))
    contract = run.contract
    queries = [contract.get("objective", ""), *contract.get("constraints", []), "实验 结论 失败 原因 归因", "结果 对比 基线"]
    excerpts = _kb_excerpts(run, queries, limit=18)
    run.save(f"{_dir(round_)}/analysis_kb_context.json", excerpts)
    prompt = render("analyze", **_common_context(run), kb_excerpts=excerpts,
                    feedback=feedback or "（第一轮，无）")
    analysis = run.llm.structured("strong", [{"role": "user", "content": prompt}], ProblemAnalysis, tag="step1.analyze")
    valid = {c["id"] for c in excerpts}
    for lesson in analysis.history_lessons:
        lesson.kb_ids = [i for i in lesson.kb_ids if i in valid]
    run.save(target, analysis)
    run.event("step1", f"问题分析完成：{len(analysis.directions)} 个方向、{len(analysis.research_questions)} 个调研问题")
    return analysis


def pick_sources(selected: list[Any], valid_ids: set[str], limit: int) -> list[Any]:
    """Keep the budget without starving a research question: first pick per question, then the rest in model order."""
    seen: set[str] = set()
    picks = []
    for sel in selected:
        if sel.source_id in valid_ids and sel.source_id not in seen:
            seen.add(sel.source_id)
            picks.append(sel)
    keep: set[int] = set()
    covered: set[str] = set()
    for index, sel in enumerate(picks):
        if sel.question_id not in covered and len(keep) < limit:
            covered.add(sel.question_id)
            keep.add(index)
    for index in range(len(picks)):
        if len(keep) >= limit:
            break
        keep.add(index)
    return [sel for index, sel in enumerate(picks) if index in keep]


def research(run: Run, round_: int, analysis: ProblemAnalysis) -> list[dict[str, Any]]:
    target = f"{_dir(round_)}/cards.json"
    if run.has(target):
        return run.load(target)
    library = Library(run.kernel.workspace / "cache/literature")
    questions = analysis.research_questions
    paper_queries = [q for rq in questions for q in rq.paper_queries]
    web_queries = [q for rq in questions for q in rq.web_queries]
    if run.has(f"{_dir(round_)}/sources.json"):
        sources = run.load(f"{_dir(round_)}/sources.json")
        library.log = run.load(f"{_dir(round_)}/search_log.json") if run.has(f"{_dir(round_)}/search_log.json") else []
    else:
        sources = library.search(paper_queries, web_queries)
        run.save(f"{_dir(round_)}/sources.json", sources)
        run.save(f"{_dir(round_)}/search_log.json", library.log)
        engines = sorted({e["engine"] for e in library.log if e.get("hits")})
        failed = sum(1 for e in library.log if "error" in e)
        if not engines and library.sources.get("on_unavailable") == "fail":
            raise RuntimeError("所有调研来源都不可用（见 search_log.json），sources.yaml 要求 on_unavailable: fail")
        run.event("step1", f"检索完成：{len(paper_queries)} 条论文查询、{len(web_queries)} 条网页查询，去重后 {len(sources)} 个来源"
                           f"（有结果的引擎 {engines}，失败请求 {failed}）")
    listing = [{"id": s["id"], "source": s["source"], "year": s["year"], "title": s["title"],
                "abstract": s.get("abstract", "")[:450]} for s in sources]
    limit = int(run.contract["budget"]["research_sources"])
    screening = run.llm.structured("fast", [{"role": "user", "content": render(
        "screen", problem=goal(run), questions=[q.model_dump() for q in questions], sources=listing, limit=str(limit))}],
        Screening, tag="step1.screen")
    by_id = {s["id"]: s for s in sources}
    chosen = pick_sources(screening.selected, set(by_id), limit)
    run.save(f"{_dir(round_)}/screening.json", {**screening.model_dump(), "limit": limit,
                                                "kept": [sel.source_id for sel in chosen]})
    if len(chosen) < len(screening.selected):
        run.event("step1", f"筛选选出 {len(screening.selected)} 个来源，按预算 {limit} 保留 {len(chosen)} 个，"
                           f"覆盖调研问题 {sorted({sel.question_id for sel in chosen})}")
    question_text = {q.id: q.question for q in questions}

    def read(selection: Any) -> dict[str, Any]:
        source = by_id[selection.source_id]
        text = library.fetch(source)
        if len(text) < 400:
            return {"source_id": source["id"], "title": source["title"], "error": "无可阅读的正文或摘要", "verified": False}
        try:
            card = run.llm.structured("fast", [{"role": "user", "content": render(
                "card", problem=goal(run), question=question_text.get(selection.question_id, ""), title=source["title"],
                url=source.get("url", ""), text=text[:14000])}], EvidenceCard, tag="step1.card")
        except RuntimeError as error:
            return {"source_id": source["id"], "error": str(error)[:300], "verified": False}
        return {**card.model_dump(), "source_id": source["id"], "title": source["title"], "url": source.get("url", ""),
                "question_id": selection.question_id, "full_text": source.get("full_text", False),
                "verified": quote_found(card.quote, text)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        cards = list(pool.map(read, chosen))
    for index, card in enumerate(cards):
        card["id"] = f"E{index:02d}"
    run.save(f"{_dir(round_)}/research_log.json", library.log)
    run.save(target, cards)
    verified = sum(1 for c in cards if c.get("verified"))
    run.event("step1", f"精读 {len(cards)} 个来源，{verified} 张证据卡的原文引用核验通过")
    return cards


def _check_proposal(proposal: Proposal, run: Run, valid_ids: set[str]) -> list[str]:
    problems = []
    unknown = [e.id for e in proposal.evidence if e.id not in valid_ids]
    if unknown:
        problems.append(f"引用了不存在的证据：{unknown}")
    if not any(e.id.startswith("E") for e in proposal.evidence):
        problems.append("没有引用任何文献证据卡")
    if not any(e.id.startswith("K") for e in proposal.evidence):
        problems.append("没有引用任何项目知识/历史实验")
    return problems


def normalize_overrides(proposal: Proposal, run: Run) -> Proposal:
    """Training overrides outside the project's editable_config_keys cannot be applied; they are dropped."""
    allowed = set(run.kernel.cfg["editable_config_keys"])
    proposal.training_overrides = {k: v for k, v in proposal.training_overrides.items() if k in allowed}
    return proposal


def propose(run: Run, round_: int, analysis: ProblemAnalysis, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    target = f"{_dir(round_)}/proposals.json"
    if run.has(target):
        return run.load(target)
    verified_cards = [c for c in cards if c.get("verified")]
    directions = analysis.directions[: int(run.contract["budget"]["proposers"])]
    context = _common_context(run)

    def one(index_direction: tuple[int, Any]) -> dict[str, Any]:
        index, direction = index_direction
        excerpts = _kb_excerpts(run, [direction.name, direction.hypothesis_seed, direction.risks], limit=10)
        prompt = render("propose", **context, analysis=analysis.model_dump(), direction=direction.model_dump(),
                        cards=verified_cards, kb_excerpts=excerpts, proposal_id=f"P{round_}{index}")
        proposal = run.llm.structured("strong", [{"role": "user", "content": prompt}], Proposal, tag="step1.propose")
        proposal.id, proposal.direction_id = f"P{round_}{index}", direction.id
        normalize_overrides(proposal, run)
        valid = {c["id"] for c in verified_cards} | {c["id"] for c in excerpts} | {c["id"] for c in run.kb.chunks}
        return {"proposal": proposal.model_dump(), "kb_context": [c["id"] for c in excerpts],
                "precheck": _check_proposal(proposal, run, valid)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        proposals = list(pool.map(one, enumerate(directions)))
    run.save(target, proposals)
    run.event("step1", f"{len(proposals)} 个独立提案完成", ids=[p["proposal"]["id"] for p in proposals])
    return proposals


def _similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    ta = set(tokenize(a["title"] + a["mechanism_hypothesis"] + a["one_sentence"]))
    tb = set(tokenize(b["title"] + b["mechanism_hypothesis"] + b["one_sentence"]))
    return len(ta & tb) / max(1, len(ta | tb))


def debate(run: Run, round_: int, proposals: list[dict[str, Any]], cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    target = f"{_dir(round_)}/debate.json"
    if run.has(target):
        return run.load(target)
    context = _common_context(run)
    verified_cards = [c for c in cards if c.get("verified")]
    items = [p["proposal"] for p in proposals]

    def critique(pair: tuple[dict[str, Any], str, dict[str, Any] | None]) -> dict[str, Any]:
        proposal, role, peer = pair
        prompt = render("critique", **context, proposal=proposal, role=role,
                        peer=peer or {}, others=[{"id": o["id"], "title": o["title"], "one_sentence": o["one_sentence"]}
                                                 for o in items if o["id"] != proposal["id"]])
        result = run.llm.structured("strong", [{"role": "user", "content": prompt}], Critique, tag=f"step1.critique.{role}")
        result.proposal_id = proposal["id"]
        return {"role": role, **result.model_dump()}

    pairs = []
    for index, proposal in enumerate(items):
        peer = items[(index + 1) % len(items)] if len(items) > 1 else None
        pairs.append((proposal, "reviewer", None))
        if peer is not None:
            pairs.append((proposal, "peer", peer))
    if run.has(f"{_dir(round_)}/critiques.json"):
        critiques = run.load(f"{_dir(round_)}/critiques.json")
    else:
        with ThreadPoolExecutor(max_workers=3) as pool:
            critiques = list(pool.map(critique, pairs))
        run.save(f"{_dir(round_)}/critiques.json", critiques)
        run.event("step1", f"辩论第 1 轮：{len(critiques)} 份质询（审稿人 + 同行）")

    def revise(proposal: dict[str, Any]) -> dict[str, Any]:
        mine = [c for c in critiques if c["proposal_id"] == proposal["id"]]
        prompt = render("revise", **context, proposal=proposal, critiques=mine, cards=verified_cards)
        revision = run.llm.structured("strong", [{"role": "user", "content": prompt}], Revision, tag="step1.revise")
        revised = revision.proposal
        revised.id, revised.direction_id = proposal["id"], proposal["direction_id"]
        normalize_overrides(revised, run)
        valid = {c["id"] for c in verified_cards} | {c["id"] for c in run.kb.chunks}
        return {"original": proposal, "critiques": mine, "rebuttals": [r.model_dump() for r in revision.rebuttals],
                "proposal": revised.model_dump(), "precheck": _check_proposal(revised, run, valid)}

    with ThreadPoolExecutor(max_workers=3) as pool:
        revised = list(pool.map(revise, items))
    run.save(target, revised)
    run.event("step1", "提案者完成答辩与修订", precheck={r["proposal"]["id"]: r["precheck"] for r in revised})
    return revised


def judge(run: Run, round_: int, revised: list[dict[str, Any]]) -> list[dict[str, Any]]:
    target = f"{_dir(round_)}/ranking.json"
    if run.has(target):
        return run.load(target)
    context = _common_context(run)
    valid = {c["id"] for c in run.load(f"{_dir(round_)}/cards.json") if c.get("verified")} | {c["id"] for c in run.kb.chunks}
    for item in revised:  # re-derive prechecks with the current rules; debate.json stays as the debate wrote it
        proposal = normalize_overrides(Proposal.model_validate(item["proposal"]), run)
        item["proposal"], item["precheck"] = proposal.model_dump(), _check_proposal(proposal, run, valid)
    run.save(f"{_dir(round_)}/judged_proposals.json", revised)
    proposals = [r["proposal"] for r in revised]
    debates = [{"id": r["proposal"]["id"], "unresolved_fatal": [i["issue"] for c in r["critiques"] for i in c["issues"]
                                                               if i["severity"] == "fatal"],
                "rebuttals": r["rebuttals"]} for r in revised]
    scores: dict[str, list[float]] = {p["id"]: [] for p in proposals}
    judgements = {}
    for role in ("strong", "fast"):
        result = run.llm.structured(role, [{"role": "user", "content": render(
            "judge", **context, proposals=proposals, debates=debates)}], Judgement, tag=f"step1.judge.{role}")
        judgements[role] = result.model_dump()
        for score in result.scores:
            if score.proposal_id in scores:
                scores[score.proposal_id].append(sum(getattr(score, c) for c in CRITERIA) / len(CRITERIA))
    ranking = []
    for item in revised:
        pid = item["proposal"]["id"]
        values = scores.get(pid) or [0.0]
        ranking.append({"id": pid, "title": item["proposal"]["title"], "score": round(sum(values) / len(values), 2),
                        "judge_scores": values, "precheck": item["precheck"]})
    ranking.sort(key=lambda r: (bool(r["precheck"]), -r["score"]))
    for i, a in enumerate(ranking):
        for b in ranking[:i]:
            pa = next(r["proposal"] for r in revised if r["proposal"]["id"] == a["id"])
            pb = next(r["proposal"] for r in revised if r["proposal"]["id"] == b["id"])
            if _similarity(pa, pb) > 0.55:
                a["duplicate_of"] = b["id"]
    run.save(f"{_dir(round_)}/judgements.json", judgements)
    run.save(target, ranking)
    run.event("step1", "评审排序完成", ranking=[(r["id"], r["score"], len(r["precheck"])) for r in ranking])
    return ranking


def handoff(run: Run, round_: int, revised: list[dict[str, Any]], ranking: list[dict[str, Any]],
            approved_ids: list[str] | None = None) -> list[dict[str, Any]]:
    routes = int(run.contract["budget"]["routes"])
    eligible = [r["id"] for r in ranking if not r["precheck"] and "duplicate_of" not in r]
    if approved_ids is not None and (not approved_ids or len(set(approved_ids)) != len(approved_ids) or any(pid not in {r["id"] for r in ranking} for pid in approved_ids)):
        raise ValueError("人工确认只能包含当前轮次中不重复的方案 ID")
    chosen = approved_ids or eligible[:routes]
    packages = []
    for pid in chosen:
        item = next(r for r in revised if r["proposal"]["id"] == pid)
        packages.append({"id": pid, "round": round_, "proposal": item["proposal"], "rebuttals": item["rebuttals"],
                         "critiques": item["critiques"], "score": next(r["score"] for r in ranking if r["id"] == pid)})
    run.save(f"{_dir(round_)}/handoff.json", packages)
    cited = {e["id"] for item in revised for e in item["proposal"].get("evidence", [])} | \
        {i for item in revised for i in item.get("kb_context", [])}
    run.save(f"{_dir(round_)}/kb_cited.json", [c for c in run.kb.chunks if c["id"] in cited])  # what each K id meant
    lines = [f"# Step1 交接包（第 {round_} 轮）\n"]
    for package in packages:
        p = package["proposal"]
        lines += [f"## {p['id']} {p['title']}（评分 {package['score']}）", f"- 一句话：{p['one_sentence']}",
                  f"- 机制假设：{p['mechanism_hypothesis']}", f"- 改动的基线部分：{p['removed_or_shrunk']}",
                  f"- 补回的能力：{p['added_capability']}", f"- 资源估计：{p['resource_estimate']}",
                  f"- 约束论证：{p['constraints_argument']}",
                  f"- 与历史的区别：{p['history_relation']}", f"- 否定条件：{p['falsification']}",
                  "- 证据：" + "；".join(f"{e['id']} {e['how_used']}" for e in p["evidence"]), ""]
    run.save(f"{_dir(round_)}/handoff.md", "\n".join(lines))
    run.event("step1", f"交接包：{chosen}")
    return packages


REFINE_SCOPE = ("这是用户给出的方案（方案完善模式）：每轮改动只在该方案附近做小幅调整（初始化、规模、局部结构、训练配置），"
                "不更换核心机制；若核心机制被实测否定，判 refuted 或 stop 并说明原因。")


def structure_user_proposal(run: Run, text: str, proposal_id: str = "U00") -> list[dict[str, Any]]:
    """Refine-mode Step1: turn the user's own design into a Proposal (no research, no debate) and
    write the round-0 artifacts the approval/handoff nodes read."""
    target = f"{_dir(0)}/debate.json"
    if run.has(target):
        return run.load(target)
    excerpts = _kb_excerpts(run, [text[:400], "实验 结论 失败 原因 归因", "初始化 收敛"], limit=10)
    prompt = render("refine", **_common_context(run), kb_excerpts=excerpts, user_proposal=text, proposal_id=proposal_id)
    proposal = run.llm.structured("strong", [{"role": "user", "content": prompt}], Proposal, tag="step1.refine")
    proposal.id, proposal.direction_id = proposal_id, "user"
    normalize_overrides(proposal, run)
    valid = {c["id"] for c in run.kb.chunks}
    proposal.evidence = [e for e in proposal.evidence if e.id in valid]  # drop invented ids
    precheck: list[str] = []
    item = {"proposal": {**proposal.model_dump(), "refine_scope": REFINE_SCOPE}, "kb_context": [c["id"] for c in excerpts],
            "precheck": precheck, "critiques": [], "rebuttals": []}
    run.save(f"{_dir(0)}/proposals.json", [item])
    run.save(target, [item])
    run.save(f"{_dir(0)}/ranking.json", [{"id": proposal_id, "title": proposal.title, "score": "用户方案", "precheck": precheck}])
    run.event("step1", "用户方案已结构化", ids=[proposal_id], precheck=precheck)
    return [item]


def summarize_for_approval(ranking: list[dict[str, Any]], revised: list[dict[str, Any]]) -> str:
    rows = []
    for r in ranking:
        p = next(x["proposal"] for x in revised if x["proposal"]["id"] == r["id"])
        flags = "；".join(r["precheck"]) or "通过"
        rows.append(f"{r['id']} [{r['score']}] {p['title']} — {p['one_sentence']}（预检：{flags}）")
    return "\n".join(rows)
