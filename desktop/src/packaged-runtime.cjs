'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');

async function runtimeLayout({ packaged, resourcesPath, sourceRoot, environment = process.env, platform = process.platform, arch = process.arch }) {
  if (!packaged) return {
    mode: 'source-production-spike', sourceRoot,
    python: environment.MARS_DESKTOP_PYTHON || path.join(sourceRoot, '.venv', platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'),
    pythonRoot: null,
  };
  if (platform !== 'darwin' || arch !== 'arm64') throw new Error('This local test bundle requires macOS Apple Silicon.');
  const declaration = JSON.parse(await fs.readFile(path.join(resourcesPath, 'local-test.json'), 'utf8'));
  if (declaration.schema !== 'mars.local-test-bundle.v1' || declaration.arch !== arch || declaration.release !== false) {
    throw new Error('Local test bundle declaration is invalid.');
  }
  const root = await fs.realpath(resourcesPath);
  const source = path.join(root, 'mars');
  const pythonRoot = path.join(root, 'python');
  const python = path.join(pythonRoot, 'bin/python3.11');
  for (const item of [source, python, path.join(source, 'backend/app/main.py'), path.join(source, 'frontend/.next/BUILD_ID'), path.join(source, 'scripts/release/runtime_assets.txt')]) {
    if (!(await fs.realpath(item)).startsWith(root + path.sep)) throw new Error('Bundled runtime escapes the application resources.');
  }
  // Environment overrides are deliberately ignored in packaged mode. There is
  // no fallback to a developer checkout, conda, PATH python, or user site-packages.
  return { mode: 'packaged-local-test', sourceRoot: source, python, pythonRoot };
}

module.exports = { runtimeLayout };
