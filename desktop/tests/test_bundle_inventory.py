"""Pure exact-content/path checks on genuine temporary files, no app substitutes."""
from pathlib import Path

import pytest

from scripts.desktop.verify_bundle import inventory


def test_inventory_detects_content_changes_and_empty_extra_directory(tmp_path: Path) -> None:
    (tmp_path / 'payload').write_bytes(b'original')
    before = inventory(tmp_path)
    (tmp_path / 'payload').write_bytes(b'changed')
    assert inventory(tmp_path) != before
    before = inventory(tmp_path)
    (tmp_path / 'extra-directory').mkdir()
    assert inventory(tmp_path) != before


def test_inventory_rejects_actual_escaping_link(tmp_path: Path) -> None:
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    outside = tmp_path / 'outside'
    outside.write_bytes(b'outside bundle')
    (bundle / 'link').symlink_to(outside)
    with pytest.raises(ValueError, match='escapes'):
        inventory(bundle)


def test_inventory_records_contained_symlink(tmp_path: Path) -> None:
    (tmp_path / 'payload').write_bytes(b'actual file')
    (tmp_path / 'link').symlink_to('payload')
    assert {'path': 'link', 'kind': 'symlink', 'target': 'payload'} in inventory(tmp_path)


@pytest.mark.parametrize('name', ['.env', '.env.local', '.env.production', '__editable__.mars.pth', 'pyvenv.cfg'])
def test_inventory_rejects_developer_environment_files(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_text('non-secret path validation input')
    with pytest.raises(ValueError, match='Developer state'):
        inventory(tmp_path)


def test_native_rpath_requires_contained_real_library(tmp_path: Path) -> None:
    import platform
    import shutil
    import subprocess
    from scripts.desktop.verify_bundle import native_dependencies
    if platform.system() != 'Darwin' or shutil.which('clang') is None:
        pytest.skip('Real macOS clang is required')
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    library_source = tmp_path / 'library.c'
    library_source.write_text('int local_value(void) { return 7; }\n')
    program_source = tmp_path / 'main.c'
    program_source.write_text('int local_value(void); int main(void) { return local_value() == 7 ? 0 : 1; }\n')
    library = outside / 'libvalue.dylib'
    program = bundle / 'program'
    subprocess.run(['clang', '-dynamiclib', str(library_source), '-o', str(library),
                    '-Wl,-install_name,@rpath/libvalue.dylib'], check=True)
    subprocess.run(['clang', str(program_source), '-o', str(program), '-L' + str(outside),
                    '-lvalue', '-Wl,-rpath,' + str(outside)], check=True)
    with pytest.raises(ValueError, match='escapes'):
        native_dependencies(bundle)
    subprocess.run(['/usr/bin/install_name_tool', '-delete_rpath', str(outside),
                    '-add_rpath', '@loader_path', str(program)], check=True)
    with pytest.raises(ValueError, match='Unresolved'):
        native_dependencies(bundle)
    shutil.copy2(library, bundle / library.name)
    assert native_dependencies(bundle)['static_loads_resolved'] is True
    subprocess.run([str(program)], check=True)


def test_native_loader_path_escape_rejected(tmp_path: Path) -> None:
    import platform
    import shutil
    import subprocess
    from scripts.desktop.verify_bundle import native_dependencies
    if platform.system() != 'Darwin' or shutil.which('clang') is None:
        pytest.skip('Real macOS clang is required')
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    code = tmp_path / 'library.c'
    code.write_text('int local_value(void) { return 7; }\n')
    library = tmp_path / 'liboutside.dylib'
    subprocess.run(['clang', '-dynamiclib', str(code), '-o', str(library),
                    '-Wl,-install_name,@loader_path/../liboutside.dylib'], check=True)
    code.write_text('int local_value(void); int main(void) { return local_value(); }\n')
    subprocess.run(['clang', str(code), str(library), '-o', str(bundle / 'program')], check=True)
    with pytest.raises(ValueError, match='escapes'):
        native_dependencies(bundle)
