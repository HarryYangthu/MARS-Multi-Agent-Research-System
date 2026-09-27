'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { runtimeLayout } = require('../src/packaged-runtime.cjs');

async function layoutFiles(root) {
  for (const relative of ['python/bin/python3.11', 'mars/backend/app/main.py', 'mars/frontend/.next/BUILD_ID', 'mars/scripts/release/runtime_assets.txt']) {
    await fs.mkdir(path.dirname(path.join(root, relative)), { recursive: true });
    await fs.writeFile(path.join(root, relative), 'path-validation input; not an executable service');
  }
  await fs.writeFile(path.join(root, 'local-test.json'), JSON.stringify({ schema: 'mars.local-test-bundle.v1', release: false, arch: 'arm64' }));
}

test('packaged path selection uses actual bundled files and ignores developer override', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'mars-package-paths-'));
  try {
    await layoutFiles(root);
    const layout = await runtimeLayout({ packaged: true, resourcesPath: root, sourceRoot: '/missing/checkout', environment: { MARS_DESKTOP_PYTHON: '/untrusted/python' }, platform: 'darwin', arch: 'arm64' });
    assert.equal(layout.mode, 'packaged-local-test');
    assert.equal(layout.python, path.join(await fs.realpath(root), 'python/bin/python3.11'));
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});

test('packaged path selection rejects real escaping symlink and missing declaration', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'mars-package-rejection-'));
  try {
    await layoutFiles(root);
    const options = { packaged: true, resourcesPath: root, sourceRoot: '/missing/checkout', platform: 'darwin', arch: 'arm64' };
    await fs.unlink(path.join(root, 'python/bin/python3.11'));
    await fs.symlink(process.execPath, path.join(root, 'python/bin/python3.11'));
    await assert.rejects(runtimeLayout(options), /escapes/);
    await fs.unlink(path.join(root, 'local-test.json'));
    await assert.rejects(runtimeLayout(options), /ENOENT/);
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});

test('local test packaged target fails closed on another architecture', async () => {
  await assert.rejects(runtimeLayout({ packaged: true, resourcesPath: '/unused', sourceRoot: '/unused', platform: 'darwin', arch: 'x64' }), /Apple Silicon/);
});
