"""Real temporary repositories, real readers and pure context transformations."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any
import os

import pytest

from app.harness.agent_loop.trace import canonical, digest
from app.harness.context.repository_evidence import grouped_read_history, repository_index
from app.harness.context.repository_materials import repository_policy, source_window
from app.harness.context.runtime_native import pack_native, observation_envelope
from app.harness.context.runtime_pack import read_material
from app.harness.context.runtime_policy import load_policy
from app.harness.llm.provider_base import Message
from app.harness.tools.code import repo_reader_tool
from app.harness.tools.context_material import read_material_tool
from app.harness.tools.registry import ToolContext, get_registry


def workspace(tmp_path: Path) -> tuple[Path, ToolContext]:
    repo = tmp_path / 'repo'
    source = repo / 'libs/model.py'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'def forward(x):\r\n    return x * 2\r\n' * 300)
    return source, ToolContext('reuse', 'pimc', 'experiment', project_repo_root=str(repo),
                               extra={'run_root': str(tmp_path / 'run')})


async def observed(ctx: ToolContext, path: str, offset: int, sequence: int) -> dict[str, Any]:
    args = {'path': path, 'char_offset': offset}
    result = await repo_reader_tool(args, ctx)
    assert result.ok, result.error
    return {'tool': 'code.repo_reader', 'ok': result.ok, 'output': result.output, 'args': args,
            'raw_ref': f'receipts/{sequence}.json', 'native_batch_id': sequence, 'native_batch_size': 1,
            'native_call': {'id': f'call-{sequence}', 'name': 'code_repo_reader', 'arguments': canonical(args)}}


@pytest.mark.asyncio
async def test_cache_is_shared_by_agents_and_snapshots_survive_live_edits(tmp_path: Path) -> None:
    source, ctx = workspace(tmp_path)
    first = await observed(ctx, 'libs/model.py', 0, 1)
    second = await observed(replace(ctx, agent='coding'), str(source), 6000, 2)
    a, b = first['output'], second['output']
    assert not a['cache']['hit'] and b['cache']['hit']
    assert a['cache']['material_ref'] == b['cache']['material_ref']
    run = Path(ctx.extra['run_root'])
    archived = await read_material_tool({'ref': a['cache']['material_ref'], 'char_offset': 6000}, ctx)
    assert archived.ok and archived.output['content'] == b['content']
    original = source.read_text(encoding='utf-8')
    stamp = source.stat()
    source.write_bytes(source.read_bytes().replace(b'* 2', b'* 3'))
    os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    changed = (await observed(ctx, 'libs/model.py', 0, 3))['output']
    assert not changed['cache']['hit'] and changed['sha256'] != a['sha256']
    assert '* 3' in changed['content']
    assert read_material(run, a['cache']['material_ref'], 0, len(original))['content'] == original
    source.unlink()
    missing = await repo_reader_tool({'path': 'libs/model.py'}, ctx)
    assert not missing.ok  # Cache never masks deletion or failed scope checks.


@pytest.mark.asyncio
async def test_dispatch_revalidates_external_edits_and_repository_binding(tmp_path: Path) -> None:
    source, ctx = workspace(tmp_path)
    ctx.run_id = tmp_path.name
    ctx.agent = 'coding'
    args = {'path': 'libs/model.py'}
    registry = get_registry()
    first = await registry.dispatch('code.repo_reader', args, ctx)
    repeat = await registry.dispatch('code.repo_reader', args, ctx)
    assert first.ok and repeat.ok and repeat.output['cache']['hit']
    stamp = source.stat()
    source.write_bytes(source.read_bytes().replace(b'* 2', b'* 3'))
    os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    changed = await registry.dispatch('code.repo_reader', args, ctx)
    assert changed.ok and changed.output['sha256'] != first.output['sha256']
    assert '* 3' in changed.output['content']
    _, other = workspace(tmp_path / 'other')
    rebound = await registry.dispatch('code.repo_reader', args,
                                     replace(ctx, project_repo_root=other.project_repo_root))
    assert rebound.ok and rebound.output['repo_root'] != changed.output['repo_root']
    source.unlink()
    assert not (await registry.dispatch('code.repo_reader', args, ctx)).ok


@pytest.mark.asyncio
async def test_cache_separates_repositories_and_rejects_corrupt_evidence(tmp_path: Path) -> None:
    source, ctx = workspace(tmp_path)
    first = (await observed(ctx, 'libs/model.py', 0, 1))['output']
    other, other_ctx = workspace(tmp_path / 'other')
    other_ctx.extra = ctx.extra
    second = (await observed(other_ctx, 'libs/model.py', 0, 2))['output']
    assert source.read_bytes() == other.read_bytes()
    assert first['cache']['key'] != second['cache']['key']
    assert not second['cache']['hit']
    material = Path(ctx.extra['run_root']) / 'context/materials' / (first['cache']['material_ref'] + '.json')
    material.write_text('{}', encoding='utf-8')
    failed = await repo_reader_tool({'path': 'libs/model.py'}, ctx)
    assert not failed.ok and 'hash mismatch' in str(failed.error)
    assert material.read_text(encoding='utf-8') == '{}'


@pytest.mark.asyncio
async def test_cached_material_does_not_authorize_outside_or_ignored_paths(tmp_path: Path) -> None:
    source, ctx = workspace(tmp_path)
    await observed(ctx, 'libs/model.py', 0, 1)
    outside = tmp_path / 'private.py'
    outside.write_bytes(source.read_bytes())
    ignored = source.parent.parent / 'data/ignored.py'
    ignored.parent.mkdir()
    ignored.write_bytes(source.read_bytes())
    for name in (str(outside), '../private.py', 'data/ignored.py'):
        denied = await repo_reader_tool({'path': name}, ctx)
        assert not denied.ok and denied.output is None


def test_cache_limits_and_line_endings_preserve_reader_semantics(tmp_path: Path) -> None:
    source, ctx = workspace(tmp_path)
    options: dict[str, Any] = dict(repository=Path(ctx.project_repo_root), relative='libs/model.py', offset=0,
                   limit=6000, run_root=Path(ctx.extra['run_root']))
    small = source_window(source, **options, policy=replace(repository_policy(), max_cached_file_bytes=1))
    assert 'cache' not in small and '\r' not in small['content']
    before = small['source_bytes_sha256']
    source.write_bytes(source.read_bytes().replace(b'\r\n', b'\n'))
    changed = source_window(source, **options, policy=replace(repository_policy(), enabled=False))
    assert changed['source_bytes_sha256'] != before and changed['sha256'] == small['sha256']
    assert 'cache' not in changed
    assert not options['run_root'].exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('native_history', [True, False])
async def test_visible_reuse_preserves_native_pairs_prefix_and_originals(tmp_path: Path, native_history: bool) -> None:
    _, ctx = workspace(tmp_path)
    history = [await observed(ctx, 'libs/model.py', 0, n) for n in range(1, 5)]
    original = deepcopy(history)
    kwargs: dict[str, Any] = dict(pinned=[Message('system', 'Rules'), Message('user', 'Compare implementation')],
                  feedback='', candidate='', budget=128000, tools=(), policy=load_policy(), metadata={},
                  root=Path(ctx.extra['run_root']), agent='experiment', readback_available=True,
                  native=True, native_observation_history=native_history)
    first, _ = pack_native(history=history[:1], previous=None, **kwargs)
    packed, manifest = pack_native(history=history, previous=None, **kwargs)
    body = canonical(history[0]['output']['content'])[1:-1]
    assert sum(body in m.content for m in packed) == 1
    assert manifest['repository_duplicate_chars_removed'] > 10000
    assert history == original
    assert packed[:3] == first[:3]  # Stable instructions and first observation/call.
    if not native_history:
        assert [c.id for m in packed for c in m.tool_calls] == [m.tool_call_id for m in packed if m.role == 'tool']
    hashes = {digest(m.to_wire()) for m in packed}
    for message in packed:
        envelope = observation_envelope(message.content) or {}
        content = envelope.get('output', {}).get('content')
        if isinstance(content, dict) and 'included_message_sha256' in content:
            assert content['included_message_sha256'] in hashes
    ledger = grouped_read_history(history)
    assert len(ledger) == 1 and ledger[0]['read_count'] == 4


@pytest.mark.asyncio
async def test_offloaded_text_is_rehydrated_and_versions_are_not_conflated(tmp_path: Path) -> None:
    source, ctx = workspace(tmp_path)
    first = await observed(ctx, 'libs/model.py', 0, 1)
    source.with_name('other.py').write_text('unrelated\n' * 900, encoding='utf-8')
    second = await observed(ctx, 'libs/other.py', 0, 2)
    kwargs: dict[str, Any] = dict(pinned=[Message('user', 'Compare')], feedback='', candidate='', budget=8000,
        tools=(), policy=load_policy(), metadata={}, root=Path(ctx.extra['run_root']), agent='experiment',
        readback_available=True, native=True, native_observation_history=True)
    packed, before = pack_native(history=[first, second], previous=None, **kwargs)
    body = canonical(first['output']['content'])[1:-1]
    assert not any(body in m.content for m in packed)
    repeat = await observed(ctx, 'libs/model.py', 0, 3)
    assert repeat['output']['cache']['hit']
    packed, _ = pack_native(history=[first, second, repeat], previous=before['state'], **kwargs)
    assert sum(body in m.content for m in packed) == 1
    # A change outside this window is still a different file version.
    source.write_bytes(source.read_bytes() + b'# revised tail\n')
    revised = await observed(ctx, 'libs/model.py', 0, 4)
    kwargs['budget'] = 128000
    packed, _ = pack_native(history=[first, revised], previous=None, **kwargs)
    assert sum(body in m.content for m in packed) == 2


@pytest.mark.asyncio
async def test_index_tracks_ranges_versions_and_repeats_without_inventing_coverage(tmp_path: Path) -> None:
    source, ctx = workspace(tmp_path)
    history = [await observed(ctx, 'libs/model.py', offset, n)
               for n, offset in enumerate([0, 0, 0, 6000], 1)]
    index = repository_index(history, max_files=64, repeat_after=3)
    record = index['files'][0]
    assert record['repeated_read'] and record['same_window_reads'] == 3
    assert record['read_ranges'] == [[0, len(source.read_text(encoding='utf-8'))]]
    source.write_bytes(source.read_bytes() + b'# change\n')
    history.append(await observed(ctx, 'libs/model.py', 0, 5))
    record = repository_index(history, max_files=64, repeat_after=3)['files'][0]
    assert record['read_ranges'] == [[0, 6000]] and not record['repeated_read']
    failed = {**history[0], 'ok': False}
    assert len(grouped_read_history([history[0], failed])) == 2
    assert repository_index([failed], max_files=64, repeat_after=3)['files'] == []
