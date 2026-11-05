"""Pure protocol inputs exercise stream assembly, not substituted model or HTTP calls."""
import json
import time

import pytest
from mars.llm import token_usage
from mars.nostream import assemble, parse_body, read_stream
from litellm import ModelResponseStream


def frame(delta=None, finish=None, **extra):
    return {"id": "protocol-input", "model": "protocol-input", "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}], **extra}


def test_stream_preserves_long_json_reasoning_finish_and_usage_only_chunk():
    content = json.dumps({"answer": "pong", "context": "输入" * 120}, ensure_ascii=False)
    rows = [frame({"role": "assistant", "content": ""}), frame({"reasoning_content": "先核对"}),
            frame({"content": content[:40]}), frame({"content": content[40:]}, "stop"),
            {"id": "protocol-input", "model": "protocol-input", "choices": [],
             "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}}]
    data, first, count = read_stream((ModelResponseStream(**row) for row in rows), 10, time.monotonic())
    assert data["choices"][0]["message"]["content"] == content
    assert data["choices"][0]["message"]["reasoning_content"] == "先核对"
    assert data["choices"][0]["finish_reason"] == "stop"
    assert data["usage"]["total_tokens"] == 12 and first is not None and count == len(rows)
    assert token_usage(data["usage"])["output_tokens"] == 7


def test_indexed_tool_fragments_and_empty_text_are_native_calls():
    rows = [frame({"role": "assistant", "content": ""}),
            frame({"tool_calls": [{"index": 0, "id": "add-input", "type": "function",
                                    "function": {"name": "a", "arguments": '{"a":2,'}}]}),
            frame({"tool_calls": [{"index": 0, "function": {"name": "dd", "arguments": '"b":3}'}}]}, "tool_calls")]
    data, _, _ = read_stream((ModelResponseStream(**row) for row in rows), 10, time.monotonic())
    message = data["choices"][0]["message"]
    assert message["content"] == "" and message["tool_calls"][0]["function"]["name"] == "add"
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"a": 2, "b": 3}
    assert data["usage"] is None  # no token usage is invented
    sse = "\n\n".join("data: " + json.dumps(row) for row in rows) + "\n\ndata: [DONE]"
    assert parse_body(sse) == data


def test_multiple_tool_indices_and_reasoning_alias_stay_separate():
    rows = [frame({"reasoning": "协议输入", "tool_calls": [
        {"index": 1, "id": "b", "function": {"name": "second", "arguments": '{"b":'}},
        {"index": 0, "id": "a", "function": {"name": "first", "arguments": '{"a":'}}]}),
        frame({"tool_calls": [{"index": 0, "function": {"arguments": "1}"}},
                               {"index": 1, "function": {"arguments": "2}"}}]}, "tool_calls")]
    message = assemble(rows)["choices"][0]["message"]
    assert message["reasoning_content"] == "协议输入"
    assert [call["id"] for call in message["tool_calls"]] == ["a", "b"]
    assert [json.loads(call["function"]["arguments"]) for call in message["tool_calls"]] == [{"a": 1}, {"b": 2}]


@pytest.mark.parametrize("rows", [[], [frame({"content": '{"answer":"pong"}'})], [{"choices": []}]])
def test_empty_or_disconnected_stream_is_not_synthesized_as_success(rows):
    with pytest.raises(ValueError, match="incomplete stream"):
        assemble(rows)


def test_stream_error_and_total_budget_are_enforced_even_on_empty_chunks():
    with pytest.raises(RuntimeError, match="upstream stream error"):
        assemble([{"error": {"message": "protocol error input"}}])
    with pytest.raises(TimeoutError, match="total budget"):
        read_stream([frame({"role": "assistant"})], 1, time.monotonic() - 2)
    assert assemble([frame({"content": "partial"}, "length")])["choices"][0]["finish_reason"] == "length"


