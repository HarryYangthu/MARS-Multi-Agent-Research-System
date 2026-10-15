"""Verify actual host receipts; no model, execution or success substitute."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.harness.reporting.execution_evidence import _receipt_checkpoints, execution_job_evidence


@pytest.fixture
def archive() -> tuple[Path, dict[str, Any]]:
    configured = os.environ.get('MARS_DIRECT_CHECKPOINT_RUN_ROOT')
    if not configured:
        pytest.skip('Requires an actual completed host job with direct checkpoint evidence')
    root = Path(configured).resolve(strict=True)
    jobs = [json.loads(path.read_text(encoding='utf-8')) for path in (root / 'execution/jobs').glob('*.json')]
    job = next(row for row in jobs if row.get('status') == 'completed' and any(
        str(item.get('path', '')).endswith('.pth') for item in row.get('evidence', [])))
    return root, job


def test_direct_checkpoint_is_opaque_and_does_not_hide_text(archive: tuple[Path, dict[str, Any]]) -> None:
    root, job = archive
    rows = [json.loads(line) for line in execution_job_evidence(root, job['run_id'], job['project']).splitlines()
            if line.startswith('{')]
    row = next(item for item in rows if item.get('experiment_id') == job['experiment_id'])
    assert 'evidence_error' not in row
    checkpoint = next(item for item in row['files'] if item['source'].endswith('.pth'))
    assert checkpoint['integrity'] == 'recorded_sha256_verified'
    assert checkpoint['text_readable'] is False and checkpoint['content_admitted'] is False
    assert 'preview' not in checkpoint and 'material_ref' not in checkpoint
    assert any(item['source'].endswith('/training.csv') and item['text_readable'] for item in row['files'])


def test_direct_checkpoint_requires_current_primary_receipt(archive: tuple[Path, dict[str, Any]]) -> None:
    root, original = archive
    job = copy.deepcopy(original)
    job['result']['fingerprint_hash'] = 'sha256:' + '0' * 64
    assert _receipt_checkpoints(root, job, job['evidence']) == {}
    job = copy.deepcopy(original)
    job['run_id'] = 'different-run'
    with pytest.raises(ValueError, match='identity/status'):
        _receipt_checkpoints(root, job, job['evidence'])
    job = copy.deepcopy(original)
    receipt = next(item for item in job['evidence'] if item['path'].endswith('execution_receipt.json'))
    receipt['sha256'] = 'sha256:' + '0' * 64
    with pytest.raises(ValueError, match='Primary receipt'):
        _receipt_checkpoints(root, job, job['evidence'])
