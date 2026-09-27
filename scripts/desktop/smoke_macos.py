"""Move and launch the actual packaged app, then deny development reads for its services."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import socket
import subprocess
import sys

from scripts.desktop.verify_bundle import verify


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--deny-read', type=Path, action='append', required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ValueError('Smoke output must be a new directory')
    output.mkdir(parents=True, mode=0o700)
    relocated = output / 'Relocated app with spaces.app'
    subprocess.run(['/usr/bin/ditto', str(args.bundle.resolve()), str(relocated)], check=True)
    before = verify(relocated, args.manifest)
    data, home, temporary = output / 'data', output / 'home', output / 'tmp'
    for directory in (data, home, temporary):
        directory.mkdir(mode=0o700)
    denied = [str(path.resolve()) for path in args.deny_read]
    if any(relocated.is_relative_to(Path(path)) or output.is_relative_to(Path(path)) for path in denied):
        raise ValueError('Read-denial paths cannot contain the relocated app or test output')
    profile = '(version 1) (allow default)\n' + '\n'.join('(deny file-read* (subpath ' + json.dumps(path) + '))' for path in denied)
    env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': str(home), 'TMPDIR': str(temporary),
           'LANG': 'en_US.UTF-8', 'MARS_DESKTOP_DATA_DIR': str(data),
           'MARS_DESKTOP_PYTHON': '/deliberately-unavailable-developer-override'}
    # Keep a genuine unrelated listener alive; the desktop may only stop owned services.
    with socket.socket() as unrelated:
        unrelated.bind(('127.0.0.1', 0))
        unrelated.listen()
        with (output / 'app.stdout.log').open('wb') as stdout, (output / 'app.stderr.log').open('wb') as stderr:
            executable_name = plistlib.loads((relocated / 'Contents/Info.plist').read_bytes())['CFBundleExecutable']
            executable = relocated / 'Contents/MacOS' / executable_name
            child = subprocess.Popen([str(executable), '--smoke'], env=env, cwd=home,
                stdout=stdout, stderr=stderr, start_new_session=True)
            try:
                returncode = child.wait(timeout=150)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=30)
                raise RuntimeError('Actual packaged application exceeded its smoke timeout') from None
        if returncode:
            raise RuntimeError(f'Actual packaged application failed (exit {returncode}); inspect local bounded logs')
        receipt = json.loads((data / 'desktop-lifecycle.json').read_text())
        if receipt.get('mode') != 'packaged-local-test' or receipt.get('packaged') is not True:
            raise ValueError('The launched process was not the packaged application')
        checks = receipt['checks']
        if not (checks.get('ownedServicesExited') and checks.get('windowLoaded') and checks.get('rendererWebSocket')
                and checks.get('rendererApiStatus') == 200 and checks.get('unauthenticatedGatewayStatus') == 403
                and checks.get('unauthenticatedBackendStatus') == 401 and checks.get('unauthenticatedFrontendStatus') == 403
                and checks.get('crossOriginGatewayStatus') == 403):
            raise ValueError('Actual app checks did not all pass')
        for service in ('backend', 'frontend'):
            try:
                os.kill(receipt['services'][service]['pid'], 0)
            except ProcessLookupError:
                continue
            raise ValueError('An owned application service is still alive')
        with socket.create_connection(unrelated.getsockname(), timeout=1):
            pass
    # Chromium already uses a process sandbox and refuses a nested sandbox-exec
    # GUI launch on this host. Keep that renderer sandbox enabled; independently
    # run the actual bundled Python and Node services with development reads denied.
    probe = output / 'runtime_probe.cjs'
    shutil.copyfile(Path(__file__).with_name('runtime_probe.cjs'), probe)
    probe_env = {**env, 'ELECTRON_RUN_AS_NODE': '1'}
    probe_result = subprocess.run(['/usr/bin/sandbox-exec', '-p', profile, str(executable),
        str(probe), str(relocated / 'Contents/Resources'), str(output / 'isolated-services')],
        env=probe_env, cwd=home, capture_output=True, text=True, timeout=150, check=False)
    (output / 'runtime-probe.stdout.log').write_text(probe_result.stdout)
    (output / 'runtime-probe.stderr.log').write_text(probe_result.stderr)
    if probe_result.returncode:
        raise RuntimeError('Bundled service read-denial probe failed; inspect the local logs')
    isolation = json.loads((output / 'isolated-services/runtime-verification.json').read_text())
    after = verify(relocated, args.manifest)
    result = {'schema': 'mars.packaged-smoke.v1', 'packaged': True, 'relocated_path_with_spaces': True,
        'gui_nested_os_sandbox_tested': False, 'service_developer_directory_reads_denied': True,
        'denied_root_count': len(denied), 'runtime_isolation': isolation,
        'developer_python_override_ignored': True, 'unrelated_listener_alive': True,
        'inventory_unchanged': before == after, 'actual_checks': checks,
        'developer_id_signed': False, 'notarized': False, 'research_execution_verified': False}
    (output / 'smoke-verification.json').write_text(json.dumps(result, indent=2) + '\n')
    sys.stdout.write(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
