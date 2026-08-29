import { beforeEach, describe, expect, it, vi } from 'vitest';

const { invoke, listen } = vi.hoisted(() => ({ invoke: vi.fn(), listen: vi.fn() }));

vi.mock('@tauri-apps/api/core', () => ({ invoke }));
vi.mock('@tauri-apps/api/event', () => ({ listen }));

import {
  beginManagedSetup,
  cancelManagedSetupOperation,
  displayPath,
  installRuntime,
  installCodexPlugin,
  configureExternalInstallation,
  browserServiceStatus,
  localServerStatus,
  localWorkerStatus,
  onManagedSetupProgress,
  mcpClientConfig,
  recheckTargetState,
  startLocalServer,
  startSharedBrowser,
  startSharedServer,
  startLocalWorker,
  stopLocalServer,
  stopBrowserService,
  stopLocalWorker,
  targetDoctor,
  targetSetupState,
} from './tauri';

beforeEach(() => {
  invoke.mockReset();
  listen.mockReset();
});

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

  it('cancels only the active managed setup draft', async () => {
    invoke.mockResolvedValue(undefined);

    await cancelManagedSetupOperation('draft-1');

    expect(invoke).toHaveBeenCalledWith('cancel_managed_setup_operation', { draftId: 'draft-1' });
  });

  it('maps managed setup progress events to their payload', async () => {
    const stop = vi.fn();
    listen.mockResolvedValue(stop);
    const handler = vi.fn();

    await expect(onManagedSetupProgress(handler)).resolves.toBe(stop);
    const listener = listen.mock.calls[0][1];
    const payload = { draft_id: 'draft-1', current: 3, total: 8, stage: 'package', message: 'Acquiring VidXP' };
    listener({ payload });

    expect(listen).toHaveBeenCalledWith('managed-setup-progress', expect.any(Function));
    expect(handler).toHaveBeenCalledWith(payload);
  });

  it('maps runtime health, MCP configuration, and service lifecycle commands', async () => {
    invoke.mockResolvedValue({});

    await targetDoctor();
    await mcpClientConfig();
    await installCodexPlugin();
    await localWorkerStatus();
    await startLocalWorker();
    await stopLocalWorker();
    await browserServiceStatus();
    await startSharedBrowser();
    await stopBrowserService();
    await localServerStatus();
    await startLocalServer();
    await startSharedServer();
    await stopLocalServer();

    expect(invoke).toHaveBeenNthCalledWith(1, 'target_doctor');
    expect(invoke).toHaveBeenNthCalledWith(2, 'mcp_client_config');
    expect(invoke).toHaveBeenNthCalledWith(3, 'install_codex_plugin');
    expect(invoke).toHaveBeenNthCalledWith(4, 'local_worker_status');
    expect(invoke).toHaveBeenNthCalledWith(5, 'start_local_worker');
    expect(invoke).toHaveBeenNthCalledWith(6, 'stop_local_worker');
    expect(invoke).toHaveBeenNthCalledWith(7, 'browser_service_status');
    expect(invoke).toHaveBeenNthCalledWith(8, 'start_shared_browser');
    expect(invoke).toHaveBeenNthCalledWith(9, 'stop_browser_service');
    expect(invoke).toHaveBeenNthCalledWith(10, 'local_server_status');
    expect(invoke).toHaveBeenNthCalledWith(11, 'start_local_server');
    expect(invoke).toHaveBeenNthCalledWith(12, 'start_shared_server');
    expect(invoke).toHaveBeenNthCalledWith(13, 'stop_local_server');
  });

  it('adds optional surfaces to the selected existing installation', async () => {
    invoke.mockResolvedValue({ profiles: [], selected_profile_id: null, issues: [] });

    await configureExternalInstallation(['scene'], ['mcp', 'server']);

    expect(invoke).toHaveBeenCalledWith('configure_external_installation', { capabilities: ['scene'], surfaces: ['mcp', 'server'] });
  });
});
