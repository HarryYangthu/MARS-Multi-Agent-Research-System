"""Real API + real filesystem loop verification; no service or model substitutes."""
from __future__ import annotations
import asyncio
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from loguru import logger
from app.harness.agent_loop import AgentLoopPolicy, LoopInput, NativeAgentLoop
from app.harness.llm.openai_provider import DeepSeekProvider
from app.harness.llm.provider_base import LLMConfig, Message
from app.harness.tools.registry import ToolRegistry, ToolSpec, ToolContext, ToolResult
from app.settings import env_or_local


async def main() -> None:
    key = env_or_local('DEEPSEEK_API_KEY')
    if not key:
        raise RuntimeError('DEEPSEEK_API_KEY required')
    root = Path('runs/native_loop_checks') / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    root.mkdir(parents=True)
    (root/"source.json").write_text(json.dumps({
        "commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
        "tracked_changes":subprocess.check_output(["git","diff","HEAD","--name-only"],text=True).splitlines(),
        "scope":"real API and filesystem arithmetic harness probe; not Idea proposal acceptance"}))
    # Explicit synthetic arithmetic fixtures, not invented research or tool outputs.
    for name, values in [('a.json',[17,23,41]),('b.json',[13,29,37])]:
        (root/name).write_text(json.dumps(values))
    registry = ToolRegistry()
    async def read(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = (root/args['name']).resolve()
        if path.parent != root.resolve():
            return ToolResult(False, error='outside test directory')
        try:
            return ToolResult(True, output={'content':path.read_text()})
        except OSError as exc:
            return ToolResult(False, error=type(exc).__name__)
    registry.register('verification.read', read, spec=ToolSpec('verification.read','verification',
        'Read an actual file from the arithmetic verification directory.',
        input_schema={'type':'object','properties':{'name':{'type':'string'}},'required':['name'],'additionalProperties':False}))
    async def validate(text: str, observations: list[dict[str, Any]]) -> list[str]:
        import re
        errors=[]
        if not re.search(r'\b160\b',text): errors.append('Report the exact combined sum of all numbers.')
        if {o['args']['name'] for o in observations if o['ok']} != {'a.json','b.json'}:
            errors.append('Read both actual files before submitting.')
        return errors
    # Harness probe uses the existing system tool context, confined to its own files.
    # This does not validate Idea Agent permissions or its proposal schema.
    summaries=[]
    for mode in ('react','reflection'):
        policy=AgentLoopPolicy.from_mapping({'protocol':'native_tools','mode':mode,'max_model_calls':8,
            'max_tool_steps':4,'input_token_budget':8000})
        result=await NativeAgentLoop().run(LoopInput(
            messages=[Message('system','Perform only this public synthetic arithmetic verification. No research claims.'),
                      Message('user','Read a.json and b.json using the supplied tool, then report their combined sum. Do not guess file contents.')],
            provider=DeepSeekProvider(api_key=key),
            config=LLMConfig(provider='deepseek',model='deepseek-v4-flash',thinking_enabled=False,
                             max_tokens=2048,request_timeout_seconds=60,max_retries=0),
            registry=registry,tool_context=ToolContext(root.name,'verification','system'),
            tools=('verification.read',),policy=policy,trace_root=root/mode,validate=validate))
        summary={'mode':mode,'status':result.status,'counts':result.counts,'answer':result.text,
                 'reflection_accepted':result.reflection_accepted}
        summaries.append(summary)
        logger.info('{}', summary)
    (root/'summary.json').write_text(json.dumps(summaries,indent=2))
    if any(s['status'] != 'passed' for s in summaries):
        raise RuntimeError(f'live loop verification failed; evidence: {root}')

if __name__=='__main__':
    asyncio.run(main())
