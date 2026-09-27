'use strict';
const { app, BrowserWindow, dialog, session } = require('electron');
const fs = require('node:fs/promises');
const path = require('node:path');
const { randomBytes, createHash } = require('node:crypto');
const YAML = require('yaml');
const { createGateway } = require('./gateway.cjs');
const { OwnedProcess, waitForPort, waitForBackend, getJSON, getStatus } = require('./lifecycle.cjs');
const { TOKEN_HEADER, isAppURL, minimalEnvironment } = require('./security.cjs');
const { seedRuntime } = require('./runtime-assets.cjs');

const sourceRoot = path.resolve(__dirname, '../..');
const smoke = process.argv.includes('--smoke');
const services = [];
let gateway;
let window;
let quitting = false;
let cleanup;
let config;
let dataRoot;
let exitCode = 0;
const receipt = { mode: 'source-production-spike', platform: process.platform, arch: process.arch, electron: process.versions.electron, startedAt: new Date().toISOString(), services: {}, checks: {} };

app.setName('MARS Desktop Spike');
if (process.env.MARS_DESKTOP_DATA_DIR) {
  if (!path.isAbsolute(process.env.MARS_DESKTOP_DATA_DIR)) throw new Error('MARS_DESKTOP_DATA_DIR must be absolute.');
  app.setPath('userData', process.env.MARS_DESKTOP_DATA_DIR);
}
app.enableSandbox();
const singleInstance = app.requestSingleInstanceLock();
if (!singleInstance) { app.quit(); }
else {
  app.on('second-instance', () => { if (window) { if (window.isMinimized()) window.restore(); window.focus(); } });
  app.on('window-all-closed', () => app.quit());
  process.on('SIGTERM', () => app.quit());
  process.on('SIGINT', () => app.quit());
  app.on('before-quit', (event) => {
    if (quitting) return;
    event.preventDefault();
    cleanup ??= stop().catch(async () => { exitCode = 1; receipt.error = 'Owned-service cleanup failed.'; receipt.exitCode = 1; await writeReceipt().catch(() => {}); }).finally(() => { quitting = true; app.exit(exitCode); });
  });
  app.whenReady().then(start).catch(async () => {
    exitCode = 1;
    receipt.error = 'Desktop startup failed; verify the configured Python runtime, runtime assets, and production frontend build.';
    if (!smoke) dialog.showErrorBox('MARS 无法启动', receipt.error);
    app.quit();
  });
}

