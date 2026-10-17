"""Pure source-window accounting and deduplication of currently visible evidence."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
import json
import re

from app.harness.agent_loop.trace import canonical, digest
from app.harness.llm.provider_base import Message


def source_output(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict) or item.get('tool') != 'code.repo_reader' or item.get('ok') is not True:
        return None
    output = item.get('output')
    if not isinstance(output, dict):
        return None
    if any(not isinstance(output.get(k), str) or not output[k] for k in ('repo_root', 'path', 'sha256')):
        return None
    if re.fullmatch('[a-f0-9]{64}', output['sha256']) is None:
        return None
    body, start, end, total = (output.get(k) for k in ('content', 'char_start', 'char_end', 'total_chars'))
    if (not isinstance(body, str) or type(start) is not int or type(end) is not int or type(total) is not int
            or not 0 <= start <= end <= total or end - start != len(body)):
        return None
    return output


def window_key(output: dict[str, Any]) -> str:
    return digest({k: output.get(k) for k in ('repo_root', 'path', 'sha256', 'source_bytes_sha256',
                                             'char_start', 'char_end', 'total_chars', 'content')})


def grouped_read_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse only successful identical read receipts in the display index."""
    result: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for item in history:
        output = source_output(item)
        if output is None:
            result.append(item)
            continue
        key = window_key(output)
        if key in seen:
            entry = seen[key]
            entry['read_count'] = entry.get('read_count', 1) + 1
            entry['last_raw_ref'] = item.get('raw_ref')
        else:
            entry = {**item, 'source_window': {k: output[k] for k in ('path', 'sha256', 'char_start', 'char_end')}}
            result.append(entry)
            seen[key] = entry
    return result


def _merge(intervals: list[list[int]]) -> list[list[int]]:
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def repository_index(history: list[dict[str, Any]], *, max_files: int, repeat_after: int) -> dict[str, Any]:
    files: dict[tuple[str, str], dict[str, Any]] = {}
    counts: dict[str, int] = {}
    for item in history:
        output = source_output(item)
        if output is None:
            continue
        key = (output['repo_root'], output['path'])
        version = (output['sha256'], output.get('source_bytes_sha256'))
        record = files.get(key)
        if record is None or record['version'] != version:
            record = {'repo_root': key[0], 'path': key[1], 'version': version,
                      'total_chars': output['total_chars'], 'read_ranges': [], 'same_window_reads': 0}
        counts[window_key(output)] = counts.get(window_key(output), 0) + 1
        record['same_window_reads'] = max(record['same_window_reads'], counts[window_key(output)])
        record['read_ranges'] = _merge(record['read_ranges'] + [[output['char_start'], output['char_end']]])
        cache = output.get('cache')
        if isinstance(cache, dict) and isinstance(cache.get('material_ref'), str):
            record['material_ref'] = cache['material_ref']
        record['repeated_read'] = record['same_window_reads'] >= repeat_after
        files.pop(key, None)
        files[key] = record
    rows = list(files.values())
    return {'files': rows[-max_files:], 'omitted_files': max(0, len(rows) - max_files),
            'note': 'Ranges describe actual historical reads, not currently visible text or current filesystem state. '
                    'Reuse visible same-version code; if absent, read the needed snapshot window with context.read_material. '
                    'Use code.repo_reader to verify live changes. Repeated reads are a progress hint, never a read ban.'}


@dataclass(frozen=True)
class SourceMessage:
    prefix: str
    envelope: dict[str, Any]
    output: dict[str, Any]
    key: str
    text_sha256: str


def _parse_message(content: str) -> SourceMessage | None:
    prefix, _, body = content.partition('\n')
    if prefix not in {'[untrusted prior action and host Observation]',
                     '[untrusted complete review Observation; exact duplicates may reference included text]'}:
        return None
    try:
        envelope = json.loads(body)
    except ValueError:
        return None
    output = source_output(envelope)
    if output is None:
        return None
    return SourceMessage(prefix, envelope, output, window_key(output), digest(output['content']))


def deduplicate_visible(units: list[list[Message]], eligible: list[bool], *,
                        parsed: dict[str, SourceMessage | None], hashes: dict[Message, str]
                        ) -> tuple[list[list[Message]], int]:
    # Run against the *current* packed messages, after any offload. A reference
    # is emitted only when its complete counterpart exists in this same prompt.
    seen: dict[str, str] = {}
    result: list[list[Message]] = []
    removed = 0
    for allowed, unit in zip(eligible, units):
        rendered = []
        for message in unit:
            updated = message
            if allowed:
                if message.content not in parsed:
                    parsed[message.content] = _parse_message(message.content)
                source = parsed[message.content]
                if source is not None:
                    if source.key in seen:
                        content = {'same_source_window': source.key, 'included_message_sha256': seen[source.key],
                                   'duplicate_of_included_text_sha256': source.text_sha256,
                                   'note': 'Exact full text appears earlier in this prompt; no reread needed for this version.'}
                        updated = replace(message, content=source.prefix + '\n' + canonical(
                            {**source.envelope, 'output': {**source.output, 'content': content}}))
                        # Small windows should not get larger merely to deduplicate.
                        if len(updated.content) < len(message.content):
                            removed += len(message.content) - len(updated.content)
                        else:
                            updated = message
                    else:
                        if message not in hashes:
                            hashes[message] = digest(message.to_wire())
                        seen[source.key] = hashes[message]
            rendered.append(updated)
        result.append(rendered)
    return result, removed
