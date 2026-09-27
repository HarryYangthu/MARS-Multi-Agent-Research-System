'use strict';
// Build verification only. This runs the real packaged services under macOS
// read denial; no provider or tool output is substituted.
const fs = require('node:fs/promises');
const path = require('node:path');
const { randomBytes } = require('node:crypto');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const run = promisify(execFile);
const [resources, output] = process.argv.slice(2);
const shell = path.join(resources, 'app/desktop/src');
const { seedRuntime } = require(path.join(shell, 'runtime-assets.cjs'));
const { createGateway } = require(path.join(shell, 'gateway.cjs'));
const { OwnedProcess, waitForPort, waitForBackend, getStatus } = require(path.join(shell, 'lifecycle.cjs'));
const services = [];
let gateway;
let stage = 'seed';
const watchdog = setTimeout(() => { close().finally(() => process.exit(1)); }, 120000);

async function main() {
  await fs.mkdir(output, { recursive: true, mode: 0o700 });
  const source = path.join(resources, 'mars');
  const pythonRoot = path.join(resources, 'python');
  const python = path.join(pythonRoot, 'bin/python3.11');
  const runtimeRoot = await seedRuntime(source, output);
  const token = randomBytes(32).toString('hex');
  gateway = await createGateway({ token, timeoutMs: 10000 });
  const env = { ...process.env, PYTHONHOME: pythonRoot, PYTHONPATH: [path.join(source, 'backend'), path.join(source, 'projects/synthetic_regression/src')].join(path.delimiter),
    PYTHONNOUSERSITE: '1', PYTHONDONTWRITEBYTECODE: '1', PYTHONUNBUFFERED: '1',
    MARS_RUNTIME_ROOT: runtimeRoot, MARS_DESKTOP_SESSION_TOKEN: token, MARS_CORS_ORIGINS: gateway.origin };
  stage = 'python-origins';
  const origins = await run(python, ['-c', `import importlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]).resolve()
modules=[importlib.import_module(name) for name in ('uvicorn','fastapi','asyncssh','chromadb','numpy','cryptography','app.main')]
paths=[pathlib.Path(p).resolve() for p in [sys.executable,sys.prefix,sys.base_prefix,*sys.path] if p]
paths += [pathlib.Path(m.__file__).resolve() for m in modules if m.__file__]
assert all(p.is_relative_to(root) for p in paths), 'External Python runtime origin'
print(json.dumps({'version':sys.version.split()[0],'all_origins_in_bundle':True,'module_count':len(modules)}))`, resources],
    { cwd: pythonRoot, env, timeout: 60000, maxBuffer: 1024 * 1024 });
  const pythonOrigins = JSON.parse(origins.stdout.trim().split('\n').at(-1));
  const frontendRoot = path.join(source, 'frontend');
  stage = 'node-origins';
  const nextPath = require.resolve('next', { paths: [frontendRoot] });
  if (!nextPath.startsWith(resources + path.sep) || !process.execPath.startsWith(path.dirname(resources) + path.sep)) throw new Error('External Node runtime origin');
  const backend = new OwnedProcess(python, [path.join(shell, 'backend-runner.py')], { cwd: runtimeRoot, env });
  stage = 'backend';
  services.push(backend);
  const backendPort = await waitForPort(backend, 60000);
  const health = await waitForBackend(backend, backendPort, token, gateway.origin, 60000);
  const frontend = new OwnedProcess(process.execPath, [path.join(shell, 'frontend-runner.cjs')], { cwd: source,
    env: { ...process.env, ELECTRON_RUN_AS_NODE: '1', NODE_ENV: 'production', NEXT_TELEMETRY_DISABLED: '1', MARS_DESKTOP_FRONTEND_ROOT: frontendRoot, MARS_DESKTOP_SESSION_TOKEN: token } });
  services.push(frontend);
  let frontendError = '';
  frontend.child.stderr.on('data', chunk => { frontendError = (frontendError + chunk.toString()).slice(-8192).replaceAll(token, '[redacted]'); });
  stage = 'frontend';
  const frontendPort = await waitForPort(frontend, 60000);
  gateway.setTargets({ backend: backendPort, frontend: frontendPort });
  stage = 'real-http';
  const headers = { 'x-mars-desktop-token': token, Origin: gateway.origin };
  const api = await getStatus(gateway.origin + '/api/projects', headers);
  const html = await getStatus(gateway.origin + '/projects', headers);
  if (api !== 200 || html !== 200) {
    await fs.writeFile(path.join(output, 'frontend-error.log'), frontendError, { mode: 0o600 });
    process.stderr.write(JSON.stringify({ api_status: api, frontend_status: html }) + '\n');
    throw new Error('Real packaged service requests failed');
  }
  await close();
  await fs.writeFile(path.join(output, 'runtime-verification.json'), JSON.stringify({
    schema: 'mars.runtime-isolation.v1', real_backend_health: health.status === 'ok', real_api_status: api,
    real_frontend_status: html, frontend_path: '/projects', python: pythonOrigins, node: process.versions.node,
    node_and_next_in_bundle: true, owned_services_exited: services.every(service => service.closed),
    research_execution_verified: false,
  }, null, 2) + '\n');
  clearTimeout(watchdog);
}
async function close() {
  if (gateway) { await gateway.close(); gateway = undefined; }
  await Promise.all(services.map(service => service.stop(5000)));
}
process.on('SIGTERM', () => { close().finally(() => process.exit(1)); });
main().catch(async (error) => { await close(); clearTimeout(watchdog); process.stderr.write(JSON.stringify({ failed: true, stage, kind: error.name }) + '\n'); process.exitCode = 1; });
