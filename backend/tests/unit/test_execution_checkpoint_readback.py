"""Genuine archived checkpoint/text readback plus real filesystem denial checks."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any

import pytest

from app.harness.reporting.execution_evidence import (
    _checkpoint_item, _execution_relative, _streamed_event_item, execution_job_evidence,
)
from app.harness.reporting.source_refs import report_source_catalogue
from app.harness.tools.execution.command_preflight import bounded_error
from app.harness.tools.registry import ToolContext, get_registry


@pytest.fixture
def actual_checkpoint_archive(tmp_path: Path) -> tuple[Path, str, str]:
    configured = os.environ.get('MARS_WRITING_CHECKPOINT_RUN_ROOT')
    if not configured:
        pytest.skip('Select a genuine archived job with an indexed checkpoint; no substitute execution is generated')
    source = Path(configured).expanduser().resolve(strict=True)
    journal, job = next((path, json.loads(path.read_text())) for path in (source / 'execution/jobs').glob('*.json')
                        if json.loads(path.read_text()).get('status') == 'completed')
    copied = tmp_path / 'actual-archive-copy'
    copied.mkdir()
    found_checkpoint = False
    for evidence in job['evidence']:
        path = Path(evidence['path'])
        if not path.is_absolute():
            path = source / path
        target = copied / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        evidence['path'] = target.relative_to(copied).as_posix()
        if path.name == 'artifact_index.json':
            for entry in json.loads(path.read_text())['files']:
                child = path.parent / entry['path']
                target_child = copied / child.relative_to(source)
                target_child.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(child, target_child)
                found_checkpoint = found_checkpoint or child.suffix == '.pth'
    assert found_checkpoint, 'The selected genuine run must reproduce the indexed checkpoint condition'
    # Relocate only the host envelope of an actual archived recovery. The
    # request, result, measurements and original failed receipt stay byte-exact.
    for evidence in job['evidence']:
        path = copied / evidence['path']
        if path.name != 'execution_receipt.json':
            continue
        receipt = json.loads(path.read_text())
        recovery = receipt.get('recovery')
        if not isinstance(recovery, dict):
            continue
        old_hash = evidence['sha256']
        for key in ('source_receipt_path', 'source_request_path', 'source_result_path'):
            recovery[key] = (copied / Path(recovery[key]).relative_to(source)).as_posix()
        path.write_text(json.dumps(receipt))
        digest = 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()
        evidence['sha256'] = digest
        if job['result']['fingerprint_hash'] == old_hash:
            job['result']['fingerprint_hash'] = digest
    copied_journal = copied / journal.relative_to(source)
    copied_journal.parent.mkdir(parents=True, exist_ok=True)
    copied_journal.write_text(json.dumps(job))
    return copied, job['run_id'], job['project']


def records(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.startswith('{')]


@pytest.mark.asyncio
async def test_actual_receipt_manifest_summary_and_steps_have_working_paired_readers(
    actual_checkpoint_archive: tuple[Path, str, str],
) -> None:
    root, run_id, project = actual_checkpoint_archive
    rows = records(execution_job_evidence(root, run_id, project, admit_materials=True))
    assert len(rows) == 1 and 'evidence_error' not in rows[0]
    assert rows[0]['status'] == 'completed'
    files = rows[0]['files']
    registry = get_registry()
    # The v3 BaseAgent loop grants exactly this validated supplemental read scope.
    scope = registry.scope_for_read_tools('writing', ('context.read_material',))
    basenames = ['execution_receipt.json', 'manifest.json', 'summary.json', 'steps.jsonl']
    basenames.extend(name for name in ('approved.yaml', 'epochs.jsonl')
                     if any(Path(item['source']).name == name for item in files))
    for basename in basenames:
        item = next(file for file in files if Path(file['source']).name == basename)
        assert item['integrity'] == 'recorded_sha256_verified'
        assert item['sha256'] == hashlib.sha256((root / item['source']).read_bytes()).hexdigest()
        if basename == 'steps.jsonl' and item['bytes'] > 131072:
            assert item['text_readable'] is False and 'material_ref' not in item
            assert item['text_limit'] == 'bounded text readback capacity exceeded'
            continue
        reader = item['reader']
        assert reader['tool'] == 'context.read_material' and reader['args']['ref'] == item['material_ref']
        content, offset = '', 0
        while True:
            result = await registry.dispatch(reader['tool'], {**reader['args'], 'char_offset': offset},
                ToolContext(run_id, project, 'writing', extra={'run_root': str(root)}, supplemental_read_scope=scope))
            assert result.ok, result.error
            assert isinstance(result.output, dict)
            content += result.output['content']
            next_offset = result.output['next_offset']
            if next_offset is None:
                break
            assert next_offset > offset
            offset = next_offset
        assert 'source: ' + item['source'] in content
        assert 'source_sha256: ' + item['sha256'] in content
        assert bounded_error((root / item['source']).read_text(), max_chars=16777216) in content
    opaque = next(item for item in files if Path(item['source']).suffix == '.pth')
    assert opaque['integrity'] == 'recorded_sha256_verified'
    assert opaque['sha256'] == hashlib.sha256((root / opaque['source']).read_bytes()).hexdigest()
    assert opaque['read_method'] == 'streamed_hash_only' and opaque['content_admitted'] is False
    assert opaque['text_readable'] is False and 'material_ref' not in opaque and 'preview' not in opaque
    catalogue = report_source_catalogue(root)
    assert '不是代码仓路径' in str(catalogue['instruction'])
    assert 'context.read_material' in str(catalogue['instruction'])


def test_modified_real_checkpoint_cannot_admit_other_text_evidence(
    actual_checkpoint_archive: tuple[Path, str, str],
) -> None:
    root, run_id, project = actual_checkpoint_archive
    checkpoint = next(root.rglob('epoch.pth'))
    checkpoint.write_bytes(checkpoint.read_bytes() + b'\x00')
    rows = records(execution_job_evidence(root, run_id, project, admit_materials=True))
    assert 'checkpoint size differs' in rows[0]['evidence_error']
    assert 'metrics' not in rows[0] and not (root / 'context/materials').exists()


@pytest.mark.parametrize('path', ['execution/.env/epoch.pth', 'execution/.ssh/epoch.pth',
    'execution/.git/epoch.pth', 'execution/private.key', 'execution/private.pem'])
def test_checkpoint_metadata_does_not_allow_control_or_credential_paths(tmp_path: Path, path: str) -> None:
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'Negative filesystem admission input; not a model or execution result.')
    with pytest.raises(ValueError, match='forbidden source'):
        _execution_relative(tmp_path, path, indexed_checkpoint=True)


def test_checkpoint_metadata_still_rejects_symlink_and_outside_files(tmp_path: Path) -> None:
    root = tmp_path / 'run'
    (root / 'execution').mkdir(parents=True)
    outside = tmp_path / 'outside.pth'
    outside.write_bytes(b'Negative filesystem input.')
    (root / 'execution/link.pth').symlink_to(outside)
    for name in ('execution/link.pth', '../outside.pth', str(outside)):
        with pytest.raises(ValueError):
            _execution_relative(root, name, indexed_checkpoint=True)


def test_oversized_real_sparse_checkpoint_is_declared_not_bytes_verified(tmp_path: Path) -> None:
    name = 'execution/output/large.pth'
    target = tmp_path / name
    target.parent.mkdir(parents=True)
    size = 32 * 1024 * 1024
    with target.open('wb') as stream:
        stream.truncate(size)  # Actual sparse file; no in-memory weight allocation or model execution.
    digest = hashlib.sha256()
    with target.open('rb') as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
    path = tmp_path / _execution_relative(tmp_path, name, indexed_checkpoint=True)
    item = _checkpoint_item(path, name, {'sha256': digest.hexdigest(), 'bytes': size}, 16 * 1024 * 1024)
    assert item['integrity'] == 'index_declared_only' and item['bytes_hash_verified'] is False
    assert item['declared_sha256'] == digest.hexdigest() and 'sha256' not in item
    assert item['hash_verification'] == 'deferred_due_to_streaming_budget'
    assert item['content_admitted'] is False and item['text_readable'] is False


def test_oversized_real_sparse_event_is_stream_verified_without_content_admission(tmp_path: Path) -> None:
    name = 'execution/output/tensorboard/events.out.tfevents.actual-filesystem-test'
    path = tmp_path / name
    path.parent.mkdir(parents=True)
    size = 18 * 1024 * 1024
    with path.open('wb') as stream:
        stream.truncate(size)
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            checksum.update(block)
    expected = checksum.hexdigest()
    verified = tmp_path / _execution_relative(tmp_path, name)
    item = _streamed_event_item(verified, name, 'sha256:' + expected, size, 134217728)
    assert item['sha256'] == expected and item['bytes'] == size
    assert item['read_method'] == 'streamed_hash_only' and item['content_admitted'] is False
    assert item['text_readable'] is False and 'material_ref' not in item and 'preview' not in item
    with pytest.raises(ValueError, match='capacity'):
        _streamed_event_item(verified, name, expected, size, size - 1)
    with pytest.raises(ValueError, match='size differs'):
        _streamed_event_item(verified, name, expected, size - 1, 134217728)
    with path.open('r+b') as stream:
        stream.write(b'Changed actual event file bytes')
    with pytest.raises(ValueError, match='recorded digest'):
        _streamed_event_item(verified, name, expected, size, 134217728)


def test_genuine_large_event_does_not_drop_completed_epoch_handoff(
    actual_checkpoint_archive: tuple[Path, str, str],
) -> None:
    root, run_id, project = actual_checkpoint_archive
    if not any(path.stat().st_size > 16777216 for path in root.rglob('events.out.tfevents.*')):
        pytest.skip('Select a genuine run with an oversized native TensorBoard event')
    text = execution_job_evidence(root, run_id, project, admit_materials=True)
    rows = records(text)
    assert len(rows) == 1 and 'evidence_error' not in rows[0]
    completed = rows[0]
    assert completed['status'] == 'completed' and completed['metrics']['epochs'] == 100
    assert completed['metrics']['optimizer_steps'] == 34500
    assert completed['measurement_recovered_without_retraining'] is True
    assert len(completed['files']) >= 29
    assert len(text) < 200000  # Metadata/previews, not the full 34,500-point curve.
    events = [item for item in completed['files'] if Path(item['source']).name.startswith('events.out.tfevents.')]
    assert events
    for item in events:
        assert item['content_admitted'] is False and item['text_readable'] is False
        assert item['read_method'] == 'streamed_hash_only' and item['integrity'] == 'recorded_sha256_verified'
        assert 'material_ref' not in item and 'preview' not in item
    for basename in ('manifest.json', 'approved.yaml', 'summary.json', 'epochs.jsonl'):
        item = next(item for item in completed['files'] if Path(item['source']).name == basename)
        assert item['text_readable'] is True and item['reader']['args']['ref'] == item['material_ref']


@pytest.mark.parametrize('field', ['bytes', 'sha256'])
def test_genuine_direct_and_indexed_event_declarations_must_agree(
    actual_checkpoint_archive: tuple[Path, str, str], field: str,
) -> None:
    root, run_id, project = actual_checkpoint_archive
    index_path = next(root.rglob('artifact_index.json'))
    index = json.loads(index_path.read_text())
    entry = next((entry for entry in index['files']
                  if Path(entry['path']).name.startswith('events.out.tfevents.')), None)
    if entry is None:
        pytest.skip('Select a genuine run with native TensorBoard events')
    entry[field] = entry[field] + 1 if field == 'bytes' else '0' * 64
    index_path.write_text(json.dumps(index))
    journal = next((root / 'execution/jobs').glob('*.json'))
    job = json.loads(journal.read_text())
    for evidence in job['evidence']:
        if evidence['path'] == index_path.relative_to(root).as_posix():
            evidence['sha256'] = 'sha256:' + hashlib.sha256(index_path.read_bytes()).hexdigest()
    journal.write_text(json.dumps(job))
    rows = records(execution_job_evidence(root, run_id, project))
    assert 'Indexed TensorBoard event differs' in rows[0]['evidence_error']
    assert 'metrics' not in rows[0]
