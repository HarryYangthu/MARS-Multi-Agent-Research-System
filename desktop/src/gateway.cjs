'use strict';
const http = require('node:http');
const net = require('node:net');
const { TOKEN_HEADER, authorizedRequest, backendPath, gatewayTarget } = require('./security.cjs');

async function createGateway({ token, timeoutMs }) {
  let origin;
  let targets;
  const sockets = new Set();
  const server = http.createServer((request, response) => {
    if (!authorizedRequest(request, token, origin)) {
      response.writeHead(403, { 'Content-Type': 'text/plain', 'Cache-Control': 'no-store' });
      response.end('Desktop session required.');
      return;
    }
    let parsed;
    try { parsed = gatewayTarget(request.url, origin); }
    catch { response.writeHead(400); response.end('Invalid local request target.'); return; }
    if (!targets) { response.writeHead(503); response.end('Services are starting.'); return; }
    const port = backendPath(parsed.pathname) ? targets.backend : targets.frontend;
    const headers = { ...request.headers, host: `127.0.0.1:${port}`, [TOKEN_HEADER]: token };
    delete headers['proxy-authorization'];
    const upstream = http.request({ hostname: '127.0.0.1', port, path: parsed.pathname + parsed.search, method: request.method, headers, timeout: timeoutMs }, (incoming) => {
      response.writeHead(incoming.statusCode, incoming.headers);
      incoming.pipe(response);
    });
    upstream.on('timeout', () => upstream.destroy());
    upstream.on('error', () => {
      if (!response.headersSent) response.writeHead(502, { 'Content-Type': 'text/plain' });
      response.end('The local service is unavailable.');
    });
    response.on('close', () => upstream.destroy());
    request.pipe(upstream);
  });
  server.on('connection', (socket) => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
  server.on('upgrade', (request, socket, head) => {
    if (!authorizedRequest(request, token, origin)) {
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n'); return;
    }
    let parsed;
    try { parsed = gatewayTarget(request.url, origin); }
    catch { socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n'); return; }
    if (!targets || !parsed.pathname.startsWith('/ws/')) {
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n'); return;
    }
    const upstream = net.connect(targets.backend, '127.0.0.1', () => {
      const headers = { ...request.headers, host: `127.0.0.1:${targets.backend}`, [TOKEN_HEADER]: token };
      delete headers['proxy-authorization'];
      upstream.write(`${request.method} ${parsed.pathname + parsed.search} HTTP/1.1\r\n${Object.entries(headers).map(([key, value]) => `${key}: ${value}`).join('\r\n')}\r\n\r\n`);
      if (head.length) upstream.write(head);
      socket.pipe(upstream); upstream.pipe(socket);
    });
    upstream.on('error', () => socket.destroy());
    socket.on('error', () => upstream.destroy());
    socket.on('close', () => upstream.destroy());
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  origin = `http://127.0.0.1:${server.address().port}`;
  return {
    origin,
    setTargets(value) { targets = value; },
    close() { for (const socket of sockets) socket.destroy(); return new Promise((resolve) => server.close(resolve)); },
  };
}
module.exports = { createGateway };
