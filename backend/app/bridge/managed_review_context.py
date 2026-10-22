"""Lossless contracts plus explicit reference excerpts for bounded reviews."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

from app.harness.schema.frontmatter_parser import parse


def bounded_review_payload(payload: dict[str, Any], maximum: int) -> dict[str, Any]:
    if len(json.dumps(payload, ensure_ascii=False, allow_nan=False)) <= maximum:
        return payload
    packed = copy.deepcopy(payload)
    reductions: list[dict[str, Any]] = []

    def document(text: str, ref: str, *, approved: bool = False) -> Any:
        if ref.endswith('.json'):
            try:
                value = json.loads(text)
            except (ValueError, TypeError):
                return text
            reductions.append({'ref': ref, 'operation': 'decode_json_without_loss'})
            return {'json_document': value}
        if not text.startswith('---'):
            return text
        try:
            parsed = parse(text)
        except (ValueError, TypeError):
            return text
        metadata = copy.deepcopy(parsed.metadata)
        projected = False
        if approved:
            # Downstream review keeps the approved mechanism and executable
            # protocol. The prior discovery audit is immutable source evidence,
            # not a second research task to repeat at every later stage.
            for field in ('research_context', 'related_literature'):
                if field not in metadata:
                    continue
                encoded = json.dumps(metadata[field], ensure_ascii=False, sort_keys=True)
                if len(encoded) <= 2000:
                    continue
                metadata[field] = {'approved_reference': ref, 'field': field,
                    'sha256': hashlib.sha256(encoded.encode()).hexdigest(), 'excerpt': True,
                    'review_scope': 'Previously approved discovery audit; omitted details are not newly read evidence.'}
                reductions.append({'ref': ref, 'field': field, 'operation': 'reference_approved_research_audit',
                    'original_characters': len(encoded), 'sha256': metadata[field]['sha256']})
                projected = True
        if parsed.body.strip() != str(parsed.metadata.get('human_summary', '')).strip():
            return {'metadata': metadata, 'body': parsed.body} if projected else text
        # The native artifact repeats human_summary as its body. All metadata,
        # including its complete human summary and execution plan, stays exact.
        reductions.append({'ref': ref, 'operation': 'deduplicate_identical_summary_body'})
        return {'metadata': metadata, 'body_equals': 'metadata.human_summary'}

    if isinstance(packed.get('candidate'), str):
        packed['candidate'] = document(packed['candidate'], str(packed.get('artifact_ref', 'candidate')))
    for ref, text in packed.get('approved_upstream', {}).items():
        if isinstance(text, str):
            packed['approved_upstream'][ref] = document(text, ref, approved=True)
    for item in packed.get('approved_artifacts', []):
        if isinstance(item.get('content'), str):
                item['content'] = document(item['content'], item['ref'], approved=True)

    context = packed.get('project_context')
    if isinstance(context, dict):
        query = str(packed.get('goal', '')) + ' ' + str(packed.get('task', ''))
        words = {word.lower() for word in re.findall(r'[A-Za-z_][A-Za-z_0-9./-]{2,}', query)}
        for item in context.get('files', []):
            text = item.get('content', '')
            if (not isinstance(text, str) or len(text) <= 3000 or item.get('role') == 'instructions'
                    or str(item.get('path', '')).endswith('AGENTS.md')):
                continue
            paragraphs = list(re.finditer(r'[^\n]+(?:\n(?!\n)[^\n]+)*', text))
            ranked = sorted(enumerate(paragraphs), key=lambda pair: (
                -sum(word in pair[1].group().lower() for word in words), pair[0]))
            selected: list[tuple[int, str]] = []
            used = 0
            for index, paragraph in ranked:
                value = paragraph.group()
                extra = len(value) + (2 if selected else 0)
                if used + extra > 3000:
                    continue
                selected.append((index, value))
                used += extra
            item['content'] = '\n\n'.join(value for _, value in sorted(selected))
            item['excerpt'] = True
            item['included_paragraphs'] = [index + 1 for index, _ in sorted(selected)]
            reductions.append({'ref': item['path'], 'operation': 'reference_paragraph_selection',
                'original_characters': len(text), 'included_characters': len(item['content']),
                'full_source_ref': 'input/folder_context.v1.json', 'sha256': item.get('sha256')})
    packed['context_delivery'] = {'version': 5,
        'full_payload_sha256': hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        'reductions': reductions,
        'instructions': 'Goals, constraints, instructions and current candidate metadata are complete. '
        'Previously approved discovery audits may be referenced with hashes; their mechanism and executable protocol remain included. '
        'Reference excerpts are explicitly marked. '
        'Do not claim to have read omitted paragraphs or use them as approval evidence. Request clarification if they are essential.'}
    # The available space changes as previous scientific handoffs accumulate.
    # Shrink reference excerpts by complete paragraphs before stopping a review;
    # goals, instructions and all artifact metadata remain exact.
    if isinstance(context, dict):
        while len(json.dumps(packed, ensure_ascii=False, allow_nan=False)) > maximum:
            excerpts = [item for item in context.get('files', []) if item.get('excerpt') and item.get('content')]
            if not excerpts:
                break
            item = max(excerpts, key=lambda value: len(value['content']))
            paragraphs = item['content'].split('\n\n')
            least = min(range(len(paragraphs)), key=lambda index: (
                sum(word in paragraphs[index].lower() for word in words), -index))
            paragraphs.pop(least)
            item['included_paragraphs'].pop(least)
            item['content'] = '\n\n'.join(paragraphs)
            for reduction in reductions:
                if reduction['ref'] == item['path'] and reduction['operation'] == 'reference_paragraph_selection':
                    reduction['included_characters'] = len(item['content'])
    return packed
