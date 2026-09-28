'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const { createHash, randomUUID } = require('node:crypto');

async function seedRuntime(sourceRoot, dataRoot) {
  const manifest = await fs.readFile(path.join(sourceRoot, 'scripts/release/runtime_assets.txt'), 'utf8');
  const paths = manifest.split(/\r?\n/).map((line) => line.trim()).filter((line) => line && !line.startsWith('#'));
  const files = [];
  for (const relative of paths) {
    if (path.isAbsolute(relative) || relative.includes('\\') || relative.split('/').some((part) => ['..', '.', '.env', '.env.local'].includes(part))) {
      throw new Error('Runtime asset manifest contains an unsafe path.');
    }
    const source = path.join(sourceRoot, relative);
    if (!(await fs.lstat(source)).isFile() || !(await fs.realpath(source)).startsWith(`${await fs.realpath(sourceRoot)}${path.sep}`)) {
      throw new Error('Runtime asset manifest must reference regular source files.');
    }
    const content = await fs.readFile(source);
    files.push({ relative, content, sha256: createHash('sha256').update(content).digest('hex') });
  }
  const fingerprint = createHash('sha256').update(JSON.stringify(files.map(({ relative, sha256 }) => ({ relative, sha256 })))).digest('hex');
  const workspace = path.join(dataRoot, 'workspace');
  const marker = path.join(workspace, '.desktop-seed.json');
  try {
    const previous = JSON.parse(await fs.readFile(marker, 'utf8'));
    if (previous.fingerprint !== fingerprint) throw new Error('Runtime resources changed. Upgrade migration is not implemented for this development spike; choose a fresh development data directory.');
    return workspace;
  } catch (error) { if (error.code !== 'ENOENT') throw error; }
  try { await fs.access(workspace); throw new Error('Existing unmarked workspace will not be overwritten. Choose an empty development data directory.'); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  const temporary = path.join(dataRoot, `.seed-${randomUUID()}`);
  await fs.mkdir(temporary, { recursive: true, mode: 0o700 });
  try {
    for (const { relative, content } of files) {
      const destination = path.join(temporary, relative);
      await fs.mkdir(path.dirname(destination), { recursive: true, mode: 0o700 });
      await fs.writeFile(destination, content, { mode: 0o600 });
    }
    await fs.writeFile(path.join(temporary, '.desktop-seed.json'), JSON.stringify({ fingerprint, files: files.map(({ relative, sha256 }) => ({ relative, sha256 })) }, null, 2), { mode: 0o600 });
    await fs.rename(temporary, workspace);
  } catch (error) { await fs.rm(temporary, { recursive: true, force: true }); throw error; }
  return workspace;
}
module.exports = { seedRuntime };
