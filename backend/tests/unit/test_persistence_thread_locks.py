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
