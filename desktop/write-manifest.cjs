'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const config = require('./package.json');
const output = path.resolve(__dirname, '../dist');

async function sha256(file) {
  const digest = crypto.createHash('sha256');
  for await (const chunk of fs.createReadStream(file)) digest.update(chunk);
  return digest.digest('hex');
}

(async () => {
  const artifacts = [];
  for (const file of [`FramePulse-Setup-${config.version}-x64.exe`, `FramePulse-${config.version}-win-x64.zip`]) {
    const fullPath = path.join(output, file);
    artifacts.push({ file, bytes: fs.statSync(fullPath).size, sha256: await sha256(fullPath) });
  }
  const manifest = { version: config.version, platform: 'win32-x64', built_at: new Date().toISOString(),
    electron: config.devDependencies.electron, code_signing: 'unsigned', artifacts };
  const destination = path.join(output, `FramePulse-${config.version}-desktop.manifest.json`);
  fs.writeFileSync(destination, JSON.stringify(manifest, null, 2) + '\n');
  console.log(JSON.stringify(manifest));
})().catch(error => { console.error(error.message); process.exitCode = 1; });
