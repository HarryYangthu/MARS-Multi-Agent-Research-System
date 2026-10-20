"""Real fresh interpreters and real rendering preserve the initialized environment."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.harness.tools.process_runtime import sanitized_subprocess_environment


_RENDER = r'''
import hashlib, importlib.util, json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from app.harness.runtime.plot_environment import initialize_plot_environment
from app.harness.tools.process_runtime import sanitized_subprocess_environment

original = os.environ.get('MPLCONFIGDIR')
# Core installs still render actual PNGs without optional in-process Matplotlib.
matplotlib_available = importlib.util.find_spec('matplotlib') is not None
assert 'matplotlib' not in sys.modules
assert initialize_plot_environment() is True
assert 'matplotlib' not in sys.modules
selected = os.environ['MPLCONFIGDIR']
assert Path(selected).is_dir()
if original:
    assert selected == original
else:
    assert selected == str(Path(tempfile.gettempdir()) / 'mars-matplotlib')
assert initialize_plot_environment() is True
assert os.environ['MPLCONFIGDIR'] == selected
from app.execution.pim_cancellation import plot_loss_curves

def fingerprint():
    current = sanitized_subprocess_environment()
    return current, hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()

before, digest = fingerprint()
digests = [digest]
images = []
for index in (1, 2):
    image = Path(sys.argv[2]) / ('render-' + str(index) + '.png')
    plot_loss_curves({'rendering_fixture': [1.0, 0.5, 0.25]}, image,
                     value_scale='native', title='Renderer environment test')
    assert image.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    current, digest = fingerprint()
    changed = sorted(key for key in set(before) | set(current) if before.get(key) != current.get(key))
    assert not changed, {'changed_environment_names': changed, 'matplotlib_available': matplotlib_available}
    digests.append(digest)
    images.append(str(image))
matplotlib_loaded = 'matplotlib' in sys.modules
assert matplotlib_loaded == matplotlib_available, {
    'matplotlib_available': matplotlib_available, 'matplotlib_loaded': matplotlib_loaded,
    'python': sys.executable,
}
assert os.environ['MPLCONFIGDIR'] == selected
print(json.dumps({'original': original, 'selected': selected, 'directory_exists': Path(selected).is_dir(),
                  'environment_digests': digests, 'images': images,
                  'matplotlib_available': matplotlib_available, 'matplotlib_loaded_for_plot': matplotlib_loaded}))
'''


@pytest.mark.parametrize("mode", ["unset", "empty", "preset"])
def test_fresh_process_initialization_and_two_real_plots_keep_environment_digest(
    tmp_path: Path, mode: str,
) -> None:
    backend = Path(__file__).resolve().parents[2]
    environment = sanitized_subprocess_environment()
    temporary = tmp_path / "process-temp"
    temporary.mkdir()
    environment["TMPDIR"] = str(temporary)
    explicit = str(tmp_path / "configured-cache")
    if mode == "preset":
        environment["MPLCONFIGDIR"] = explicit
    elif mode == "empty":
        environment["MPLCONFIGDIR"] = ""
    else:
        environment.pop("MPLCONFIGDIR", None)
    completed = subprocess.run([sys.executable, "-c", _RENDER, str(backend), str(tmp_path)],
        env=environment, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert len(set(result["environment_digests"])) == 1
    assert result["directory_exists"]
    assert result["matplotlib_loaded_for_plot"] == result["matplotlib_available"], result
    assert result["selected"] == explicit if mode == "preset" else result["original"] in (None, "")
    assert len(result["images"]) == 2 and all(Path(name).is_file() for name in result["images"])


def test_existing_file_cache_failure_is_nonfatal_and_does_not_claim_stability(tmp_path: Path) -> None:
    path = tmp_path / "not-a-directory"
    path.write_text("Actual file blocks optional cache directory creation.")
    environment = sanitized_subprocess_environment(overrides={"MPLCONFIGDIR": str(path)})
    program = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from app.harness.runtime.plot_environment import initialize_plot_environment
original = os.environ['MPLCONFIGDIR']
ready = initialize_plot_environment()
assert ready is False and os.environ['MPLCONFIGDIR'] == original
assert 'matplotlib' not in sys.modules
print(json.dumps({'ready': ready, 'preserved': original}))
'''
    completed = subprocess.run([sys.executable, "-c", program, str(Path(__file__).resolve().parents[2])],
        env=environment, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"ready": False, "preserved": str(path)}
    assert "MARS" in completed.stderr and "尚未保证" in completed.stderr
    assert path.read_text() == "Actual file blocks optional cache directory creation."
