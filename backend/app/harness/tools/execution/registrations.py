"""UI-managed execution registrations, stored beside the trusted host config.

Never read launch authority from a research checkout or imported project files.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def registration_path() -> Path:
    from app.harness.tools.config import _host_config_path
    path = _host_config_path('MARS_EXECUTION_CONFIG_PATH', 'execution.yaml')
    return path.with_name(path.stem + '.projects.json')


def registered_commands() -> dict[str, dict[str, Any]]:
    path = registration_path()
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        raise ValueError('项目训练启动配置损坏')
    for project, row in raw.items():
        if (not isinstance(row, dict) or re.fullmatch(r'folder_[0-9a-f]{32}', project) is None
                or row.get('id') != 'training_' + project or row.get('project_ids') != [project]
                or not isinstance(row.get('argv'), list) or not row['argv']
                or any(not isinstance(part, str) or not part for part in row['argv'])):
            raise ValueError('项目训练启动配置身份或命令无效')
    return raw
