import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const temporary = mkdtempSync(join(tmpdir(), 'vidxp-notices-'));
const rustNotices = join(temporary, 'rust.txt');
const destination = join(desktop, 'THIRD_PARTY_NOTICES.txt');

try {
  execFileSync(
    'cargo',
    ['about', 'generate', '--manifest-path', 'src-tauri/Cargo.toml', '-c', 'about.toml', '-o', rustNotices, 'about.hbs'],
    { cwd: desktop, stdio: 'inherit' },
  );
  const checker = join(desktop, 'node_modules', 'license-checker-rseidelsohn', 'bin', 'license-checker-rseidelsohn.js');
  const npmInventory = JSON.parse(execFileSync(process.execPath, [checker, '--production', '--json'], { cwd: desktop, encoding: 'utf8' }));
  const frontend = ['FRONTEND DEPENDENCIES', '=====================', ''];
  for (const [identity, metadata] of Object.entries(npmInventory).sort(([left], [right]) => left.localeCompare(right))) {
    if (identity.startsWith('vidxp-desktop@')) continue;
    frontend.push('-------------------------------------------------------------------------------');
    frontend.push(`${identity} | ${metadata.licenses || 'UNKNOWN'} | ${metadata.repository || 'source unavailable'}`);
    frontend.push('');
    if (metadata.licenseFile) {
      frontend.push(readFileSync(metadata.licenseFile, 'utf8').trim());
    } else {
      frontend.push('No separate license file was published with this package.');
    }
    frontend.push('');
  }
  const artifact = [
    'VidXP Desktop Third-Party Notices',
    '===================================',
    '',
    'This artifact is generated from the locked production dependency graphs.',
    '',
    readFileSync(rustNotices, 'utf8').trim(),
    '',
    frontend.join('\n').trim(),
    '',
  ].join('\n').replace(/[ \t]+$/gm, '');
  if (process.argv.includes('--write')) {
    writeFileSync(destination, artifact, 'utf8');
  } else if (readFileSync(destination, 'utf8') !== artifact) {
    throw new Error('THIRD_PARTY_NOTICES.txt is stale; run npm run notices:write.');
  }
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
