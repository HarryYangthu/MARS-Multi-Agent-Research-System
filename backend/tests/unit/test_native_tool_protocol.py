"""Pure message/SDK envelope contracts, never substitutes for live execution."""
import pytest
from openai.types.chat import ChatCompletion
from app.harness.llm.provider_base import Completion, LLMConfig, Message, ToolCall
from app.harness.llm.openai_provider import DeepSeekProvider
from app.harness.agent_loop.native_protocol import native_decision, wire_name
from app.harness.agent_loop.context import pack_context


def test_native_envelope_and_wire_roundtrip() -> None:
    provider = DeepSeekProvider(api_key="parser-input-not-a-key")
    cfg = LLMConfig(provider="deepseek", model="parser-contract", thinking_enabled=False,
                    tools=({"type": "function", "function": {"name": "read"}},))
    envelope = ChatCompletion.model_validate({"id": "authored-parser-input", "object": "chat.completion",
        "created": 0, "model": "parser-contract", "choices": [{"index": 0, "finish_reason": "tool_calls",
        "message": {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function",
        "function": {"name": wire_name("file.read"), "arguments": '{"path":"a"}'}}]}}]})
    completion = provider._completion_from_response(envelope, cfg)
    decision = native_decision(completion, ("file.read",))
    assert decision['tool'] == 'file.read' and decision['args'] == {'path': 'a'}
    messages = [Message('assistant', '', completion.tool_calls), Message('tool', 'actual content', tool_call_id='c1')]
    wire = provider._request_kwargs(messages, cfg)
    assert wire['messages'][1]['tool_call_id'] == wire['messages'][0]['tool_calls'][0]['id']
    assert wire['extra_body']['thinking']['type'] == 'disabled'
    assert provider._client is None


@pytest.mark.parametrize('args', ['{"x":1,"x":2}', '[]', '{"x":NaN}', '{"x":1e999}'])
def test_invalid_arguments_never_become_actions(args: str) -> None:
    with pytest.raises(ValueError):
        native_decision(Completion('', 'parser', 'parser', tool_calls=(ToolCall('c1',wire_name('read'),args),)), ('read',))


def test_compaction_keeps_complete_call_result_pairs() -> None:
    history = [{'tool':'read', 'args':{}, 'reason':'inspect', 'ok':True, 'output':'x'*12000,
        'native_call':{'id':f'c{i}','name':wire_name('read'),'arguments':'{}'}} for i in range(5)]
    messages, manifest = pack_context([Message('system','task')], history, '', '', budget=4000, observation_chars=512)
    for i,m in enumerate(messages):
        if m.role == 'tool':
            assert messages[i-1].tool_calls[0].id == m.tool_call_id
        if m.tool_calls:
            assert messages[i+1].tool_call_id == m.tool_calls[0].id
    assert manifest['estimated_upper_bound_tokens'] <= 4000


def test_context_digest_matches_wire_messages() -> None:
    from app.harness.agent_loop.trace import digest
    messages, manifest = pack_context([Message('system','task')], [], '', '', budget=4000, observation_chars=512)
    assert manifest['visible_sha256'] == digest([m.to_wire() for m in messages])


def test_batch_parsing_preserves_ids_and_context_group() -> None:
    from app.harness.agent_loop.native_protocol import group_messages
    calls = (ToolCall('a',wire_name('read'),'{"path":"a"}'),
             ToolCall('b',wire_name('read'),'{"path":"b"}'))
    actions = native_decision(Completion('inspect both', 'parser','parser',tool_calls=calls),('read',))['batch']
    for action in actions:
        action.update(native_batch_id=1, native_batch_size=2)
    messages = group_messages(actions, ['authored result A','authored result B'])
    assert messages[0].tool_calls == calls
    assert [m.tool_call_id for m in messages[1:]] == ['a','b']
    with pytest.raises(ValueError, match='incomplete'):
        group_messages(actions[:1], ['authored result A'])
    with pytest.raises(ValueError, match='duplicate'):
        native_decision(Completion('', 'parser','parser',tool_calls=(calls[0],calls[0])),('read',))


