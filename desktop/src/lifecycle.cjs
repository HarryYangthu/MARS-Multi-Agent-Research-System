'use strict';
const { spawn } = require('node:child_process');
const { EventEmitter } = require('node:events');
const http = require('node:http');
const { TOKEN_HEADER } = require('./security.cjs');

class OwnedProcess extends EventEmitter {
  constructor(command, args, options = {}) {
    super();
    this.child = spawn(command, args, { ...options, detached: process.platform !== 'win32', stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
    this.closed = false;
    this.failure = null;
    this.done = new Promise((resolve) => {
      this.child.once('error', (error) => { this.failure = error; this.emit('failure'); });
      this.child.once('close', (code, signal) => {
        this.closed = true;
        this.emit('closed', { code, signal });
        resolve({ code, signal });
      });
    });
    // Never mirror arbitrary child output: provider exceptions can contain credentials.
    this.child.stderr.resume();
  }
  async stop(timeoutMs) {
    if (this.stopping) return this.stopping;
    this.stopping = this.stopOwnedTree(timeoutMs);
    return this.stopping;
  }
  async stopOwnedTree(timeoutMs) {
    if (!Number.isInteger(this.child.pid)) { await this.done; return; }
    if (process.platform === 'win32') {
      if (this.closed) return;
      // Only our still-running child and its descendants are targeted.
      const killer = spawn('taskkill', ['/PID', String(this.child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
      await new Promise((resolve) => { killer.once('error', resolve); killer.once('close', resolve); });
    } else {
      try { process.kill(-this.child.pid, 'SIGTERM'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
    }
    const groupAlive = () => {
      if (process.platform === 'win32') return !this.closed;
      try { process.kill(-this.child.pid, 0); return true; } catch (error) { if (error.code === 'ESRCH') return false; if (error.code === 'EPERM') return true; throw error; }
    };
    const deadline = Date.now() + timeoutMs;
    while (groupAlive() && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 20));
    if (groupAlive()) {
      if (process.platform === 'win32') throw new Error('Owned Windows process tree did not stop.');
      try { process.kill(-this.child.pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
    }
    await this.done;
  }
}
function waitForPort(process, timeoutMs) {
  return new Promise((resolve, reject) => {
    let buffer = '';
    const cleanup = () => { clearTimeout(timer); process.child.stdout.off('data', onData); process.off('closed', onClosed); process.off('failure', onFailure); process.child.stdout.resume(); };
    const fail = () => { cleanup(); reject(new Error('A local service failed to start. Check the configured runtime and production build.')); };
    const onClosed = fail;
    const onFailure = fail;
    const onData = (data) => {
      buffer = (buffer + data.toString()).slice(-16384);
      for (const line of buffer.split('\n').slice(0, -1)) {
        if (!line.startsWith('MARS_DESKTOP_READY ')) continue;
        try {
          const value = JSON.parse(line.slice('MARS_DESKTOP_READY '.length));
          if (Number.isInteger(value.port) && value.port > 0 && value.port <= 65535 && value.pid === process.child.pid) {
            cleanup(); resolve(value.port); return;
          }
        } catch { /* Ignore incomplete control records. */ }
      }
      buffer = buffer.slice(buffer.lastIndexOf('\n') + 1);
    };
    const timer = setTimeout(fail, timeoutMs);
    process.child.stdout.on('data', onData); process.once('closed', onClosed); process.once('failure', onFailure);
    if (process.closed || process.failure) fail();
  });
}
function getJSON(url, token, origin) {
  return new Promise((resolve, reject) => {
    const request = http.get(url, { headers: { [TOKEN_HEADER]: token, ...(origin ? { Origin: origin } : {}) }, timeout: 1500 }, (response) => {
      let body = '';
      response.on('data', (chunk) => { body += chunk; if (body.length > 65536) request.destroy(); });
      response.on('end', () => {
        try { if (response.statusCode !== 200) throw new Error('not ready'); resolve(JSON.parse(body)); } catch { reject(new Error('Local service is not ready.')); }
      });
    });
    request.on('timeout', () => request.destroy(new Error('Local service timed out.')));
    request.on('error', reject);
  });
}
function getStatus(url, headers = {}) {
  return new Promise((resolve, reject) => {
    const request = http.get(url, { headers, timeout: 1500 }, (response) => { response.resume(); resolve(response.statusCode); });
    request.on('timeout', () => request.destroy(new Error('Local service timed out.')));
    request.on('error', reject);
  });
}
async function waitForBackend(process, port, token, origin, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (process.closed) throw new Error('Backend exited before becoming ready.');
    try {
      const health = await getJSON(`http://127.0.0.1:${port}/health`, token, origin);
      if (health.status === 'ok' && health.service === 'mars-backend') return health;
    } catch { /* Startup may still be importing the real backend. */ }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error('The real backend did not become ready before the startup timeout.');
}
module.exports = { OwnedProcess, waitForPort, waitForBackend, getJSON, getStatus };
