'use strict';
const http = require('node:http');
const { consumeFrontendToken } = require('./security.cjs');

async function main() {
  const directory = process.env.MARS_DESKTOP_FRONTEND_ROOT;
  const token = process.env.MARS_DESKTOP_SESSION_TOKEN;
  delete process.env.MARS_DESKTOP_SESSION_TOKEN;
  const next = require(require.resolve('next', { paths: [directory] }));
  const app = next({ dev: false, dir: directory, hostname: '127.0.0.1' });
  await app.prepare();
  const handler = app.getRequestHandler();
  const server = http.createServer((request, response) => {
    if (!consumeFrontendToken(request, token)) {
      response.writeHead(403); response.end('Desktop session required.'); return;
    }
    handler(request, response);
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  process.stdout.write(`MARS_DESKTOP_READY ${JSON.stringify({ port: server.address().port, pid: process.pid })}\n`);
  let stopping = false;
  const stop = async () => {
    if (stopping) return;
    stopping = true;
    server.closeAllConnections();
    server.close();
    await app.close();
    process.exit(0);
  };
  process.on('SIGTERM', stop);
  process.on('SIGINT', stop);
}
main().catch(() => { process.stderr.write('Production frontend failed to start.\n'); process.exit(1); });
