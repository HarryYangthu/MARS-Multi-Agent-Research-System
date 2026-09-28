"""Bounded real BaseAgent assembly/native loop compaction and interruption recovery check.

Authored stress material and a random local nonce are diagnostic inputs, not research
results. No provider/tool substitutes. Does not certify scientific proposal quality.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import secrets
from typing import Any

import frontmatter
from loguru import logger

from app.agents.base import BaseAgent, RunRequest
from app.agents.idea.agent import IdeaAgent
from app.harness.agent_loop import AgentLoopPolicy, LoopInput, NativeAgentLoop
from app.harness.agent_loop.trace import atomic_json
from app.harness.context.runtime_policy import freeze_policy
from app.harness.llm.model_registry import get_agent_config, select_provider
from app.harness.tools.registry import ToolContext, get_registry
from app.harness.schema.validator import validate_document


async def verify(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=False)
    source = root / 'input'; source.mkdir()
    nonce = secrets.token_hex(16)
    (source / 'baseline.py').write_text(f'VALIDATION_NONCE = "{nonce}"\n')
    base_cfg = get_agent_config('idea')
    agent = IdeaAgent(agent_config=replace(base_cfg, tools=(), raw={**base_cfg.raw,
        'tools': [], 'loop': {'protocol':'native_tools','native_observation_history':True,'mode':'react','submission_body_field':'human_summary'}}))
    request = RunRequest(project='synthetic_regression', user_request=(
        '这是一项上下文机制诊断，不开展科研、不声称实验成功。先使用 context.read_material 回读一页已压缩的背景资料，'
        'ref 使用上下文提供的哈希；随后用 code.repo_reader 读取 baseline.py。每轮只调用一个工具，两个工具各一次。'
        '最后通过 mars_submit_document 提交简短 proposal.v1 的 metadata 对象，project=synthetic_regression，'
        'human_summary 简述实际操作；额外字段 verification_nonce 必须等于文件里的值。'
        'research_question/hypothesis/novelty 用中文说明压缩恢复机制验证，各至少8字符；不虚构文献或实验成绩。'),
        upstream_artifacts={'diagnostic-background.md': ('# Background\n' + '诊断填充，非研究证据。' * 160 + '\n') * 4},
        extra={'run_root': str(root), 'context_sources': {'project_rules':False,'project_references':False,
               'code_repositories':False,'agent_resources':False,'memory':False}})
    # Exercise the shared BaseAgent assembly with the configured Idea identity.
    # Scientific Idea handoff validation is a separate acceptance layer.
    context = await BaseAgent.build_context(agent, request)
    submission_schema = BaseAgent.submission_schema(agent, request)
    assert submission_schema is not None
    submission_schema["required"] += ["human_summary", "verification_nonce"]
    submission_schema["properties"]["human_summary"] = {"type": "string", "minLength": 1}
    submission_schema["properties"]["verification_nonce"] = {"type": "string", "pattern": "^[a-f0-9]{32}$"}
    messages = agent._messages_for_context(request, context, purpose='context_diagnostic')
    policy = AgentLoopPolicy(protocol='native_tools', native_observation_history=True, mode='react',
        submission_body_field='human_summary', input_token_budget=28000, max_model_calls=5, max_tool_steps=2, max_validation_repairs=1, max_protocol_repairs=1)
    config_agent = replace(base_cfg, max_tokens=8192, max_retries=0, request_timeout_seconds=180)
    interrupted = False

    async def progress(event: dict[str, Any]) -> None:
        nonlocal interrupted
        if event['kind'] == 'observation' and event.get('tool') == 'code.repo_reader' and event.get('ok') and not interrupted:
            interrupted = True
            raise asyncio.CancelledError()

    async def validate(text: str, observations: list[dict[str, Any]]) -> list[str]:
        result = validate_document(text, expected_schema='proposal.v1')
        errors = [str(e.message) for e in result.errors]
        if frontmatter.loads(text).metadata.get('verification_nonce') != nonce:
            errors.append('verification_nonce must equal the value read from the real source file')
        names = [o['tool'] for o in observations if o.get('ok')]
        if names != ['context.read_material','code.repo_reader']:
            errors.append('Read each specified real tool once, in the requested order')
        return errors

    async def run(resume: bool) -> Any:
        provider, cfg = select_provider(config_agent)
        return await NativeAgentLoop().run(LoopInput(messages=messages, provider=provider, config=cfg,
            registry=get_registry(), tools=('context.read_material','code.repo_reader'),
            tool_context=ToolContext(root.name,'synthetic_regression','idea',project_repo_root=str(source),
                extra={'run_root':str(root)}, supplemental_read_scope=get_registry().scope_for_read_tools(
                    'idea', ('context.read_material', 'code.repo_reader'))), policy=policy, trace_root=root/'trace', validate=validate,
            final_schema=submission_schema, context_metadata=context.metadata,
            progress_sink=progress, resume=resume))

    try:
        await run(False)
    except asyncio.CancelledError:
        pass
    if not interrupted:
        raise RuntimeError('live run did not reach the planned interruption boundary')
    first = json.loads((root/'trace/checkpoint.json').read_text())
    assert first['status'] == 'interrupted' and first['context_compaction']['act']['levels']
    result = await run(True)
    checkpoint = json.loads((root/'trace/checkpoint.json').read_text())
    records = [json.loads(line) for line in (root/'trace/events.jsonl').read_text().splitlines()]
    manifests = [json.loads(p.read_text()) for p in (root/'context/agents/idea/manifests').glob('*.json')]
    report = {'status': result.status, 'scope':'BaseAgent assembly / Idea identity + real native tools + compaction + resume + proposal schema',
        'scientific_acceptance':False, 'full_research_accepted':False, 'provider':base_cfg.model_provider,
        'model':base_cfg.model_name, 'interrupted':interrupted, 'counts_before_resume':first['counts'],
        'counts':result.counts, 'usage':checkpoint['usage'], 'usage_complete':checkpoint['usage_complete'],
        'compaction_events':sum(row['kind']=='context_compressed' for row in records),
        'actual_manifests':len(manifests), 'policy_version':freeze_policy(root)['version'],
        'model_request_limit':policy.max_model_calls, 'output_token_limit':8192}
    (root/'result.md').write_text(result.text)
    atomic_json(root/'receipt.json',report)
    if result.status != 'passed' or result.counts['tool_dispatches'] != 2:
        raise RuntimeError('live validation did not pass; consult receipt')
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args = parser.parse_args()
    report = asyncio.run(verify(args.output.resolve()))
    logger.info('Context v3 live verification: {}', json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    main()
