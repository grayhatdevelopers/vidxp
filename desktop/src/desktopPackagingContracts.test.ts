import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repository = resolve(desktop, '..');

function read(path: string) {
  return readFileSync(resolve(repository, path), 'utf8');
}

describe('Desktop packaging and documentation contracts', () => {
  it('packages the project license and complete third-party notices', () => {
    const config = JSON.parse(read('desktop/src-tauri/tauri.conf.json'));
    expect(config.bundle.resources).toMatchObject({
      '../THIRD_PARTY_NOTICES.txt': 'THIRD_PARTY_NOTICES.txt',
      '../../LICENSE': 'LICENSE',
      '../../premiere/packages/vidxp-premiere-cep.zxp': 'premiere/vidxp-premiere-cep.zxp',
      '../../premiere/packages/vidxp-premiere-uxp.ccx': 'premiere/vidxp-premiere-uxp.ccx',
    });

    const notices = read('desktop/THIRD_PARTY_NOTICES.txt');
    expect(notices).toContain('VIDXP PROJECT LICENSE');
    expect(notices).toContain('uv 0.12.0 | MIT OR Apache-2.0');
    expect(notices).toContain('Astral Software Inc.');
  });

  it('limits borderless custom chrome to the Windows configuration', () => {
    const base = JSON.parse(read('desktop/src-tauri/tauri.conf.json'));
    const windows = JSON.parse(read('desktop/src-tauri/tauri.windows.conf.json'));
    expect(base.app.windows[0]).toMatchObject({ decorations: true, shadow: true });
    expect(windows.app.windows[0]).toMatchObject({ decorations: false, shadow: false });
  });

});
