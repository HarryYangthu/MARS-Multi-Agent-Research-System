"""Unit tests for pure logic and real temporary files (no model, tool or training substitutes)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mars.knowledge import KnowledgeBase, tokenize
from mars.llm import extract_json, strip_thinking
from mars.research import quote_found
from mars.codingagent import PermissionPolicy

ROOT = Path(__file__).resolve().parents[1]


def test_strip_thinking_and_extract_json() -> None:
    assert strip_thinking("<think>plan</think>{\"a\": 1}") == "{\"a\": 1}"
    assert strip_thinking("reasoning...</think>{\"a\": 2}") == "{\"a\": 2}"
    assert extract_json("```json\n{\"x\": [1, 2]}\n```") == {"x": [1, 2]}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_tokenize_mixes_ascii_and_cjk_bigrams() -> None:
    tokens = tokenize("LUT 稀疏注意力 attention")
    assert "lut" in tokens and "attention" in tokens and "稀疏" in tokens and "注意" in tokens


def test_knowledge_search_ranks_matching_section(tmp_path: Path) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text("# 推理速度\n不能沿时间递归，推理不能比 FIR 慢。\n\n# 数据\n两套数据分别训练。\n")
    kb = KnowledgeBase([(doc, "domain")])
    hits = kb.search("推理 递归 速度", k=1)
    assert hits and hits[0]["title"] == "推理速度"


def test_quote_found_requires_verbatim_text() -> None:
    text = "We propose a dilated convolution that grows the receptive field exponentially without pooling."
    assert quote_found("a dilated convolution that grows the receptive field exponentially", text)
    assert not quote_found("a dilated convolution that shrinks the receptive field", text)
    assert not quote_found("short", text)


def test_permission_policy(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    policy = PermissionPolicy([repo], ["python3 ", "git status"])
    assert policy.decide({"input": {"file_path": str(repo / "a.py"), "content": "x"}})[0]
    assert not policy.decide({"input": {"file_path": "/etc/hosts", "content": "x"}})[0]
    assert policy.decide({"input": {"command": "python3 -c 'print(1)'"}})[0]
    assert not policy.decide({"input": {"command": "curl -s https://example.com"}})[0]
    assert not policy.decide({"input": {"command": "rm -rf build"}})[0]
    # paths echoed inside approval options must not cause a denial
    request = {"input": {"file_path": str(repo / "b.py")}, "options": [{"rules": [{"ruleContent": "/etc/passwd"}]}]}
    assert policy.decide(request)[0]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True)


def test_quote_found_tolerates_pdf_artifacts_not_paraphrase() -> None:
    pdf = "the convolution oper-\nation can be executed in the Fourier domain, where it scales signiﬁcantly better"
    assert quote_found("the convolution operation can be executed in the Fourier domain, where it scales significantly better", pdf)
    assert not quote_found("convolutions are computed with the FFT, which scales much better for big filters", pdf)


def test_permission_policy_segments(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    policy = PermissionPolicy([repo], ["python3 ", "git status"])
    assert policy.decide({"input": {"command": f'cd "{repo}" && python3 -c "print(1)"'}})[0]
    assert policy.decide({"input": {"command": "rm -f _scratch.py && git status --short"}})[0]
    assert not policy.decide({"input": {"command": "cd /etc && python3 -c 'print(1)'"}})[0]
    assert not policy.decide({"input": {"command": f'cd "{repo}" && git add -A'}})[0]
    assert not policy.decide({"input": {"command": "rm -f /etc/hosts"}})[0]


def test_permission_policy_unbalanced_quotes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    policy = PermissionPolicy([repo], ["python3 "])
    command = """python3 -c "import a; print('x && y')" && python3 -c 'print(1)'"""
    allowed, _ = policy.decide({"input": {"command": command}})
    assert isinstance(allowed, bool)


def test_permission_policy_chained_and_substituted_commands(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    policy = PermissionPolicy([repo], ["python3 ", "ls", "head "])
    assert not policy.decide({"input": {"command": "ls; rm -rf ~/x"}})[0]
    assert not policy.decide({"input": {"command": "ls | sh"}})[0]
    assert not policy.decide({"input": {"command": "python3 -c \"$(cat /etc/passwd)\""}})[0]
    assert policy.decide({"input": {"command": "python3 -c \"import a; print(1)\" | head -5"}})[0]


def test_api_status_phase_lock_and_partial_events(tmp_path: Path, monkeypatch) -> None:
    from mars import api, osutil
    monkeypatch.setenv("MARS_DEFAULT_PROJECT", str(tmp_path))
    run = tmp_path / "runs/demo"
    (run / "step1/r0").mkdir(parents=True)
    (run / "contract.yaml").write_text("title: demo\n")
    for name in ("ranking.json", "handoff.json"):
        (run / "step1/r0" / name).write_text("[]")
    (run / "step2/P00/i1").mkdir(parents=True)
    (run / "step1/r1").mkdir()
    (run / "step1/r1/ranking.json").write_text('[{"id": "P10", "score": 7}]')
    (run / "events.jsonl").write_text('{"ts": "t", "stage": "s", "message": "m"}\n{"ts": "t", "sta')
    state = api.status("demo")  # back in Step1 round 1 with old routes on disk: still an approval wait
    assert state["phase"] == "awaiting_approval" and state["running"] is False and state["pending_approval"][0]["id"] == "P10"
    assert len(api.events("demo")) == 1
    with (run / "run.lock").open("a+") as lock:
        osutil.lock(lock)
        lock.write("4242")
        lock.flush()
        state = api.status("demo")
        assert state["running"] is True and state["pid"] == 4242 and state["phase"] == "step1"
        try:
            api.resume("demo")
            raise AssertionError("resume must refuse a running run")
        except RuntimeError:
            pass
    (run / "step1/r1/handoff.json").write_text("[]")
    assert api.status("demo")["phase"] == "step2"
    try:
        api.start_run(tmp_path / "task.yaml", "demo")
        raise AssertionError("start_run must refuse an existing run id")
    except ValueError:
        pass


def test_refine_mode_routing_and_implement_status(tmp_path: Path, monkeypatch) -> None:
    from mars import api, graph
    assert graph.entry({"mode": "refine"}) == "refine" and graph.entry({}) == "step1"
    monkeypatch.setenv("MARS_DEFAULT_PROJECT", str(tmp_path))
    run = tmp_path / "runs/impl"
    (run / "implement").mkdir(parents=True)
    (run / "contract.yaml").write_text("title: t\n")
    state = api.status("impl")
    assert state["phase"] == "implementing" and state["kind"] == "implement" and state["implementation"] is None
    (run / "implement/result.json").write_text('{"ok": true, "candidate_dir": "/x"}')
    assert api.status("impl")["phase"] == "finished" and api.implementation("impl")["candidate_dir"] == "/x"
    staged = api._text_file("new", "spec", "build a smaller FIR")
    assert staged.read_text() == "build a smaller FIR"
    secret = tmp_path / "secret.txt"
    secret.write_text("do not read")
    assert api._text_file("new", "spec", str(secret)).read_text() == str(secret)  # a str is never opened
    assert api._text_file("new", "spec", secret).read_text() == "do not read"


def test_portability_hooks(tmp_path: Path, monkeypatch) -> None:
    """Windows paths and spaced paths are policy-checked; credentials never reach the agent; external sandbox hook."""
    from mars.llm import thinking_params
    from mars.codingagent import _absolute, runtime_environment, sandbox_prefix
    repo = tmp_path / "re po"
    repo.mkdir()
    policy = PermissionPolicy([repo], ["python3 "])
    assert policy.decide({"input": {"file_path": str(repo / "a b.py")}})[0]
    assert not policy.decide({"input": {"file_path": "/etc/new file.txt"}})[0]
    assert _absolute("C:\\Users\\x") and _absolute("D:/data") and not _absolute("src/model.py")
    monkeypatch.setenv("UPSTREAM_API_KEY", "secret")
    assert "UPSTREAM_API_KEY" not in runtime_environment(tmp_path / "rt", {})
    monkeypatch.setenv("MARS_SANDBOX_COMMAND", '["wrap"]')
    wrapped = sandbox_prefix([repo], 4242)
    assert wrapped[:2] == ["wrap", f"--write={repo.resolve()}"] and wrapped[-2:] == ["--port=4242", "--"]
    assert all(a.startswith("--deny-read=") for a in wrapped[2:-2])
    spec = {"thinking": False, "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    assert thinking_params(spec)["extra_body"] == {"thinking": {"type": "disabled"},
                                                   "chat_template_kwargs": {"enable_thinking": False}}


def test_source_registry_json_engine(tmp_path: Path) -> None:
    import json
    from mars.research import Library
    data = tmp_path / "hits.json"
    data.write_text(json.dumps({"data": {"records": [{"t": "Paper A", "doc": {"abs": "x"}, "y": 2024,
                                                       "link": "https://doi.org/10.1109/X.1"}]}}))
    sources = {"engines": {"arxiv": {"enabled": False}, "lib": {"kind": "json", "url": data.as_uri(), "items": "data.records",
                                                              "fields": {"title": "t", "abstract": "doc.abs", "year": "y", "url": "link"}}}}
    library = Library(tmp_path / "cache", sources)
    assert [name for name, _, _ in library.engines()] == ["lib"]
    hits = library.search(["q"], ["ignored by paper engines"])
    assert [(h["source"], h["title"], h["abstract"], h["year"], h["pdf_url"]) for h in hits] == [("lib", "Paper A", "x", "2024", "")]
    assert library.log == [{"engine": "lib", "query": "q", "hits": 1}]


def test_workbench_apis(tmp_path: Path, monkeypatch) -> None:
    import json
    from mars import api
    from mars.llm import token_usage
    from mars.workspace import running_job
    from mars.codingagent import summarize_log
    assert token_usage({"prompt_tokens": 14, "completion_tokens": 9, "prompt_tokens_details": {"cached_tokens": 2}}) == \
        {"input_tokens": 14, "output_tokens": 9, "cached_tokens": 2}
    assert token_usage(None) == {"input_tokens": None, "output_tokens": None, "cached_tokens": None}
    log = tmp_path / "codingagent.jsonl"
    event = lambda payload: json.dumps({"ts": 1.0, "kind": "event", "payload": {"method": "session/event", "params": {"payload": payload}}})
    log.write_text("\n".join([event({"kind": "tool_input_start", "toolCallId": "a", "toolName": "Edit"}),
                              event({"kind": "tool_call", "toolCallId": "a", "input": {"file_path": "/w/model.py"}}),
                              event({"kind": "tool_error", "toolCallId": "a"}),
                              json.dumps({"ts": 2.0, "kind": "permission", "payload": {"allowed": False}})]))
    summary = summarize_log(log)
    assert summary["tools"] == {"Edit": 1} and summary["tool_errors"] == 1 and summary["permissions"] == {"approved": 0, "denied": 1}
    assert summary["files_touched"] == ["/w/model.py"] and summary["ended"] == 2.0
    monkeypatch.setenv("MARS_DEFAULT_PROJECT", str(tmp_path))
    run = tmp_path / "runs/demo"
    (run / "step2/P00/i2").mkdir(parents=True)
    (run / "events.jsonl").write_text(json.dumps({"ts": "t", "stage": "step2", "message": "P00 第 2 轮：开始实现",
                                                  "parent": "cand/demo-P00-i1"}) + "\n")
    (run / "step2/P00/i1").mkdir()
    (run / "step2/P00/i1/record.json").write_text(json.dumps({"iteration": 1, "commit": "c1"}))
    lineage = api._lineage("demo", "P00", {"iteration": 2, "commit": "c2"})
    assert lineage == {"parent_iteration": 1, "parent_branch": "cand/demo-P00-i1", "base_commit": "c1"}
    with running_job({"kind": "codingagent", "run_id": "demo", "route": "P00", "iteration": 2, "stage": "implement", "target": "local"}, tmp_path / "workspace/jobs"):
        assert [j["stage"] for j in api.active_jobs("demo")] == ["implement"] and api.active_jobs("other") == []
    assert api.active_jobs("demo") == []


def test_process_liveness_without_signals() -> None:
    import os
    import sys
    from mars.osutil import alive
    assert alive(os.getpid())
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert not alive(child.pid)


def test_run_views_without_candidate_repository(tmp_path: Path, monkeypatch) -> None:
    """Viewing copied runs/ on a machine without the candidate repository must not fail (status, lists, report export)."""
    import json
    from mars import api
    monkeypatch.setenv("MARS_DEFAULT_PROJECT", str(tmp_path))
    monkeypatch.setattr(api, "_repo", lambda *a, **k: tmp_path / "no-repo")
    record = tmp_path / "runs/demo/step2/P00/i1/record.json"
    record.parent.mkdir(parents=True)
    (tmp_path / "runs/demo/contract.yaml").write_text("title: demo\n", encoding="utf-8")
    record.write_text(json.dumps({"iteration": 1, "commit": "abc", "task": {"summary": "x"}}), encoding="utf-8")
    assert api.route_summaries("demo")[0]["iterations"][0]["base_commit"] is None
    with pytest.raises(FileNotFoundError, match="候选仓库"):
        api.iteration_diff("demo", "P00", 1)


def test_sandbox_denies_reading_secrets(tmp_path: Path, monkeypatch) -> None:
    """The coding agent runs under the OS sandbox: key files listed by secret_paths() are unreadable, code is not."""
    import shutil
    from mars.codingagent import sandbox_prefix, secret_paths
    secret = tmp_path / "upstream.env"
    secret.write_text("ZHIPU_API_KEY=not-a-real-key\n", encoding="utf-8")
    readable = tmp_path / "code.py"
    readable.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv("MARS_SECRET_PATHS", str(secret))
    assert secret.resolve() in secret_paths()
    monkeypatch.setenv("MARS_SANDBOX_COMMAND", '["wrap"]')
    assert f"--deny-read={secret.resolve()}" in sandbox_prefix([tmp_path], 4242)
    monkeypatch.delenv("MARS_SANDBOX_COMMAND")
    if not shutil.which("sandbox-exec"):
        pytest.skip("macOS sandbox-exec not available here; the wrapper enforces --deny-read")
    prefix = sandbox_prefix([tmp_path / "work"], 4242)
    assert subprocess.run([*prefix, "cat", str(secret)], capture_output=True).returncode != 0
    assert subprocess.run([*prefix, "cat", str(readable)], capture_output=True).returncode == 0


def test_non_streaming_gateway_returning_sse_text(monkeypatch) -> None:
    """Company GLM gateway: a stream=false request answered with an SSE body (data: lines) instead of JSON."""
    import json
    from mars import nostream
    sse = "\n".join([
        'data: {"id":"c1","choices":[{"index":0,"delta":{"role":"assistant","reasoning_content":"先看"}}]}',
        'data: {"id":"c1","choices":[{"index":0,"delta":{"content":"好的，"}}]}',
        'data: {"id":"c1","choices":[{"index":0,"delta":{"content":"调用工具。","tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"read_file","arguments":"{\\"path\\": "}}]}}]}',
        'data: {"id":"c1","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"libs/a.py\\"}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":12,"completion_tokens":7,"total_tokens":19}}',
        "data: [DONE]", ""])
    data = nostream.parse_body(sse)
    message = data["choices"][0]["message"]
    assert message["content"] == "好的，调用工具。" and message["reasoning_content"] == "先看"
    assert message["tool_calls"] == [{"id": "call_1", "type": "function",
                                      "function": {"name": "read_file", "arguments": '{"path": "libs/a.py"}'}}]
    assert data["choices"][0]["finish_reason"] == "tool_calls" and data["usage"]["total_tokens"] == 19
    assert nostream.parse_body('{"choices":[{"message":{"content":"ok"}}]}')["choices"][0]["message"]["content"] == "ok"
    assert nostream.parse_body('{"data":{"choices":[{"message":{"content":"wrapped"}}]}}')["choices"][0]["message"]["content"] == "wrapped"
    with pytest.raises(ValueError):
        nostream.parse_body("event: ping\n\n")
    sent = {}

    class Reply:
        status_code, text = 200, sse

    def post(url, json, timeout, headers):
        sent.update(url=url, body=json, headers=headers)
        return Reply()
    monkeypatch.setattr(nostream.httpx, "post", post)
    result = nostream.complete("https://gw/v1/", "sk-secret-123", {"model": "glm", "messages": [], "stream": True,
                                                       "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}, 5)
    assert sent["url"] == "https://gw/v1/chat/completions" and sent["body"]["stream"] is False
    assert sent["body"]["chat_template_kwargs"] == {"enable_thinking": False} and "extra_body" not in sent["body"]
    assert sent["headers"]["Authorization"] == "Bearer sk-secret-123" and "sk-secret-123" not in json.dumps(sent["body"])
    assert result["choices"][0]["message"]["tool_calls"][0]["id"] == "call_1"


def test_pick_sources_keeps_every_research_question_within_budget() -> None:
    from mars.schemas import Selection
    from mars.step1 import pick_sources
    # 14 picks; the last 4 are the only ones for RQ5 and RQ6
    picks = [Selection(source_id=f"S{i}", question_id=f"RQ{1 + i // 3}", reason="") for i in range(10)]
    picks += [Selection(source_id=f"S{i}", question_id="RQ5" if i < 12 else "RQ6", reason="") for i in range(10, 14)]
    chosen = pick_sources(picks, {s.source_id for s in picks}, 10)
    assert len(chosen) == 10
    assert {s.question_id for s in chosen} == {s.question_id for s in picks}
    assert [s.source_id for s in chosen] == sorted((s.source_id for s in chosen), key=lambda x: int(x[1:]))
    # unknown and duplicate ids are skipped; under budget everything is kept
    extra = [Selection(source_id="X", question_id="RQ7", reason=""), picks[0]]
    assert [s.source_id for s in pick_sources(picks[:3] + extra, {"S0", "S1", "S2"}, 10)] == ["S0", "S1", "S2"]


