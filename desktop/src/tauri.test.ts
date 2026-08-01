import { describe, expect, it } from 'vitest';

import { displayPath } from './tauri';

describe('displayPath', () => {
  it('prettifies extended Windows drive and UNC paths', () => {
    expect(displayPath(String.raw`\\?\C:\Users\test\vidxp.exe`)).toBe(
      String.raw`C:\Users\test\vidxp.exe`,
    );
    expect(displayPath(String.raw`\\?\UNC\server\share\vidxp.exe`)).toBe(
      String.raw`\\server\share\vidxp.exe`,
    );
  });

  it('leaves normal and non-filesystem device namespaces unchanged', () => {
    const paths = [
      String.raw`C:\Users\test\vidxp.exe`,
      String.raw`\\server\share\vidxp.exe`,
      String.raw`\\.\PhysicalDrive0`,
      String.raw`\\?\GLOBALROOT\Device\HarddiskVolumeShadowCopy1`,
      String.raw`\\?\Volume{01234567-89ab-cdef-0123-456789abcdef}\root`,
      String.raw`\\?\C:relative\vidxp.exe`,
    ];

    for (const path of paths) expect(displayPath(path)).toBe(path);
  });
});
