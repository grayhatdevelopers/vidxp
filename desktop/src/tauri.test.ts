import { beforeEach, describe, expect, it, vi } from 'vitest';

const { invoke } = vi.hoisted(() => ({ invoke: vi.fn() }));

vi.mock('@tauri-apps/api/core', () => ({ invoke }));

import {
  beginManagedSetup,
  displayPath,
  installRuntime,
  recheckTargetState,
  targetSetupState,
} from './tauri';

beforeEach(() => invoke.mockReset());

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

describe('desktop IPC adapter', () => {
  const emptyState = { profiles: [], selected_profile_id: null, issues: [] };

  it('uses distinct state read and revalidation commands', async () => {
    invoke.mockResolvedValue(emptyState);

    await expect(targetSetupState()).resolves.toEqual(emptyState);
    await expect(recheckTargetState()).resolves.toEqual(emptyState);

    expect(invoke).toHaveBeenNthCalledWith(1, 'target_state');
    expect(invoke).toHaveBeenNthCalledWith(2, 'refresh_target_state');
  });

  it('preserves the scoped managed draft and install request contracts', async () => {
    const draft = { id: 'draft-1', previous_profile_id: 'profile-1' };
    const request = {
      capabilities: ['actor'],
      surfaces: ['browser'],
      prepare_models: false,
      draft_id: draft.id,
    };
    invoke.mockResolvedValueOnce(draft).mockResolvedValueOnce({
      install: { prepared: false },
      setup: emptyState,
    });

    await expect(beginManagedSetup()).resolves.toEqual(draft);
    await installRuntime(request);

    expect(invoke).toHaveBeenNthCalledWith(1, 'begin_managed_setup');
    expect(invoke).toHaveBeenNthCalledWith(2, 'install_runtime', { request });
  });
});
