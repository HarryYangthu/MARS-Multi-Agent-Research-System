'use strict';
// Actual local sockets exercise proxy boundaries only. These listeners never
// imitate a MARS API, model response, tool result or successful research run.
const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const net = require('node:net');
const { createGateway } = require('../src/gateway.cjs');
const { TOKEN_HEADER } = require('../src/security.cjs');
const token = 'b'.repeat(64);

async function recordingListener() {
  const requests = [];
  const upgrades = [];
  const server = http.createServer((request, response) => {
    requests.push(request.url);
    response.writeHead(418); response.end('Boundary test listener.');
  });
  server.on('upgrade', (request, socket) => {
    upgrades.push(request.url);
    socket.end('HTTP/1.1 426 Upgrade Required\r\nConnection: close\r\n\r\n');
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { requests, upgrades, port: server.address().port,
    close: () => { server.closeAllConnections(); return new Promise((resolve) => server.close(resolve)); } };
}
function rawRequest(origin, target) {
  const url = new URL(origin);
  return new Promise((resolve, reject) => {
    const request = http.request({ hostname: url.hostname, port: url.port, path: target,
      headers: { [TOKEN_HEADER]: token, Connection: 'close' } }, (response) => {
      response.resume(); response.once('end', () => resolve(response.statusCode));
    });
    request.once('error', reject); request.end();
  });
}
function rawUpgrade(origin, target) {
  const url = new URL(origin);
  return new Promise((resolve, reject) => {
    const socket = net.connect(Number(url.port), url.hostname, () => {
      socket.write(`GET ${target} HTTP/1.1\r\nHost: ${url.host}\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n${TOKEN_HEADER}: ${token}\r\n\r\n`);
    });
    socket.setTimeout(2000, () => { socket.destroy(); reject(new Error('Boundary test socket timeout.')); });
    socket.once('data', (data) => { socket.destroy(); resolve(data.toString()); });
    socket.once('error', reject);
  });
}
test('real gateway never forwards ambiguous HTTP paths and preserves encoded identifiers', async () => {
  const backend = await recordingListener();
  const frontend = await recordingListener();
  const gateway = await createGateway({ token, timeoutMs: 1000 });
  gateway.setTargets({ backend: backend.port, frontend: frontend.port });
  try {
    for (const target of ['/%61pi/projects', '/API/projects', '/api%2fruns', '/%2561pi/projects', '/runs/%2e%2e/api/projects', '/api/%zz']) {
      assert.equal(await rawRequest(gateway.origin, target), 400, target);
    }
    assert.deepEqual(backend.requests, []);
    assert.deepEqual(frontend.requests, []);
    const api = '/api/runs/run%20one?source=%2Fpapers%2Fone';
    const page = '/runs/run%2Bone?display=%E4%B8%AD%E6%96%87';
    assert.equal(await rawRequest(gateway.origin, api), 418);
    assert.equal(await rawRequest(gateway.origin, page), 418);
    assert.deepEqual(backend.requests, [api]);
    assert.deepEqual(frontend.requests, [page]);
  } finally { await gateway.close(); await Promise.all([backend.close(), frontend.close()]); }
});
test('real gateway applies the same path validation to WebSocket upgrades', async () => {
  const backend = await recordingListener();
  const frontend = await recordingListener();
  const gateway = await createGateway({ token, timeoutMs: 1000 });
  gateway.setTargets({ backend: backend.port, frontend: frontend.port });
  try {
    for (const target of ['/%77s/runs/one', '/ws%2fruns/one', '/ws/runs/%zz', '/ws/%252fapi']) {
      assert.match(await rawUpgrade(gateway.origin, target), /^HTTP\/1\.1 400 Bad Request/);
    }
    assert.deepEqual(backend.upgrades, []);
    assert.match(await rawUpgrade(gateway.origin, '/ws/runs/run%20one?display=%E4%B8%AD'), /^HTTP\/1\.1 426 Upgrade Required/);
    assert.deepEqual(backend.upgrades, ['/ws/runs/run%20one?display=%E4%B8%AD']);
    assert.deepEqual(frontend.requests, []);
    assert.deepEqual(frontend.upgrades, []);
  } finally { await gateway.close(); await Promise.all([backend.close(), frontend.close()]); }
});
