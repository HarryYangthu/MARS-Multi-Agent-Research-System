from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.storage.discovery_checkpoint_store import (
    CheckpointStatus,
    DiscoveryCheckpointStore,
)
from app.storage.discovery_common import (
    DiscoveryConflictError,
    DiscoveryCorruptionError,
    InvalidDiscoveryTransition,
)


def test_legacy_checkpoint_read_is_empty_and_lazy(tmp_path: Path) -> None:
    run_root = tmp_path / "legacy"
    run_root.mkdir()
    store = DiscoveryCheckpointStore(run_root, run_id="run-1")

    assert store.latest() is None
    assert store.replay() == []
    assert store.replay_state() is None
    assert store.recover() is None
    assert not (run_root / "discovery").exists()


def test_checkpoint_pause_resume_complete_and_replay(tmp_path: Path) -> None:
    store = DiscoveryCheckpointStore(tmp_path / "run", run_id="run-1")
    first = store.checkpoint(
        phase="generate",
        iteration=0,
        state={"cursor": 3},
        idempotency_key="checkpoint:1",
        expected_sequence=0,
    )

    assert store.checkpoint(
        phase="generate",
        iteration=0,
        state={"cursor": 3},
        idempotency_key="checkpoint:1",
    ) == first
    paused = store.pause(reason="human_review", expected_sequence=1)
    resumed = store.resume(expected_sequence=2)
    completed = store.complete(state={"cursor": 4}, expected_sequence=3)

    assert paused.status == CheckpointStatus.PAUSED
    assert resumed.status == CheckpointStatus.RUNNING
    assert completed.status == CheckpointStatus.COMPLETED
    assert [item.sequence for item in store.replay()] == [1, 2, 3, 4]
    assert store.replay_state(sequence=4) == {"cursor": 4}
    with pytest.raises(InvalidDiscoveryTransition):
        store.resume()


def test_checkpoint_idempotency_and_compare_and_swap(tmp_path: Path) -> None:
    store = DiscoveryCheckpointStore(tmp_path / "run", run_id="run-1")
    store.checkpoint(
        phase="generate",
        iteration=0,
        state={"cursor": 1},
        idempotency_key="same-key",
    )

    with pytest.raises(DiscoveryConflictError):
        store.checkpoint(
            phase="generate",
            iteration=0,
            state={"cursor": 2},
            idempotency_key="same-key",
        )
    with pytest.raises(DiscoveryConflictError):
        store.pause(reason="review", expected_sequence=99)


def test_crash_recovery_repairs_latest_and_pauses_running_work(tmp_path: Path) -> None:
    store = DiscoveryCheckpointStore(tmp_path / "run", run_id="run-1")
    running = store.checkpoint(
        phase="evaluate",
        iteration=2,
        state={"pending": ["candidate-2"]},
        idempotency_key="checkpoint:running",
    )
    store.latest_path.unlink()

    recovered = store.recover()

    assert recovered is not None
    assert recovered.sequence == running.sequence + 1
    assert recovered.status == CheckpointStatus.PAUSED
    assert recovered.reason == "crash_recovery"
    assert store.recover() == recovered
    assert store.resume().state == {"pending": ["candidate-2"]}


def test_latest_falls_back_to_hash_chain_but_corrupt_record_blocks_replay(
    tmp_path: Path,
) -> None:
    store = DiscoveryCheckpointStore(tmp_path / "run", run_id="run-1")
    checkpoint = store.checkpoint(
        phase="archive",
        iteration=1,
        state={"elite": "candidate-1"},
        idempotency_key="checkpoint:archive",
    )
    store.latest_path.write_text("not-json", encoding="utf-8")
    assert store.latest() == checkpoint

    record_path = next(store.checkpoints_dir.glob("*.json"))
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    payload["state"] = {"elite": "tampered"}
    record_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DiscoveryCorruptionError):
        store.replay()


def test_concurrent_checkpoints_have_contiguous_sequences(tmp_path: Path) -> None:
    store = DiscoveryCheckpointStore(tmp_path / "run", run_id="run-1")

    def save(index: int) -> int:
        return store.checkpoint(
            phase="search",
            iteration=index,
            state={"index": index},
            idempotency_key=f"checkpoint:{index}",
        ).sequence

    with ThreadPoolExecutor(max_workers=8) as executor:
        sequences = list(executor.map(save, range(24)))

    assert sorted(sequences) == list(range(1, 25))
    assert [item.sequence for item in store.replay()] == list(range(1, 25))
