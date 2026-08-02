import { execFileSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repository = resolve(desktop, '..');
const temporary = mkdtempSync(join(tmpdir(), 'vidxp-notices-'));
const rustNotices = join(temporary, 'rust.txt');
const destination = join(desktop, 'THIRD_PARTY_NOTICES.txt');
const cargoLock = join(desktop, 'src-tauri', 'Cargo.lock');
const preferredArtifact = /^(licen[cs]e|copying|notice)(?:[._-].*)?$/i;
const documentationArtifact = /^(readme|changelog)(?:[._-].*)?$/i;

const mitBody = `Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.`;

function packageRoot(metadata) {
  if (!metadata.licenseFile) return null;
  return dirname(metadata.licenseFile);
}

function publishedLicenseArtifacts(metadata) {
  const root = packageRoot(metadata);
  if (!root) return [];
  const preferred = readdirSync(root, { withFileTypes: true })
    .filter((entry) => entry.isFile() && preferredArtifact.test(entry.name))
    .map((entry) => join(root, entry.name))
    .sort((left, right) => left.localeCompare(right));
  if (preferred.length > 0) return preferred;
  const selected = metadata.licenseFile;
  return selected && !documentationArtifact.test(selected.split(/[\\/]/).at(-1) ?? '')
    ? [selected]
    : [];
}

function frontendLicenseText(identity, metadata) {
  const artifacts = publishedLicenseArtifacts(metadata);
  if (artifacts.length > 0) {
    return artifacts.map((path) => readFileSync(path, 'utf8').trim()).join('\n\n');
  }
  if (metadata.licenses === 'MIT') {
    const root = packageRoot(metadata);
    const packageMetadata = root
      ? JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
      : {};
    const author = typeof packageMetadata.author === 'string'
      ? packageMetadata.author
      : packageMetadata.author?.name;
    if (!author) {
      throw new Error(`${identity} declares MIT but publishes no preferred license artifact or author attribution.`);
    }
    return `MIT License\n\nCopyright holder: ${author}\n\n${mitBody}`;
  }
  throw new Error(`${identity} has no resolvable published license artifact for ${metadata.licenses}.`);
}

try {
  const lockBefore = readFileSync(cargoLock);
  execFileSync(
    'cargo',
    [
      'about', 'generate', '--locked', '--frozen',
      '--manifest-path', 'src-tauri/Cargo.toml',
      '-c', 'about.toml', '-o', rustNotices, 'about.hbs',
    ],
    { cwd: desktop, stdio: 'inherit' },
  );
  const lockAfter = readFileSync(cargoLock);
  if (!lockBefore.equals(lockAfter)) {
    throw new Error('cargo-about modified desktop/src-tauri/Cargo.lock.');
  }

  const checker = join(
    desktop,
    'node_modules',
    'license-checker-rseidelsohn',
    'bin',
    'license-checker-rseidelsohn.js',
  );
  const npmInventory = JSON.parse(execFileSync(
    process.execPath,
    [checker, '--production', '--json'],
    { cwd: desktop, encoding: 'utf8' },
  ));
  const frontend = ['FRONTEND DEPENDENCIES', '=====================', ''];
  for (const [identity, metadata] of Object.entries(npmInventory).sort(([left], [right]) => left.localeCompare(right))) {
    if (identity.startsWith('vidxp-desktop@')) continue;
    const licenses = String(metadata.licenses ?? '');
    const source = String(metadata.repository ?? '');
    if (!licenses || /unknown|unlicensed/i.test(licenses)) {
      throw new Error(`${identity} has an unresolved shipped license.`);
    }
    if (!source) throw new Error(`${identity} has no published source repository.`);
    frontend.push('-------------------------------------------------------------------------------');
    frontend.push(`${identity} | ${licenses} | ${source}`);
    frontend.push('');
    frontend.push(frontendLicenseText(identity, metadata));
    frontend.push('');
  }

  const sidecars = JSON.parse(readFileSync(join(desktop, 'sidecars.json'), 'utf8'));
  const projectLicense = readFileSync(join(repository, 'LICENSE'), 'utf8').trim();
  const uvMitLicense = readFileSync(join(desktop, 'licenses', 'uv-LICENSE-MIT.txt'), 'utf8').trim();
  const artifact = [
    'VidXP Desktop Legal Notices',
    '===========================',
    '',
    'This artifact is generated from locked production dependency graphs and bundled sidecar metadata.',
    '',
    'VIDXP PROJECT LICENSE',
    '=====================',
    'VidXP Desktop and VidXP | MIT | https://github.com/grayhatdevelopers/vidxp',
    '',
    projectLicense,
    '',
    'BUNDLED EXECUTABLES',
    '===================',
    `uv ${sidecars.uv_version} | MIT OR Apache-2.0 | https://github.com/astral-sh/uv/tree/${sidecars.uv_version}`,
    'The complete MIT terms from the pinned uv release follow. The complete Apache-2.0 terms are included in the Rust dependency section below.',
    '',
    uvMitLicense,
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
