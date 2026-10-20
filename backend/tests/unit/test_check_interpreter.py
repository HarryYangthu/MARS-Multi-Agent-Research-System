"""Pure resolution and real compilation in a non-login environment."""
import json
import os
import subprocess
import shutil
import sys
import venv
from pathlib import Path

import pytest

from app.harness.runtime.git_runtime import resolve_git
from app.harness.tools.check_interpreter import check_argv


def test_python_placeholder_runs_real_compile_without_path(tmp_path: Path) -> None:
    source = tmp_path / 'source.py'
    source.write_text('value = 1\n')
    argv = check_argv(('python', '-m', 'compileall', '-q', str(source)), search_path='')
    assert argv[0] == sys.executable
    assert subprocess.run(argv, env={'PATH': ''}, capture_output=True).returncode == 0


def test_explicit_python_and_other_commands_remain_exact() -> None:
    for argv in [(sys.executable, '-m', 'pytest'), ('uv', 'run', 'pytest'), ('python3', '-V')]:
        assert check_argv(argv, search_path='') == argv


def test_selected_python_survives_a_changed_child_search_path() -> None:
    directory = str(Path(sys.executable).parent)
    selected = shutil.which('python', path=directory)
    assert selected is not None
    argv = check_argv(('python', '-c', 'import sys; print(sys.prefix)'), search_path=directory)
    assert argv[0] == str(Path(selected).absolute())
    result = subprocess.run(argv, env={'PATH': ''}, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == sys.prefix


def test_default_python_preserves_real_venv_with_base_python_on_path(tmp_path: Path) -> None:
    prefix = tmp_path / 'actual-check-venv'
    # POSIX shared-library builds need their original executable location;
    # Windows venvs keep their normal copied launcher.
    venv.EnvBuilder(with_pip=False, symlinks=os.name != 'nt').create(prefix)
    python = prefix / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    site = prefix / 'Lib/site-packages' if os.name == 'nt' else next(prefix.glob('lib/python*/site-packages'))
    (site / 'actual-test-runtime.pth').write_text(str(Path(pytest.__file__).resolve().parent.parent) + '\n')
    base_python = str(getattr(sys, '_base_executable', sys.executable))
    selected_git = resolve_git().executable
    # Exercise an actual venv and the actual installed pytest, without seeding
    # a global interpreter or downloading dependencies during this regression.
    environment = dict(os.environ)
    environment['PATH'] = str(Path(base_python).parent) + os.pathsep + os.defpath
    environment['MARS_GIT_EXECUTABLE'] = selected_git
    environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[2])
    environment.pop('PYTHONHOME', None)
    script = '''import json, os, shutil, subprocess, sys
from pathlib import Path
import pytest
from app.harness.runtime.git_runtime import git_child_environment, resolve_git
from app.harness.tools.check_interpreter import check_argv
assert Path(shutil.which('python') or shutil.which('python3')).parent != Path(sys.executable).parent
argv = check_argv(('python', '-c', "import json, pytest, shutil, subprocess, sys; print(json.dumps({'prefix':sys.prefix,'python':sys.executable,'pytest':pytest.__version__,'git':shutil.which('git'),'git_ok':subprocess.run(['git','--version'],capture_output=True).returncode == 0}))"))
assert argv[0] == os.path.abspath(sys.executable)
child = subprocess.run(argv, env=git_child_environment(), capture_output=True, text=True)
assert child.returncode == 0, child.stderr
actual = json.loads(child.stdout)
assert actual['prefix'] == sys.prefix
assert actual['python'] == argv[0]
assert actual['pytest'] == pytest.__version__
assert Path(actual['git']).resolve() == Path(resolve_git().executable).resolve()
assert actual['git_ok'] is True
print(json.dumps(actual))
'''
    result = subprocess.run([str(python), '-c', script], env=environment,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert Path(actual['prefix']) == prefix
    assert actual['python'] == str(python)
    assert actual['pytest'] == pytest.__version__
    assert actual['git_ok'] is True
