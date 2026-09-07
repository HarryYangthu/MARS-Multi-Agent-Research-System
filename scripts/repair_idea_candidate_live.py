"""Real Idea revision using a prior audited public research run, without new research."""
from __future__ import annotations
import argparse
import asyncio
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any
from loguru import logger
from app.agents.idea.agent import IdeaAgent
from app.agents.base import Artifact
from app.agents.idea.delivery import progress_sink, write_delivery
from app.harness.agent_loop import LoopInput, NativeAgentLoop, AgentLoopPolicy
from app.harness.agent_loop.trace import audit_trace, atomic_json, digest
from app.harness.agent_loop.review import ExternalReview
from app.harness.llm.model_registry import get_agent_config
from app.harness.schema.frontmatter_parser import parse
from app.harness.tools.registry import ToolContext, get_registry
from scripts.run_idea_lut_live import evaluation_request


async def run(prior: Path, target: Path, review: Path) -> None:
    initial = json.loads((prior/'input'/'request.json').read_text())
    prior_summary = json.loads((prior/'summary.json').read_text())
    trace = Path(prior_summary['trace_root'])
    if not audit_trace(trace)['consistent']:
        raise ValueError('prior evidence trace must be consistent')
    state = json.loads((trace/'checkpoint.json').read_text())
    if not state['candidate']:
        raise ValueError('prior run has no candidate to revise')
    bound_review = ExternalReview.from_mapping(json.loads(review.read_text()))
    if bound_review.candidate_digest != digest(state['candidate']):
        raise ValueError('external review does not match the current prior candidate')
    if state.get('pending') is not None:
        raise ValueError('prior run must have no pending operation')
    source_commit = subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    source_tree = subprocess.check_output(['git','rev-parse','HEAD^{tree}'],text=True).strip()
    if subprocess.check_output(['git','status','--porcelain'],text=True).strip():
        raise ValueError('commit source changes before a live revision')
    target.mkdir(parents=True,exist_ok=False)
    scenario = initial['scenario']
    request = evaluation_request(scenario,target)
    request.user_request += '\nThis is a revision using the supplied, previously audited public research. Do not repeat retrieval. Resolve the external review and output only the corrected complete document.'
    cfg = get_agent_config('idea')
    agent = IdeaAgent(agent_config=replace(cfg,tools=()))
    context = await agent.build_context(request)
    context.upstream['previous_unaccepted_candidate'] = state['candidate']
    context.upstream['external_review'] = review.read_text()
    receipts=[]
    for observation in state['history']:
        if observation['tool']=='search.fetch_sources' and observation['ok']:
            for source in observation['output'].get('sources',[]):
                receipts.append({key: source.get(key) for key in ('title','url','sha256','download_path','read_receipt','visible_pages')})
    # All visible excerpts are prior real tool results, not authored observations.
    unique: dict[str, dict[str, Any]] = {}
    for receipt in receipts:
        identity = receipt['sha256']
        record = unique.setdefault(identity, {**receipt, 'visible_pages': {}, 'read_receipts': [], 'full_document_read': False})
        record['read_receipts'].append(receipt['read_receipt'])
        for page in receipt['visible_pages']:
            old = record['visible_pages'].get(page['page'])
            if old is None or len(page['text']) > len(old['text']):
                record['visible_pages'][page['page']] = page
    for record in unique.values():
        record['visible_pages'] = list(record['visible_pages'].values())
    context.upstream['prior_public_read_receipts'] = json.dumps(list(unique.values()),ensure_ascii=False)
    provider, model = agent._select_provider()
    model.request_timeout_seconds=300
    model.max_retries=0
    model.max_tokens=16384
    model.temperature=0.1
    async def validate(text: str, observations: list[dict[str, Any]]) -> list[str]:
        return await agent.validate_candidate(request,text,state['history']+observations)
    atomic_json(target/'source.json',{'source_commit':source_commit, 'source_tree':source_tree, 'source_dirty':False,
        'prior_run':str(prior),'prior_source_commit':initial['source_commit'],'review':review.read_text(),
        'prior_candidate_digest':bound_review.candidate_digest, 'external_assistance':True,
        'scope':'candidate revision using prior audited research; not a new end-to-end research run'})
    try:
        result=await NativeAgentLoop().run(LoopInput(
            messages=agent._messages_for_context(request,context,purpose='evidence_bound_revision'),
            provider=provider,config=model,registry=get_registry(),tools=(),
            tool_context=ToolContext(target.name,request.project,'idea'),
            policy=AgentLoopPolicy(protocol='native_tools',mode='react',max_model_calls=4,max_tool_steps=0,
                                   max_validation_repairs=3,input_token_budget=128000),
            trace_root=target/'trace',validate=validate,final_schema=agent.submission_schema(request),
            progress_sink=progress_sink(request,'trace')))
    finally:
        await provider.close()
    (target/'candidate.md').write_text(result.text)
    audit=audit_trace(target/'trace')
    atomic_json(target/'audit.json',audit)
    delivery=None
    metadata: dict[str, Any]={}
    if result.status=='passed' and audit['consistent']:
        parsed=parse(result.text)
        metadata=parsed.metadata
        delivery=write_delivery(Artifact(result.text,'proposal.v1',metadata,parsed.body),request,
                                invocation='trace',reviewed=False)
    atomic_json(target/'summary.json',{'status':result.status,'counts':result.counts,
        'usage':audit['facts']['usage'],'usage_complete':audit['facts']['usage_complete'],
        'trace_consistent':audit['consistent'],'source_commit':source_commit,'source_tree':source_tree,
        'candidate_digest':digest(result.text), 'human_summary':metadata.get('human_summary'),
        'handoff':metadata.get('handoff'), 'delivery_root':str(delivery) if delivery else None,
        'external_assistance':True,'new_research_performed':False,'model_review_passed':False,
        'scientific_validated':False,'simulation_executed':False,'prior_evidence_run':str(prior)})
    logger.info('IDEA_REVISION_RESULT status={} counts={} output={}',result.status,result.counts,target)
    if delivery is None:
        raise RuntimeError('revision did not produce an audited delivery; prior evidence remains unchanged')

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('prior',type=Path)
    parser.add_argument('target',type=Path)
    parser.add_argument('review',type=Path)
    args=parser.parse_args()
    asyncio.run(run(args.prior.resolve(),args.target.resolve(),args.review.resolve()))
