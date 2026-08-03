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
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repository = resolve(desktop, '..');
const temporary = mkdtempSync(join(tmpdir(), 'vidxp-notices-'));
const rustNotices = join(temporary, 'rust.txt');
const destination = join(desktop, 'THIRD_PARTY_NOTICES.txt');
const cargoLock = join(desktop, 'src-tauri', 'Cargo.lock');
const preferredArtifact = /^(licen[cs]e|copying|notice)(?:[._-].*)?$/i;
const documentationArtifact = /^(readme|changelog)(?:[._-].*)?$/i;

const vendoredArtifacts = new Map([
  ['react-remove-scroll-bar@2.3.8', {
    path: join(desktop, 'licenses', 'npm', 'react-remove-scroll-bar-2.3.8-LICENSE.txt'),
    source: 'https://github.com/theKashey/react-remove-scroll-bar/blob/8ca9ba5ea52de03308fe8ced94f7b159a44d28ff/LICENSE',
    sha256: 'a79aae0c0f21990d9d963bb3c5a79cdcea9a46f8523ba55c58d7fe776b6ebc84',
  }],
]);

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
  const vendored = vendoredArtifacts.get(identity);
  if (vendored) {
    const contents = readFileSync(vendored.path, 'utf8').replace(/\r\n?/g, '\n').trim();
    const actual = createHash('sha256').update(`${contents}\n`, 'utf8').digest('hex');
    if (actual !== vendored.sha256) {
      throw new Error(`${identity} vendored license digest ${actual} does not match ${vendored.sha256}.`);
    }
    return `Vendored verbatim upstream license for ${identity}\nSource: ${vendored.source}\nSHA-256: ${vendored.sha256}\n\n${contents}`;
  }
  throw new Error(`${identity} has no published license artifact and no exact, provenance-pinned vendored exception for ${metadata.licenses}.`);
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
    readFileSync(rustNotices, 'utf8')
      .replace(/^\- vidxp-desktop [^\n]+\n/m, '')
      .trim(),
    '',
    frontend.join('\n').trim(),
    '',
  ].join('\n').replace(/\r\n?/g, '\n').replace(/[ \t]+$/gm, '');
  if (process.argv.includes('--write')) {
    writeFileSync(destination, artifact, 'utf8');
  } else if (readFileSync(destination, 'utf8').replace(/\r\n?/g, '\n') !== artifact) {
    const generated = join(temporary, 'THIRD_PARTY_NOTICES.txt');
    writeFileSync(generated, artifact, 'utf8');
    try {
      execFileSync(
        'git',
        ['diff', '--no-index', '--', destination, generated],
        { cwd: desktop, stdio: 'inherit' },
      );
    } catch (error) {
      if (error?.status !== 1) throw error;
    }
    throw new Error('THIRD_PARTY_NOTICES.txt is stale; run npm run notices:write.');
  }
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
