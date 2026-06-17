from __future__ import annotations

from app.harness.observability.events import make_event, normalize_event


def test_event_envelope_redacts_sensitive_payload() -> None:
    event = make_event(
        run_id="run1",
        project="moe-pimc",
        channel="run.run1.tool",
        kind="tool.completed",
        source={"component": "test"},
        payload={
            "safe": "ok",
            "token": "secret-token",
            "nested": {"authorization": "Bearer abc"},
        },
    )

    assert event["schema"] == "event.v1"
    assert event["payload"]["safe"] == "ok"
    assert event["payload"]["token"] == "[redacted]"
    assert event["payload"]["nested"]["authorization"] == "[redacted]"


def test_legacy_event_normalizes_into_event_envelope() -> None:
    event = normalize_event(
        {"event": "hitl.review_required", "agent": "coding", "timestamp": "2026-06-17T00:00:00+00:00"},
        run_id="run1",
        project="moe-pimc",
        default_channel="hitl_events",
        default_kind="hitl.event",
    )

    assert event["schema"] == "event.v1"
    assert event["kind"] == "hitl.review_required"
    assert event["severity"] == "warning"
    assert event["payload"]["agent"] == "coding"
