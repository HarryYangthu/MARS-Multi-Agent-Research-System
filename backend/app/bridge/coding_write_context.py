"""Read the run's verified write chain before asking for another code draft."""
from __future__ import annotations

import json

from app.bridge.completed_code_changes import completed_code_changes
from app.storage.run_store import RunHandle


def coding_write_context(run: RunHandle) -> str | None:
    """Supply complete bounded evidence, never an inferred or truncated manifest."""
    instruction = (
        '首稿优先按本任务整条真实成功写入链的当前净改动填写 files_changed；'
        '前轮保留的改动也必须列出，不能只列本轮新写文件。'
        'unchanged 属于复用，不声明新增。该清单不是批准或仿真成功。'
        '存在 warnings 或 complete=false 时证据不完整、不可声称核验通过；'
        '不得通过删除旧文件、重写相同内容或编造改动消除清单差异。'
    )
    try:
        # This reader applies configs/code_review.yaml max_records/max_scan_records
        # and validates archived postimages against the currently bound files.
        view = completed_code_changes(run, project=run.project)
        if not view['write_records'] and not view['items'] and not view['warnings']:
            return None
        warnings = list(view['warnings'])
        payload = {
            'schema': 'coding_written_code_manifest.v1', 'run_id': run.run_id,
            'project': run.project, 'complete': not warnings, 'verified': not warnings,
            'files_changed': [{'path': item['path'], 'type': item['change']} for item in view['items']],
            'unchanged': view['unchanged'], 'warnings': warnings,
            'write_records': view['write_records'], 'instruction': instruction,
        }
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Exceeding the existing evidence budget must not silently advertise a
        # partial list or turn a readable context into a new workflow hard stop.
        payload = {
            'schema': 'coding_written_code_manifest.v1', 'run_id': run.run_id,
            'project': run.project, 'complete': False, 'verified': False,
            'warnings': [str(exc)], 'instruction': instruction,
        }
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
