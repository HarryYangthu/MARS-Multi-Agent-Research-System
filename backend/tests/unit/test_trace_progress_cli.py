"""Progress reads real files and never invents a successful execution state."""
import json
from pathlib import Path

from scripts.watch_agent_trace import progress_snapshot


def test_missing_trace_is_not_reported_as_completed(tmp_path: Path) -> None:
    assert progress_snapshot(tmp_path) == []


def test_unreadable_trace_remains_an_explicit_diagnostic(tmp_path: Path) -> None:
    path = tmp_path / "agent_traces/idea/invocation/facts.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"status":')
    result = progress_snapshot(tmp_path)
    assert result[0]["status"] == "unreadable"
    assert result[0]["error_type"] == "JSONDecodeError"
    # Incomplete metadata must also not be presented as a finished run.
    path.write_text(json.dumps({"status": "passed"}))
    assert progress_snapshot(tmp_path)[0]["status"] == "unreadable"
