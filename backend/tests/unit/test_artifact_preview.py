"""Draft preview reads genuine files without granting invalid code approval."""
from pathlib import Path

import pytest

from app.api import dependencies
from app.api.artifacts import _read_view
from app.storage.run_store import RunStore


@pytest.mark.parametrize('text', [
    '---\nschema: code_spec.v1\nfiles_changed: [unfinished\n---\nDraft body remains editable.\n',
    'A human-authored draft without metadata.\n',
])
def test_bad_draft_remains_visible_as_original_text(tmp_path: Path, text: str) -> None:
    previous = dependencies._run_store
    store = RunStore(tmp_path / 'runs')
    dependencies._run_store = store
    try:
        run = store.create(project='synthetic_regression', task='editable draft preview')
        path = run.root / 'coding/code_spec.v1.md'
        path.write_text(text, encoding='utf-8')
        view = _read_view(run.run_id, 'coding', 'code_spec', 'v1')
        assert view.text == text and view.valid is False and view.errors
        assert path.read_text(encoding='utf-8') == text
        assert not (run.root / 'coding/code_spec.approved.md').exists()
        assert not list((run.root / 'execution').glob('**/job.json'))
    finally:
        dependencies._run_store = previous