async function writeReceipt() {
  if (dataRoot) await fs.writeFile(path.join(dataRoot, 'desktop-lifecycle.json'), `${JSON.stringify(receipt, null, 2)}\n`, { mode: 0o600 });
}
async function stop() {
  receipt.stoppedAt = new Date().toISOString();
  if (gateway) await gateway.close();
  await Promise.all(services.map((service) => service.stop(config?.shutdown_timeout_ms ?? 5000)));
  receipt.checks.ownedServicesExited = services.every((service) => service.closed);
  receipt.exitCode = exitCode;
  await writeReceipt();
}
async function start() {
  if (app.isPackaged) throw new Error('A self-contained desktop distribution has not been configured.');
  dataRoot = app.getPath('userData');
  await fs.mkdir(dataRoot, { recursive: true, mode: 0o700 });
  config = YAML.parse(await fs.readFile(path.join(sourceRoot, 'desktop/config.yaml'), 'utf8'));
  for (const field of ['startup_timeout_ms', 'shutdown_timeout_ms', 'smoke_timeout_ms', 'proxy_timeout_ms']) {
    if (!Number.isInteger(config[field]) || config[field] <= 0) throw new Error('Invalid desktop runtime configuration.');
  }
  receipt.stage = 'runtime-assets';
  receipt.frontendBuildId = (await fs.readFile(path.join(sourceRoot, 'frontend/.next/BUILD_ID'), 'utf8')).trim();
  receipt.shellSha256 = createHash('sha256').update(await fs.readFile(__filename)).digest('hex');
  const token = randomBytes(32).toString('hex');
  const runtimeRoot = await seedRuntime(sourceRoot, dataRoot);
  gateway = await createGateway({ token, timeoutMs: config.proxy_timeout_ms });
  const environment = minimalEnvironment(process.env);
  const python = process.env.MARS_DESKTOP_PYTHON || path.join(sourceRoot, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  await fs.access(python);
  if (cleanup) throw new Error('Desktop is stopping.');
  receipt.stage = 'backend';
  const backend = new OwnedProcess(python, [path.join(__dirname, 'backend-runner.py')], {
    cwd: runtimeRoot,
    env: { ...environment, PYTHONPATH: path.join(sourceRoot, 'backend'), PYTHONDONTWRITEBYTECODE: '1', PYTHONUNBUFFERED: '1', MARS_RUNTIME_ROOT: runtimeRoot, MARS_DESKTOP_SESSION_TOKEN: token, MARS_CORS_ORIGINS: gateway.origin },
  });
  services.push(backend);
  const backendPort = await waitForPort(backend, config.startup_timeout_ms);
  const health = await waitForBackend(backend, backendPort, token, gateway.origin, config.startup_timeout_ms);
  receipt.services.backend = { pid: backend.child.pid, port: backendPort, health };
  if (cleanup) throw new Error('Desktop is stopping.');
  receipt.stage = 'frontend';
  const frontend = new OwnedProcess(process.execPath, [path.join(__dirname, 'frontend-runner.cjs')], {
    cwd: sourceRoot,
    env: { ...environment, ELECTRON_RUN_AS_NODE: '1', NODE_ENV: 'production', NEXT_TELEMETRY_DISABLED: '1', MARS_DESKTOP_FRONTEND_ROOT: path.join(sourceRoot, 'frontend'), MARS_DESKTOP_SESSION_TOKEN: token },
  });
  services.push(frontend);
  const frontendPort = await waitForPort(frontend, config.startup_timeout_ms);
  receipt.services.frontend = { pid: frontend.child.pid, port: frontendPort };
  receipt.services.gateway = { origin: gateway.origin };
  gateway.setTargets({ backend: backendPort, frontend: frontendPort });
  await getJSON(`${gateway.origin}/health`, token, gateway.origin);
  receipt.checks.authenticatedGatewayHealth = true;
  receipt.checks.unauthenticatedGatewayStatus = await getStatus(`${gateway.origin}/health`);
  receipt.checks.unauthenticatedBackendStatus = await getStatus(`http://127.0.0.1:${backendPort}/health`);
  receipt.checks.unauthenticatedFrontendStatus = await getStatus(`http://127.0.0.1:${frontendPort}/`);
  receipt.checks.crossOriginGatewayStatus = await getStatus(`${gateway.origin}/health`, { [TOKEN_HEADER]: token, Origin: 'https://external.example' });
  if (receipt.checks.unauthenticatedGatewayStatus !== 403 || receipt.checks.unauthenticatedBackendStatus !== 401 || receipt.checks.unauthenticatedFrontendStatus !== 403 || receipt.checks.crossOriginGatewayStatus !== 403) {
    throw new Error('A local service accepted a request outside the desktop session.');
  }
  receipt.stage = 'window';
  const rendererSession = session.fromPartition(`mars-${randomBytes(16).toString('hex')}`);
  rendererSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  rendererSession.setPermissionCheckHandler(() => false);
  rendererSession.webRequest.onBeforeRequest((details, callback) => {
    callback({ cancel: !isAppURL(details.url, gateway.origin) && !details.url.startsWith('devtools://') });
  });
  rendererSession.webRequest.onBeforeSendHeaders((details, callback) => {
    const headers = { ...details.requestHeaders };
    for (const key of Object.keys(headers)) if (key.toLowerCase() === TOKEN_HEADER) delete headers[key];
    if (isAppURL(details.url, gateway.origin)) headers[TOKEN_HEADER] = token;
    callback({ requestHeaders: headers });
  });
  rendererSession.webRequest.onHeadersReceived((details, callback) => {
    callback({ responseHeaders: { ...details.responseHeaders, 'Content-Security-Policy': ["default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' ws://127.0.0.1:*; worker-src 'self' blob:; frame-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"] } });
  });
  window = new BrowserWindow({
    width: config.window.width, height: config.window.height, minWidth: config.window.min_width, minHeight: config.window.min_height,
    title: 'MARS', show: false, backgroundColor: '#f8fafc',
    webPreferences: { session: rendererSession, nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, allowRunningInsecureContent: false, webviewTag: false, devTools: !app.isPackaged },
  });
  window.removeMenu();
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, url) => { if (!isAppURL(url, gateway.origin)) event.preventDefault(); });
  window.webContents.on('will-redirect', (event, url) => { if (!isAppURL(url, gateway.origin)) event.preventDefault(); });
  window.webContents.on('will-attach-webview', (event) => event.preventDefault());
  window.webContents.on('render-process-gone', () => { if (!cleanup) { exitCode = 1; app.quit(); } });
  services.forEach((service) => service.on('closed', () => { if (!cleanup) { exitCode = 1; receipt.error = 'An owned service exited unexpectedly.'; app.quit(); } }));
  if (services.some((service) => service.closed)) throw new Error('An owned service exited during startup.');
  await window.loadURL(gateway.origin);
  window.show();
  receipt.checks.windowLoaded = true;
  receipt.stage = 'ready';
  await writeReceipt();
  if (smoke) {
    await new Promise((resolve) => setTimeout(resolve, config.smoke_timeout_ms));
    receipt.checks.renderer = await window.webContents.executeJavaScript(`({title: document.title, nodeAvailable: typeof require !== 'undefined' || typeof process !== 'undefined', bodyTextLength: document.body.innerText.length})`);
    if (receipt.checks.renderer.nodeAvailable || receipt.checks.renderer.bodyTextLength === 0) throw new Error('Renderer isolation or content check failed.');
    receipt.checks.rendererApiStatus = await window.webContents.executeJavaScript(`fetch('/api/projects').then(response => response.status)`);
    receipt.checks.rendererWebSocket = await window.webContents.executeJavaScript(`new Promise(resolve => { const socket = new WebSocket(location.origin.replace(/^http/, 'ws') + '/ws/runs/desktop-connectivity-check'); const timer = setTimeout(() => { socket.close(); resolve(false); }, 2000); socket.onopen = () => { clearTimeout(timer); socket.close(); resolve(true); }; socket.onerror = () => { clearTimeout(timer); resolve(false); }; })`);
    if (receipt.checks.rendererApiStatus !== 200 || !receipt.checks.rendererWebSocket) throw new Error('Desktop renderer could not reach the actual local API and WebSocket.');
    const screenshot = await window.webContents.capturePage();
    await fs.writeFile(path.join(dataRoot, 'desktop-smoke.png'), screenshot.toPNG(), { mode: 0o600 });
    app.quit();
  }
}
