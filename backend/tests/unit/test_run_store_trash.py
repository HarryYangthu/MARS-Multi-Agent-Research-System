from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.storage.run_store import RUN_SUBDIRS, TRASH_RETENTION_DAYS, RunStore


def test_trash_restore_and_permanent_delete_keep_artifacts_together(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    now = datetime.now(tz=timezone.utc)
    run = store.create(task="Trash me", project="pimc", now=now)
    artifact = run.subdir("idea") / "idea_proposal.v1.md"
    artifact.write_text("artifact body", encoding="utf-8")

    trashed = store.trash(run.run_id, now=now)

    assert store.get(run.run_id) is None
    assert run.run_id not in {r.run_id for r in store.list()}
    assert trashed.run_id == run.run_id
    assert trashed.meta["trash_retention_days"] == TRASH_RETENTION_DAYS
    assert TRASH_RETENTION_DAYS <= trashed.days_remaining <= TRASH_RETENTION_DAYS + 1
    assert (store.trash_root / run.run_id / "idea" / artifact.name).exists()
    for subdir in RUN_SUBDIRS:
        assert (store.trash_root / run.run_id / subdir).is_dir()

    restored = store.restore(run.run_id)

    assert restored.run_id == run.run_id
    assert (restored.root / "idea" / artifact.name).read_text(encoding="utf-8") == "artifact body"
    assert store.get_trashed(run.run_id) is None

    trashed_again = store.trash(run.run_id, now=now)
    assert trashed_again.run_id == run.run_id
    store.delete_trashed(run.run_id)

    assert store.get(run.run_id) is None
    assert store.get_trashed(run.run_id) is None
    assert not (store.trash_root / run.run_id).exists()


def test_expired_trash_is_purged(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    now = datetime(2026, 7, 6, 8, 0, tzinfo=timezone.utc)
    run = store.create(task="Old trash", project="pimc", now=now)
    store.trash(run.run_id, now=now)

    purged = store.purge_expired_trash(
        now=now + timedelta(days=TRASH_RETENTION_DAYS, seconds=1)
    )

    assert purged == 1
    assert store.list_trashed(purge_expired=False) == []


def test_invalid_run_id_is_rejected(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")

    with pytest.raises(ValueError):
        store.trash("../outside")
