"""Pure resolution and real compilation in a non-login environment."""
import subprocess
import shutil
import sys
from pathlib import Path

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
