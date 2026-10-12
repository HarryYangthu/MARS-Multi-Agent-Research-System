"""Real threads/files/processes; no mocked locks or service responses."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys

import pytest
from filelock import Timeout

from app.harness.persistence import path_lock


def test_nested_lock_keeps_other_threads_and_processes_excluded(tmp_path: Path) -> None:
    path = tmp_path / 'state.lock'

    def contender() -> None:
        with pytest.raises(Timeout):
            with path_lock(path, timeout=0.05):
                pytest.fail('another thread entered the owned transaction')

    with ThreadPoolExecutor(1) as pool:
        with path_lock(path, timeout=2):
            with path_lock(path, timeout=2):
                pool.submit(contender).result(timeout=3)
            script = ('from filelock import FileLock, Timeout\nimport sys\n'
                      'try:\n with FileLock(sys.argv[1], timeout=0.05): sys.exit(2)\n'
                      'except Timeout: sys.exit(0)\n')
            result = subprocess.run([sys.executable, '-c', script, str(path)], timeout=5)
            assert result.returncode == 0
        with path_lock(path, timeout=1):
            pass


def test_contended_nested_transactions_preserve_all_updates(tmp_path: Path) -> None:
    path = tmp_path / 'state.lock'
    counter = tmp_path / 'counter.txt'
    counter.write_text('0')

    def update(_: int) -> None:
        for _ in range(15):
            with path_lock(path, timeout=5):
                value = int(counter.read_text())
                with path_lock(path, timeout=1):
                    counter.write_text(str(value + 1))

    with ThreadPoolExecutor(6) as pool:
        list(pool.map(update, range(6)))
    assert counter.read_text() == '90'


def test_nested_exception_releases_outer_transaction(tmp_path: Path) -> None:
    path = tmp_path / 'state.lock'
    with pytest.raises(ValueError):
        with path_lock(path, timeout=1):
            with path_lock(path, timeout=1):
                raise ValueError('abort transaction')
    with ThreadPoolExecutor(1) as pool:
        def acquire_again() -> None:
            with path_lock(path, timeout=1):
                pass
        pool.submit(acquire_again).result(timeout=3)


def test_concurrent_state_and_artifact_reads(tmp_path: Path) -> None:
    from app.harness.runtime.run_graph import RunGraph
    from app.storage.artifact_store import ArtifactStore
    from app.storage.run_state_store import RunStateStore
    from app.storage.run_store import RunStore

    run = RunStore(tmp_path).create(task='lock-read', project='pimc', entrypoint='idea')
    graph = RunGraph()
    graph.add_node('idea')
    graph.set_entrypoint('idea')
    state = RunStateStore(run)
    state.write(graph=graph, request={'task': run.task, 'project': run.project,
                'entrypoint': 'idea', 'standalone': True}, status='created', expected_revision=0)

    def read_transaction(index: int) -> None:
        for _ in range(10):
            if index % 2:
                assert RunStateStore(run).load() is not None
            else:
                assert ArtifactStore(run).list_versions(agent_dir='idea', stem='proposal') == []
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(read_transaction, range(8)))


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows extended path alias')
def test_extended_windows_path_is_same_nested_lock(tmp_path: Path) -> None:
    path = tmp_path / 'state.lock'
    extended = Path('\\\\?\\' + str(path.resolve()))
    for outer, inner in [(path, extended), (extended, path)]:
        with path_lock(outer, timeout=1):
            with path_lock(inner, timeout=0.1):
                pass
