// Example adapter: run this from any Node application, including FlowDesk main process.
// node examples/flowdesk-integration.mjs <python-executable> <media-path> <library-path>
import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { createInterface } from 'node:readline';

export async function annotateMedia(python, media, library, onProgress = () => {}) {
  const child = spawn(python, ['-m', 'framepulse', 'analyze', media, '--output', library], {
    windowsHide: true, shell: false, env: process.env,
  });
  let stdout = '';
  child.stdout.setEncoding('utf8');
  child.stdout.on('data', chunk => { stdout += chunk; });
  createInterface({ input: child.stderr }).on('line', line => {
    try { onProgress(JSON.parse(line)); } catch { /* Non-JSON dependency diagnostics are not progress. */ }
  });
  const code = await new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', resolve);
  });
  if (![0, 3].includes(code)) throw new Error(`FramePulse exited with ${code}`);
  const result = JSON.parse(stdout.trim());
  const record = JSON.parse(await readFile(result.record, 'utf8'));
  if (record.format !== 'framepulse.media' || record.spec_version !== '0.1.0') throw new Error('Unsupported record version');
  return { result, record, partial: code === 3 };
}

if (process.argv.length >= 5) {
  const { result, record } = await annotateMedia(...process.argv.slice(2, 5), progress => console.error(progress.message || progress.event));
  console.log(JSON.stringify({ ...result, firstShotStartSeconds: (record.shots[0].range?.start_us ?? 0) / 1e6 }));
}
