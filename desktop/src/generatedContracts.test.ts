import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repository = resolve(desktop, '..');

function read(path: string) {
  return readFileSync(resolve(repository, path), 'utf8');
}

describe('generated Desktop contracts', () => {
  it('packages the project license and complete third-party notices', () => {
    const config = JSON.parse(read('desktop/src-tauri/tauri.conf.json'));
    expect(config.bundle.resources).toEqual(expect.arrayContaining([
      '../THIRD_PARTY_NOTICES.txt',
      '../../LICENSE',
    ]));

    const notices = read('desktop/THIRD_PARTY_NOTICES.txt');
    expect(notices).toContain('VIDXP PROJECT LICENSE');
    expect(notices).toContain('uv 0.12.0 | MIT OR Apache-2.0');
    expect(notices).toContain('Astral Software Inc.');
  });

  it('exposes deterministic write and check commands for the model catalog', () => {
    const packageJson = JSON.parse(read('desktop/package.json'));
    expect(packageJson.scripts['model-catalog:write']).toBe(
      'uv run --frozen python scripts/model-catalog.py --write',
    );
    expect(packageJson.scripts['model-catalog:check']).toBe(
      'uv run --frozen python scripts/model-catalog.py --check',
    );
  });

  it('keeps public contributor commands and target ownership guidance accurate', () => {
    const contributing = read('docs/CONTRIBUTING.md');
    expect(contributing).toContain(
      'cargo install cargo-about --version 0.9.1 --locked --features cli',
    );
    expect(contributing).toContain('npm --prefix desktop run notices:write');
    expect(contributing).toContain('npm --prefix desktop run model-catalog:write');

    const installation = read('INSTALLATION_GUIDE.md');
    expect(installation).toContain('Use an existing installation');
    expect(installation).toContain('the installation stays externally owned');
    expect(installation).toContain('Prepare / verify models');
    expect(installation).toContain('Open VidXP');
  });
});
