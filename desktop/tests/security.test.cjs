'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { TOKEN_HEADER, tokenMatches, isAppURL, authorizedRequest, backendPath, gatewayTarget, consumeFrontendToken, minimalEnvironment } = require('../src/security.cjs');
const { createGateway } = require('../src/gateway.cjs');
const token = 'a'.repeat(64);
test('desktop request authentication rejects external origins, wrong hosts and missing tokens', () => {
  const origin = 'http://127.0.0.1:43210';
  const request = { headers: { host: '127.0.0.1:43210', [TOKEN_HEADER]: token, origin } };
  assert.equal(authorizedRequest(request, token, origin), true);
  for (const header of [{ origin: 'https://external.example' }, { host: 'localhost:43210' }, { [TOKEN_HEADER]: '' }, { 'sec-fetch-site': 'cross-site' }]) {
    assert.equal(authorizedRequest({ headers: { ...request.headers, ...header } }, token, origin), false);
  }
  assert.equal(tokenMatches('a', 'a'), false);
  assert.equal(isAppURL('http://127.0.0.1:43210/api/runs', origin), true);
  assert.equal(isAppURL('ws://127.0.0.1:43210/ws/runs/one', origin), true);
  for (const url of ['http://127.0.0.1:43211/', 'http://127.0.0.1.evil.test:43210/', 'file:///etc/passwd', 'http://user@127.0.0.1:43210/']) assert.equal(isAppURL(url, origin), false);
});
test('gateway routing has fixed paths and child environment excludes credentials and runtime injection', () => {
  assert.equal(backendPath('/api/runs'), true);
  assert.equal(backendPath('/ws/runs/one'), true);
  assert.equal(backendPath('/apifake'), false);
  assert.deepEqual(minimalEnvironment({ PATH: '/bin', OPENAI_API_KEY: 'private', NODE_OPTIONS: '--inspect', NODE_TLS_REJECT_UNAUTHORIZED: '0' }), { PATH: '/bin' });
});
test('request target validation rejects path interpretation changes while preserving encoded IDs and query values', () => {
  const origin = 'http://127.0.0.1:43210';
  for (const target of ['/%61pi/projects', '/a%70i/projects', '/%77s/runs/one', '/%68ealth', '/API/projects', '/Health',
    '/api%2fruns', '/runs/%2fapi/projects', '/runs/%5capi', '/runs/%252fapi', '/%2561pi/projects',
    '/runs/../api/projects', '/runs/%2e%2e/api/projects', '/api//runs', '//api/projects',
    '/api/runs#fragment', '/api/runs/%00', '/api/%zz', '/api/runs?name=%zz', '/api/%E4',
    'http://127.0.0.1:43210/api/projects', '/api\\runs']) {
    assert.throws(() => gatewayTarget(target, origin), Error, target);
  }
  for (const target of ['/api/runs/run%20one', '/api/projects/%E4%B8%AD%E6%96%87', '/runs/run%2Bone',
    '/api/runs?next=%2Fapi%2Fruns&path=C%3A%5Cdata', '/api/runs?id=a%252Fb']) {
    const parsed = gatewayTarget(target, origin);
    assert.equal(parsed.pathname + parsed.search, target);
  }
});
test('frontend authorization consumes the credential before downstream request handling', async () => {
  const http = require('node:http');
  const server = http.createServer((request, response) => {
    if (!consumeFrontendToken(request, token)) { response.writeHead(403); response.end(); return; }
    response.end(JSON.stringify({
      normalizedToken: Object.hasOwn(request.headers, TOKEN_HEADER),
      rawToken: request.rawHeaders.some((value, index) => index % 2 === 0 && value.toLowerCase() === TOKEN_HEADER),
      distinctToken: Object.hasOwn(request.headersDistinct, TOKEN_HEADER),
      acceptedHeader: request.headers['x-boundary-test'],
    }));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  try {
    const response = await fetch(`http://127.0.0.1:${server.address().port}/`, {
      headers: { 'X-Mars-Desktop-Token': token, 'x-boundary-test': 'preserved' },
    });
    assert.deepEqual(await response.json(), { normalizedToken: false, rawToken: false, distinctToken: false, acceptedHeader: 'preserved' });
    assert.equal((await fetch(`http://127.0.0.1:${server.address().port}/`)).status, 403);
  } finally { server.closeAllConnections(); await new Promise((resolve) => server.close(resolve)); }
});
test('real gateway denies unauthenticated HTTP and reports unavailable unstarted dependencies', async () => {
  const gateway = await createGateway({ token, timeoutMs: 1000 });
  try {
    const denied = await fetch(`${gateway.origin}/health`);
    assert.equal(denied.status, 403);
    const starting = await fetch(`${gateway.origin}/health`, { headers: { [TOKEN_HEADER]: token } });
    assert.equal(starting.status, 503);
    const crossSite = await fetch(`${gateway.origin}/api/runs`, { method: 'POST', headers: { [TOKEN_HEADER]: token, Origin: 'https://external.example' } });
    assert.equal(crossSite.status, 403);
  } finally { await gateway.close(); }
});
test('real gateway rejects an unauthenticated WebSocket upgrade', async () => {
  const net = require('node:net');
  const gateway = await createGateway({ token, timeoutMs: 1000 });
  try {
    const url = new URL(gateway.origin);
    const reply = await new Promise((resolve, reject) => {
      const socket = net.connect(Number(url.port), url.hostname, () => socket.write(`GET /ws/runs/test HTTP/1.1\r\nHost: ${url.host}\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n`));
      socket.once('data', (data) => { socket.destroy(); resolve(data.toString()); });
      socket.once('error', reject);
    });
    assert.match(reply, /^HTTP\/1.1 403 Forbidden/);
  } finally { await gateway.close(); }
});
