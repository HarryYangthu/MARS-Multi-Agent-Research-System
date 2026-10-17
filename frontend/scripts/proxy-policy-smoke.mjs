// Real HTTP transport test; no LLM, Agent, tool result or metric is simulated.
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { parse } from 'node:url';
import nextConfig from '../next.config.mjs';
import proxyModule from 'next/dist/server/lib/router-utils/proxy-request.js';

assert(Number.isSafeInteger(nextConfig.experimental.proxyTimeout));
assert(nextConfig.experimental.proxyTimeout > 30000);
const upstream = http.createServer((_req, res) => {
  const timer = setTimeout(() => res.end('real delayed HTTP response'), 31000);
  res.on('close', () => clearTimeout(timer));
});
upstream.listen(0, '127.0.0.1');
await once(upstream, 'listening');
const upstreamPort = upstream.address().port;
const proxy = http.createServer((req, res) => {
  const deadline = req.url === '/default' ? undefined : nextConfig.experimental.proxyTimeout;
  proxyModule.proxyRequest(req, res, parse(`http://127.0.0.1:${upstreamPort}/`), undefined, undefined, deadline)
    .catch(() => { if (!res.destroyed) res.destroy(); });
});
proxy.listen(0, '127.0.0.1');
await once(proxy, 'listening');
const proxyPort = proxy.address().port;
try {
  const started = Date.now();
  const outcomes = await Promise.allSettled([
    fetch(`http://127.0.0.1:${proxyPort}/default`).then(async r => ({status: r.status, body: await r.text()})),
    fetch(`http://127.0.0.1:${proxyPort}/configured`).then(async r => ({status: r.status, body: await r.text()})),
  ]);
  assert.equal(outcomes[0].status, 'fulfilled');
  assert.equal(outcomes[0].value.status, 500);
  assert.equal(outcomes[1].status, 'fulfilled');
  assert.equal(outcomes[1].value.status, 200);
  assert.equal(outcomes[1].value.body, 'real delayed HTTP response');
  assert(Date.now() - started >= 31000);
  console.log('Actual Next proxy: default 30s disconnect reproduced; configured request completed beyond 30s.');
} finally {
  proxy.closeAllConnections(); upstream.closeAllConnections();
  proxy.close(); upstream.close();
}
