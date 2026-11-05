import json
from pathlib import Path

from mars_app import evolution


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _round(run: Path) -> None:
    d = run / "step1" / "r0"
    _write(run / "contract.yaml", "budget:\n  research_sources: 2\n")
    _write(d / "analysis.json", {"directions": [{"id": "D1", "name": "方向一"}],
                                 "research_questions": [{"id": "RQ1", "question": "问题一", "paper_queries": ["q a"], "web_queries": ["w"]},
                                                        {"id": "RQ2", "question": "问题二", "paper_queries": ["q b"], "web_queries": []}]})
    _write(d / "search_log.json", [{"engine": "arxiv", "query": "q a", "hits": 2}, {"engine": "arxiv", "query": "q b", "hits": 0},
                                   {"engine": "web", "query": "w", "hits": 1}, {"engine": "openalex", "query": "q a", "error": "timeout"}])
    _write(d / "sources.json", [{"id": "S000", "source": "arxiv", "title": "A", "query": "q a"}, {"id": "S001", "source": "arxiv", "title": "B", "query": "q a"},
                                {"id": "S002", "source": "web", "title": "C", "query": "w"}])
    _write(d / "cards.json", [{"id": "E00", "source_id": "S001", "question_id": "RQ1", "title": "B", "verified": True, "full_text": True},
                              {"id": "E01", "source_id": "S000", "question_id": "RQ1", "title": "A", "verified": False, "full_text": False}])
    _write(d / "ranking.json", [{"id": "P00", "score": 7.0, "judge_scores": [7.0, 7.0], "precheck": []}])
    _write(d / "judged_proposals.json", [{"proposal": {"id": "P00", "title": "方案", "evidence": [{"id": "E00"}]}, "original": {"title": "原方案"},
                                          "critiques": [{"role": "reviewer", "verdict": "revise", "issues": [{"severity": "fatal", "criterion": "机制", "issue": "梯度断开"}]}],
                                          "rebuttals": [{"issue": "梯度断开", "response": "接受，改用功能卷积"}, {"issue": "x", "response": "不同意"}]}])
    _write(d / "handoff.json", [{"id": "P00"}])


def test_step1_story_from_round_files(tmp_path):
    _round(tmp_path)
    calls = [{"tag": "step1.screen", "ok": True, "output": json.dumps({"selected": [{"source_id": "S001", "question_id": "RQ1"},
                                                                                     {"source_id": "S000", "question_id": "RQ1"},
                                                                                     {"source_id": "S002", "question_id": "RQ2"}]})},
             {"tag": "step1.screen", "ok": True, "output": {"selected": [{"source_id": "S999", "question_id": "RQ1"}]}}]
    _write(tmp_path / "llm_calls.jsonl", "\n".join(json.dumps(c) for c in calls))
    story = evolution.step1_story(tmp_path, "r0")
    assert story["focus"] == "P00" and story["focus_rq"] == "RQ1" and story["example_query"] == "q a"
    assert story["requests"] == 4 and story["hits_total"] == 3 and story["sources"] == 3 and story["budget"] == 2
    assert story["hits"]["arxiv"] == [2, 0] and story["hits"]["openalex"] == [None, None]
    assert [p["source_id"] for p in story["picks"]] == ["S001", "S000", "S002"]  # the selection beyond the budget is kept
    assert story["lanes"][0]["titles"] == ["A", "B"]
    prop = story["proposals"][0]
    assert prop["approved"] and prop["critiques"][0]["issues"][0]["severity"] == "fatal"
    assert [r["kind"] for r in prop["rebuttals"]] == ["accept", "rebut"]


def test_picks_fall_back_to_cards_without_llm_log(tmp_path):
    _round(tmp_path)
    story = evolution.step1_story(tmp_path, "r0")
    assert [p["source_id"] for p in story["picks"]] == ["S001", "S000"]
    assert evolution.step1_story(tmp_path, "r9") is None


def test_human_events_and_round_lookup(tmp_path):
    _round(tmp_path)
    _write(tmp_path / "events.jsonl", "\n".join(json.dumps(e, ensure_ascii=False) for e in [
        {"ts": "1", "stage": "step1", "message": "x"},
        {"ts": "2", "stage": "operator", "message": "预算调整"},
        {"ts": "3", "stage": "graph", "message": "进入 Step2", "routes": ["P00"], "approved_by": "human"}]) + "\nnot json")
    events = evolution.human_events(tmp_path)
    assert [e["kind"] for e in events] == ["operator", "approval"] and events[1]["approve"] == ["P00"]
    assert evolution.round_of(tmp_path, "P00") == "r0" and evolution.round_of(tmp_path, "P77") is None
