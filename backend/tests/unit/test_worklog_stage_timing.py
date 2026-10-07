"""Pure timestamp projection: these authored events do not claim Agent execution."""
from pathlib import Path

from app.api.timeline import _elapsed_seconds, _review_worklog, _worklog_items, _worklog_started_at
from app.storage.run_store import RunStore


def test_stage_clock_excludes_upstream_work_and_unstarted_stage_stays_unknown(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task='stage-clock', project='authored-timestamps')
    run.write_event('agent_events', {'event': 'agent.state_changed', 'agent': 'idea',
        'node': 'idea', 'to_state': 'running', 'timestamp': '2026-10-07T08:00:00Z'})
    run.write_event('agent_events', {'event': 'agent.state_changed', 'agent': 'experiment',
        'node': 'experiment', 'to_state': 'running', 'timestamp': '2026-10-07T11:34:00Z'})
    run.write_event('agent_events', {'event': 'agent.state_changed', 'agent': 'experiment',
        'node': 'experiment', 'to_state': 'waiting_review', 'timestamp': '2026-10-07T11:40:00Z'})
    items = _worklog_items(run=run, agent_filter='experiment')
    start = _worklog_started_at(run, items, 'experiment')
    assert start == '2026-10-07T11:34:00Z'
    assert _elapsed_seconds(start, items[-1].timestamp) == 360.0
    assert items[-1].elapsed_seconds == 360.0
    assert all(item.agent == 'experiment' for item in items)
    assert _worklog_items(run=run, agent_filter='coding') == []
    assert _worklog_started_at(run, [], 'coding') == ''
    assert any(item.id == '0:run:created' for item in _worklog_items(run=run))


def test_edits_and_approvals_are_not_displayed_as_rejections() -> None:
    for action, expected in [('approve', '人工审核已批准'), ('edit', '人工编辑已保存'), ('comment', '收到人工评论')]:
        item = _review_worklog(index=1, payload={'agent': 'experiment', 'action': action,
            'detail': {'version': 'v2', 'text': 'Reviewer note.'}})
        assert item.title == expected and item.status == action
        assert '生成新版产物' not in item.next_action and '重新生成方案' not in item.detail
