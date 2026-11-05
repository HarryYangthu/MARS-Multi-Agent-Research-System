"""Real model checks: select a working transport, then test JSON, tools and thinking on it.

The streaming reader and the non-streaming JSON/SSE adapter are shared with mars-core.
No fallback model is used by the final LLM.structured check. About 12 short requests per role.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal

CORE = Path(os.environ.get("MARS_CORE_ROOT", Path(__file__).resolve().parents[2] / "mars-core")).resolve()
sys.path.insert(0, str(CORE))
from mars.llm import LLM, strip_thinking, thinking_params  # noqa: E402
from mars.nostream import complete, parse_body, read_stream  # noqa: E402

import litellm
from pydantic import BaseModel

PONG = [{"role": "user", "content": "Reply with the single word: pong"}]
TOOL = {"type": "function", "function": {"name": "add", "description": "Add two integers.",
        "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                       "required": ["a", "b"]}}}
TOOL_MESSAGES = [{"role": "user", "content": "Use the add tool to compute 2 + 3. Call the tool first. "
                  "After receiving its result, reply with only the number, without calling another tool."}]
THINK_PROMPT = [{"role": "user", "content": "Find the smallest positive integer n such that n modulo 5 is 2, "
                 "n modulo 7 is 3, and n modulo 11 is 4. Verify all three conditions; reply briefly."}]
THINKING = {"none": {}, "zhipu_enabled": {"thinking": {"type": "enabled"}},
            "zhipu_disabled": {"thinking": {"type": "disabled"}},
            "template_enable_false": {"chat_template_kwargs": {"enable_thinking": False}},
            "template_enable_true": {"chat_template_kwargs": {"enable_thinking": True}}}


class ProbeAnswer(BaseModel):
    answer: Literal["pong"]


def safe_error(error: object, spec: dict) -> str:
    text = f"{type(error).__name__}: {error}"
    return text.replace(spec["api_key"], "[redacted]")[:500] if spec.get("api_key") else text[:500]


def thinking_body(spec: dict, variant: dict) -> dict:
    """Remove only thinking switches, retaining unrelated provider settings without mutating config."""
    extra = copy.deepcopy(spec.get("extra_body") or {})
    for key in ("thinking", "reasoning_effort", "enable_thinking"):
        extra.pop(key, None)
    template = extra.pop("chat_template_kwargs", {}).copy()
    template.pop("enable_thinking", None)
    template.update(variant.get("chat_template_kwargs") or {})
    if template:
        extra["chat_template_kwargs"] = template
    extra.update({k: v for k, v in variant.items() if k != "chat_template_kwargs"})
    return extra


def raw_probe(spec: dict) -> dict:
    body = json.dumps({"model": spec["model"], "messages": PONG, "max_tokens": 512, "stream": False}).encode()
    request = urllib.request.Request(spec["api_base"].rstrip("/") + "/chat/completions", data=body, method="POST",
                                     headers={"Authorization": "Bearer " + spec["api_key"], "Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=float(spec.get("timeout", 600))) as response:
            data, status, kind = response.read(), response.status, response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        data, status, kind = error.read(), error.code, error.headers.get("Content-Type", "")
    except OSError as error:
        return {"ok": False, "error": safe_error(error, spec)}
    text = data.decode("utf-8", "replace")
    result = {"ok": status == 200, "status": status, "content_type": kind,
              "seconds": round(time.monotonic() - started, 1), "sse_body_for_non_stream": text.lstrip().startswith(("data:", "event:")),
              "body_start": text.replace(spec["api_key"], "[redacted]")[:160]}
    try:
        parsed = parse_body(text)
        result["response_valid"] = bool(parsed.get("choices")) and not parsed.get("error")
    except (ValueError, TypeError, RuntimeError) as error:
        result.update(response_valid=False, parse_error=safe_error(error, spec))
    return result


def call(spec: dict, *, stream: bool, extra_body: dict | None = None, use_spec_thinking: bool = True,
         **params) -> dict:
    messages = params.pop("messages", PONG)
    if spec.get("single_user") and len(messages) > 1 and "tools" not in params:
        messages = [{"role": "user", "content": "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages)}]
    request = dict(model="openai/" + spec["model"], api_base=spec["api_base"], api_key=spec["api_key"],
                   messages=messages, max_tokens=int(spec["max_tokens"]), **params)
    request.update(thinking_params(spec) if use_spec_thinking else {"extra_body": thinking_body(spec, extra_body or {})})
    started = time.monotonic()
    metadata = {"transport": "stream" if stream else "nonstream"}
    try:
        if stream:
            chunks = litellm.completion(**request, stream=True, stream_options={"include_usage": True},
                                        timeout=float(spec.get("idle_timeout", 180)))
            data, first, count = read_stream(chunks, float(spec.get("timeout", 600)), started)
            metadata.update(chunks=count, first_chunk_s=first)
        else:
            body = {k: v for k, v in request.items() if v is not None and k not in ("api_base", "api_key")}
            data = complete(spec["api_base"], spec["api_key"], {**body, "model": spec["model"]}, float(spec.get("timeout", 600)))
        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content") or ""
        finish = choice.get("finish_reason")
        calls = message.get("tool_calls") or []
        ok = bool(strip_thinking(content).strip() or calls) and finish not in ("length", "content_filter")
        result = {**metadata, "accepted": True, "ok": ok, "seconds": round(time.monotonic() - started, 1),
                  "content": content, "reply": strip_thinking(content)[-160:],
                  "reasoning_content": message.get("reasoning_content") or message.get("reasoning") or "",
                  "reasoning_chars": len(message.get("reasoning_content") or message.get("reasoning") or ""),
                  "think_tag": "</think>" in content, "finish_reason": finish, "truncated": finish == "length",
                  "tool_calls": calls, "usage": (data.get("usage") or {}).get("completion_tokens")}
        if not ok:
            result["error"] = f"finish_reason={finish}: 输出不完整、被过滤，或只有思考没有正文/工具调用"
        return result
    except Exception as error:
        return {**metadata, "ok": False, "seconds": round(time.monotonic() - started, 1), "error": safe_error(error, spec)}


def select_transport(result: dict, preference: str = "auto") -> str | None:
    candidates = ("stream", "nonstream") if preference == "auto" else (preference,)
    return next((name for name in candidates if result.get(name, {}).get("ok")), None)


def validate_json(result: dict) -> dict:
    result = dict(result)
    valid = False
    try:
        data = json.loads(strip_thinking(result.get("content", "")).strip())
        valid = isinstance(data, dict) and data.get("answer") == "pong"
    except ValueError:
        pass
    result.update(valid_json=valid, valid_json_tail=valid, ok=bool(result.get("ok") and valid))
    if not result["ok"]:
        result.setdefault("error", "完整回复未通过 JSON 对象校验（answer 必须为 pong）")
    return result


def tool_arguments(result: dict) -> dict | None:
    calls = result.get("tool_calls") or []
    if len(calls) != 1 or not calls[0].get("id") or calls[0].get("function", {}).get("name") != "add":
        return None
    try:
        args = json.loads(calls[0]["function"]["arguments"])
        if set(args) == {"a", "b"} and all(type(value) is int for value in args.values()) and sorted(args.values()) == [2, 3]:
            return args
    except (ValueError, TypeError, KeyError):
        pass
    return None


def thinking_effects(thinking: dict) -> dict:
    effects = {}
    for name, on, off in (("zhipu", "zhipu_enabled", "zhipu_disabled"),
                          ("template", "template_enable_true", "template_enable_false")):
        a, b = thinking.get(on, {}), thinking.get(off, {})
        checked = a.get("ok") and b.get("ok")
        observed = checked and bool(a.get("reasoning_chars") or a.get("think_tag")) and not (b.get("reasoning_chars") or b.get("think_tag"))
        effects[name] = {"status": "observed_disabled" if observed else "inconclusive" if checked else "not_checked",
                         "on": on, "off": off}
    return effects


def tool_messages(result: dict, value: int) -> list[dict]:
    assistant = {"role": "assistant", "content": result["content"] or None, "tool_calls": result["tool_calls"]}
    if result.get("reasoning_content"):
        assistant["reasoning_content"] = result["reasoning_content"]
    return TOOL_MESSAGES + [assistant, {"role": "tool", "tool_call_id": result["tool_calls"][0]["id"], "content": str(value)}]


def check_tools(spec: dict, stream: bool) -> dict:
    tools = call(spec, stream=stream, tools=[TOOL], tool_choice="auto", messages=TOOL_MESSAGES)
    args = tool_arguments(tools)
    tools.update(native_tool_call=bool(tools.get("tool_calls")), arguments_valid=args is not None,
                 ok=bool(tools["ok"] and args is not None))
    if tools["ok"]:
        value = args["a"] + args["b"]
        tools["executed"] = {"name": "add", "arguments": args, "result": value}
        round_trip = call(spec, stream=stream, tools=[TOOL], tool_choice="auto", messages=tool_messages(tools, value))
        round_trip["ok"] = bool(round_trip["ok"] and not round_trip["tool_calls"] and strip_thinking(round_trip["content"]).strip() == str(value))
        if not round_trip["ok"]:
            round_trip.setdefault("error", "工具结果回传后未得到正确的最终答案 5")
        tools["round_trip"] = round_trip
    elif not tools.get("error"):
        tools["error"] = "未收到有效的 add 原生工具调用，或参数并非整数 2 和 3"
    return tools


def suggest(role: str, result: dict) -> dict:
    advice = {}
    if result.get("capability_transport"):
        advice["stream"] = result["capability_transport"] == "stream"
    if result.get("json") and not result["json"].get("skipped"):
        advice["json_mode"] = bool(result["json"].get("ok"))
    if result.get("raw", {}).get("sse_body_for_non_stream"):
        advice["note_raw"] = "网关对非流式请求返回 SSE；已使用 mars-core 的 JSON/SSE 兼容解析，能力项按标注的调用方式验证。"
    if result.get("thinking"):
        advice["thinking_switch_effect"] = {name: value.get("reasoning_chars", 0)
                                             for name, value in result["thinking"].items() if value.get("ok")}
    for effect in thinking_effects(result.get("thinking") or {}).values():
        if effect["status"] == "observed_disabled":
            advice["to_disable_thinking"] = THINKING[effect["off"]]
            advice["thinking_note"] = "本次同题对照观察到思考内容消失；不代表所有输入都能关闭模型内部推理。"
            break
    if role == "coder" and not result.get("tools", {}).get("round_trip", {}).get("ok"):
        advice["coder"] = "尚未通过原生工具调用、参数校验及真实工具结果回传；不能确认可用作 coder。"
    return advice


def check_structured(llm: LLM, role: str, transport: str, json_mode: bool, spec: dict) -> dict:
    scoped = copy.copy(llm)
    scoped.roles = copy.deepcopy(llm.roles)
    scoped.roles[role].update(stream=transport == "stream", json_mode=json_mode, max_tokens=spec["max_tokens"])
    scoped.fallbacks, scoped.retries = {}, 0
    started = time.monotonic()
    try:
        answer = scoped.structured(role, PONG, ProbeAnswer, repairs=0, tag="connection.structured")
        return {"ok": True, "transport": transport, "seconds": round(time.monotonic() - started, 1),
                "reply": json.dumps(answer.model_dump()), "model": spec["model"], "fallback_used": False, "json_mode": json_mode}
    except Exception as error:
        return {"ok": False, "transport": transport, "error": safe_error(error, spec), "fallback_used": False}


def role_ready(role: str, result: dict) -> bool:
    if not result.get("capability_transport"):
        return False
    return bool(result.get("tools", {}).get("round_trip", {}).get("ok") if role == "coder" else result.get("structured", {}).get("ok"))


def probe_role(llm: LLM, role: str, skip_thinking: bool, preference: str = "auto", max_tokens: int = 4096) -> dict:
    spec = {**llm.deployment(role)}
    spec["max_tokens"] = min(int(spec.get("max_tokens", max_tokens)), max_tokens)
    result = {"model": spec["model"], "api_base": spec["api_base"], "report_version": 2,
              "validation_scope": "short_requests", "probe_max_tokens": spec["max_tokens"],
              "configured": {k: llm.roles[role].get(k) for k in ("stream", "thinking", "reasoning_effort", "extra_body", "json_mode", "max_tokens")}}
    result["raw"] = raw_probe(spec)
    raw, status = result["raw"], result["raw"].get("status", 0)
    if raw.get("error") or status in (401, 403, 404, 429) or status >= 500:
        reason = raw.get("error") or f"HTTP {status}：请核对内网/VPN、服务地址、模型权限、Key 和限流状态"
        raw["error"] = reason
        result.update({name: {"ok": False, "skipped": True, "error": "未执行：基础连接尚未通过"}
                       for name in ("nonstream", "stream", "json", "structured", "tools")})
        result.update(ready=False, suggested={"note_raw": reason})
        return result
    result["nonstream"] = call(spec, stream=False)
    result["stream"] = call(spec, stream=True)
    transport = select_transport(result, preference)
    result["capability_transport"] = transport
    if transport is None:
        result.update({name: {"ok": False, "skipped": True, "error": "未执行：没有通过基础文本检查的指定调用方式"}
                       for name in ("json", "structured", "tools")})
        result.update(ready=False, suggested={"note_raw": "先修复基础调用；尚未判定 JSON、工具调用或思考开关能力。"})
        return result
    streaming = transport == "stream"
    if not skip_thinking:
        result["thinking"] = {name: call(spec, stream=streaming, extra_body=body, use_spec_thinking=False,
                                         messages=THINK_PROMPT) for name, body in THINKING.items()}
        result["thinking_switches"] = thinking_effects(result["thinking"])
    result["json"] = validate_json(call(spec, stream=streaming, response_format={"type": "json_object"},
        messages=[{"role": "user", "content": 'Return the JSON object {"answer": "pong"} and nothing else.'}]))
    result["structured"] = check_structured(llm, role, transport, result["json"]["ok"], spec)
    result["tools"] = check_tools(spec, streaming)
    result["suggested"] = suggest(role, result)
    result["ready"] = role_ready(role, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", action="append")
    parser.add_argument("--skip-thinking", action="store_true")
    parser.add_argument("--transport", choices=("auto", "stream", "nonstream"), default="auto")
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "local/checks")
    args = parser.parse_args()
    if args.max_tokens <= 0:
        parser.error("--max-tokens must be positive")
    llm = LLM()
    report = {}
    for role in args.role or list(llm.roles):
        try:
            report[role] = probe_role(llm, role, args.skip_thinking, args.transport, args.max_tokens)
        except Exception as error:
            report[role] = {"ready": False, "error": f"{type(error).__name__}: {error}"[:400]}
    ok = bool(report) and all(result.get("ready") for result in report.values())
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"models-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"\n报告：{path}\n{'通过' if ok else '未通过'}：strong/fast 需通过内核结构校验；coder 需通过工具调用及结果回传。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
