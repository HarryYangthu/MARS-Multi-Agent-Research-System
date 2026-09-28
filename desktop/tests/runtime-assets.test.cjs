'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { seedRuntime } = require('../src/runtime-assets.cjs');

test('real runtime assets seed to isolated data directory and preserve subsequent user edits', async () => {
  const dataRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'mars-desktop-assets-'));
  try {
    const sourceRoot = path.resolve(__dirname, '../..');
    const workspace = await seedRuntime(sourceRoot, dataRoot);
    assert.equal(await fs.readFile(path.join(workspace, 'configs/agents.yaml'), 'utf8'), await fs.readFile(path.join(sourceRoot, 'configs/agents.yaml'), 'utf8'));
    await assert.rejects(fs.access(path.join(workspace, '.env')));
    const custom = path.join(workspace, 'user-note.md');
    await fs.writeFile(custom, 'user-owned');
    assert.equal(await seedRuntime(sourceRoot, dataRoot), workspace);
    assert.equal(await fs.readFile(custom, 'utf8'), 'user-owned');
  } finally { await fs.rm(dataRoot, { recursive: true, force: true }); }
});
