'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { once } = require('node:events');
const { OwnedProcess, waitForPort } = require('../src/lifecycle.cjs');

test('shutdown terminates only owned real child processes', async () => {
  const owned = new OwnedProcess(process.execPath, ['-e', 'setInterval(() => {}, 1000)']);
  const unrelated = new OwnedProcess(process.execPath, ['-e', 'setInterval(() => {}, 1000)']);
  try {
    await Promise.all([once(owned.child, 'spawn'), once(unrelated.child, 'spawn')]);
    await owned.stop(1000);
    assert.equal(owned.closed, true);
    assert.doesNotThrow(() => process.kill(unrelated.child.pid, 0));
    assert.equal(unrelated.closed, false);
    await owned.stop(1000);
  } finally { await Promise.all([owned.stop(1000), unrelated.stop(1000)]); }
});
test('actual subprocess failure rejects startup instead of reporting service success', async () => {
  const failed = new OwnedProcess(process.execPath, ['-e', 'process.exit(7)']);
  await assert.rejects(waitForPort(failed, 1000), /failed to start/);
  assert.equal((await failed.done).code, 7);
});
test('missing executable rejects startup clearly', async () => {
  const failed = new OwnedProcess('/does-not-exist/mars-runtime', []);
  await assert.rejects(waitForPort(failed, 1000), /failed to start/);
  await failed.stop(1000);
});
test('shutdown escalates for a real child that ignores SIGTERM', { skip: process.platform === 'win32' }, async () => {
  const owned = new OwnedProcess(process.execPath, ['-e', "process.on('SIGTERM', () => {}); process.stdout.write('ready\\n'); setInterval(() => {}, 1000)"]);
  await once(owned.child.stdout, 'data');
  await owned.stop(40);
  assert.equal((await owned.done).signal, 'SIGKILL');
});
test('shutdown reaps the owned real process group including a child', { skip: process.platform === 'win32' }, async () => {
  const owned = new OwnedProcess(process.execPath, ['-e', "const { spawn } = require('node:child_process'); const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' }); child.on('spawn', () => process.stdout.write(String(child.pid) + '\\n')); setInterval(() => {}, 1000);"]);
  const [output] = await once(owned.child.stdout, 'data');
  const descendant = Number(output.toString().trim());
  assert.doesNotThrow(() => process.kill(descendant, 0));
  await owned.stop(1000);
  assert.equal(owned.closed, true);
  assert.throws(() => process.kill(descendant, 0), { code: 'ESRCH' });
});