def test_native_idea_prompt_has_no_legacy_json_instruction() -> None:
    from app.agents.idea.agent import IdeaAgent
    from app.agents.base import RunRequest, ContextPack
    agent = IdeaAgent()
    request = RunRequest(project='pimc',user_request='public task')
    context = ContextPack(system=agent.agent_brief,project='',task='public task')
    text = '\n'.join(m.content for m in agent._messages_for_context(request,context,purpose='contract'))
    assert 'final.metadata' not in text and 'final.body' not in text
    assert 'mars_submit_document' in text
    assert 'never write YAML in arguments' in text


def test_native_candidate_is_not_rewrapped_as_json() -> None:
    candidate = '---\nschema: proposal.v1\n---\n# Draft'
    messages, _ = pack_context([Message('system','task')], [], '', candidate,
                               budget=4000, observation_chars=512, native=True)
    assert messages[-1].content == candidate


@pytest.mark.asyncio
async def test_fenced_frontmatter_gets_actionable_format_feedback() -> None:
    from app.agents.idea.agent import IdeaAgent
    from app.agents.base import RunRequest
    errors = await IdeaAgent().validate_candidate(RunRequest(project='pimc',user_request='test'),
        'Explanation\n```markdown\n---\nschema: proposal.v1\n---\ntext\n```', [])
    assert len(errors) == 1 and errors[0].startswith('/format:')
    assert 'code fences' in errors[0]


def test_native_submission_serializes_exact_metadata_with_yaml_sensitive_text() -> None:
    import json
    from app.harness.agent_loop.native_protocol import SUBMIT_DOCUMENT, native_specs
    from app.harness.schema.frontmatter_parser import parse
    metadata = {"schema": "proposal.v1", "human_summary": "检查边界: 保留数值1.2及原文。",
                "method_spec": {"definition": "f(x): a*x + b", "coefficients": [1, 2]}, "enabled": False}
    arguments = json.dumps({"metadata": metadata, "body": "Parser input body."}, ensure_ascii=False)
    call = ToolCall("submission-1", SUBMIT_DOCUMENT, arguments)
    decision = native_decision(Completion("", "parser", "parser", tool_calls=(call,)), (), structured_final=True)
    assert decision["submission_id"] == call.id
    parsed = parse(decision["final"])
    assert parsed.metadata == metadata
    assert parsed.body == "Parser input body."
    assert "tool" not in decision
    assert native_specs([], {"type": "object"})[0]["function"]["name"] == SUBMIT_DOCUMENT


@pytest.mark.parametrize("arguments", [
    '{"metadata":{"x":1,"x":2},"body":"text"}',
    '{"metadata":{"x":NaN},"body":"text"}',
    '{"metadata":{},"body":"text"}',
    '{"metadata":{"x":1},"body":"text","approved":true}',
])
def test_native_submission_rejects_invalid_arguments(arguments: str) -> None:
    from app.harness.agent_loop.native_protocol import SUBMIT_DOCUMENT
    with pytest.raises(ValueError):
        native_decision(Completion("", "parser", "parser", tool_calls=(ToolCall("c", SUBMIT_DOCUMENT, arguments),)),
                        (), structured_final=True)


def test_native_submission_cannot_be_mixed_with_tool_execution() -> None:
    from app.harness.agent_loop.native_protocol import SUBMIT_DOCUMENT
    calls = (ToolCall("read", wire_name("file.read"), "{}"),
             ToolCall("submit", SUBMIT_DOCUMENT, '{"metadata":{"x":1},"body":"text"}'))
    with pytest.raises(ValueError, match="never batched"):
        native_decision(Completion("", "parser", "parser", tool_calls=calls), ("file.read",), structured_final=True)
    with pytest.raises(ValueError, match="mars_submit_document"):
        native_decision(Completion("raw prose", "parser", "parser"), (), structured_final=True)
    with pytest.raises(ValueError, match="unknown"):
        native_decision(Completion("", "parser", "parser", tool_calls=(calls[1],)), ())
