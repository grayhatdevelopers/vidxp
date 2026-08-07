import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StrictMode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  targetSetupState: vi.fn(), recheckTargetState: vi.fn(), discoverLocalTargets: vi.fn(),
  chooseLocalExecutable: vi.fn(), inspectLocalTarget: vi.fn(), activateLocalTarget: vi.fn(),
  selectTargetProfile: vi.fn(), deleteTargetProfile: vi.fn(), confirmForgetTarget: vi.fn(), beginManagedSetup: vi.fn(),
  cancelManagedSetup: vi.fn(), installMediaRuntime: vi.fn(), installRuntime: vi.fn(),
  prepareManagedModels: vi.fn(), onManagedSetupProgress: vi.fn(),
  runtimeManifest: vi.fn(), runtimeStatus: vi.fn(), launchUi: vi.fn(),
  chooseModelDirectory: vi.fn(), modelDirectoryInventory: vi.fn(),
  targetDoctor: vi.fn(), mcpClientConfig: vi.fn(), installCodexPlugin: vi.fn(), localServerStatus: vi.fn(), localWorkerStatus: vi.fn(), browserServiceStatus: vi.fn(),
  startLocalServer: vi.fn(), startSharedServer: vi.fn(), stopLocalServer: vi.fn(), startSharedBrowser: vi.fn(), stopBrowserService: vi.fn(), startLocalWorker: vi.fn(), stopLocalWorker: vi.fn(), configureExternalInstallation: vi.fn(),
}));

const windowMocks = vi.hoisted(() => ({
  close: vi.fn(), isMaximized: vi.fn(), minimize: vi.fn(), onResized: vi.fn(), toggleMaximize: vi.fn(),
}));

vi.mock('@tauri-apps/api/window', () => ({ getCurrentWindow: () => windowMocks }));
vi.mock('./tauri', () => ({
  ...mocks,
  selectedProfile: (state: any) => state.profiles.find((profile: any) => profile.id === state.selected_profile_id) ?? null,
  errorMessage: (error: unknown, fallback: string) => typeof error === 'string' ? error : fallback,
  displayPath: (path: string) => path,
}));

import { App } from './App';

const frontend = { available: true, launchable: true, optional: true, code: 'frontend_available', message: 'Available.', remediation: '' };
const localProfile = {
  id: 'local-1', display_name: 'Studio VidXP', schema_version: 1, kind: 'existing_local',
  lifecycle_ownership: 'external', executable: 'C:\\Tools\\VidXP\\vidxp.exe',
  display_executable: 'C:\\Tools\\VidXP\\vidxp.exe', data_root: 'C:\\Data', display_data_root: 'C:\\Data',
  repository_root: 'C:\\Data\\repositories\\default', display_repository_root: 'C:\\Data\\repositories\\default',
  observed_vidxp_version: '0.4.0', probe_schema_version: 1, probe_protocol_version: 1,
  launch_protocol_version: 1, runtime: null, frontend, last_successful_validation_at: 1,
  last_validated_at: '2026-08-01T10:00:00Z', validation_error: null, capabilities: [], surfaces: ['worker', 'browser'],
};
const managedProfile = {
  ...localProfile, id: 'managed-a', display_name: 'Managed VidXP', kind: 'managed',
  lifecycle_ownership: 'desktop', managed_runtime_profile: 'runtime-a', capabilities: ['scene'], surfaces: [],
  frontend: { ...frontend, available: false, launchable: false, code: 'frontend_unavailable', message: 'Browser interface is not installed.', remediation: 'Return to managed setup.' },
};
const emptyState = { profiles: [], selected_profile_id: null, issues: [] };
const localState = { profiles: [localProfile], selected_profile_id: localProfile.id, issues: [] };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

function renderApp() {
  return render(<StrictMode><MantineProvider env="test" defaultColorScheme="dark"><App /></MantineProvider></StrictMode>);
}

async function enterLocal(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole('heading', { name: 'How would you like to set up VidXP?' });
  await user.click(screen.getByRole('radio', { name: /Use an existing installation/i }));
  await user.click(screen.getByRole('button', { name: 'Continue' }));
}

async function enterManaged(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole('heading', { name: 'How would you like to set up VidXP?' });
  await user.click(screen.getByRole('radio', { name: /Set up VidXP for me/i }));
  await user.click(screen.getByRole('button', { name: 'Continue' }));
  await user.click(screen.getByRole('button', { name: 'Choose features' }));
  await screen.findByRole('heading', { name: 'Choose your VidXP features' });
}

describe('desktop target lifecycle', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    windowMocks.isMaximized.mockResolvedValue(false);
    windowMocks.onResized.mockResolvedValue(vi.fn());
    mocks.targetSetupState.mockResolvedValue(emptyState);
    mocks.recheckTargetState.mockResolvedValue(emptyState);
    mocks.discoverLocalTargets.mockResolvedValue([{ executable: 'C:\\Tools\\VidXP\\vidxp.exe', display_path: 'C:\\Tools\\VidXP\\vidxp.exe', source: 'PATH' }]);
    mocks.inspectLocalTarget.mockResolvedValue({
      state: 'ready_to_use', adoptable: true, executable: 'C:\\Tools\\VidXP\\vidxp.exe', reported_version: '0.4.0',
      probe_compatible: true, launch_compatible: true, message: 'Compatible contracts.', remediation: '', technical_details: null,
      validation: { canonical_executable: 'C:\\Tools\\VidXP\\vidxp.exe', protocol_version: 1, launch_protocol_version: 1, python_version: '3.14', display_data_root: 'C:\\Data', can_launch_frontend: true, frontend },
    });
    mocks.beginManagedSetup.mockResolvedValue({ id: 'draft-1', previous_profile_id: null });
    mocks.cancelManagedSetup.mockResolvedValue(emptyState);
    mocks.confirmForgetTarget.mockResolvedValue(true);
    mocks.runtimeManifest.mockResolvedValue({ package_version: '0.4.0', capabilities: { scene: { extra: 'scene', label: 'Visual scene search' } }, surfaces: {
      worker: { extra: 'local-worker', label: 'Process videos on this computer', description: 'Run video work locally.', default: true },
      browser: { extra: 'frontend', label: 'Browser interface', description: 'Open VidXP in your browser.', default: true },
      mcp: { extra: 'mcp', label: 'AI assistant integration', description: 'Connect a compatible AI app.', default: false },
      server: { extra: 'server', label: 'App integration service', description: 'Let other local apps connect.', default: false },
    } });
    mocks.runtimeStatus.mockResolvedValue({ state: 'never_configured', ready: false, runtime_profile: null, package_version: '0.4.0', capabilities: [], surfaces: [], model_directory: 'C:\\Models', detail: 'No managed runtime yet.' });
    mocks.modelDirectoryInventory.mockResolvedValue({ directory: 'C:\\Models', exists: false, readable: true, total_bytes: 0, file_count: 0, recognized_models: [], empty: true, verification_required: false, truncated: false, detail: 'Empty.' });
    mocks.installMediaRuntime.mockResolvedValue({ ready: true });
    mocks.onManagedSetupProgress.mockResolvedValue(vi.fn());
    mocks.installRuntime.mockResolvedValue({
      install: { package_version: '0.4.0', capabilities: ['scene'], surfaces: ['worker', 'browser'], model_directory: 'C:\\Models', prepared: true },
      setup: { profiles: [managedProfile], selected_profile_id: managedProfile.id, issues: [] },
    });
    mocks.prepareManagedModels.mockResolvedValue({ profiles: [managedProfile], selected_profile_id: managedProfile.id, issues: [] });
    mocks.launchUi.mockResolvedValue(undefined);
    mocks.targetDoctor.mockResolvedValue({ ok: true, modalities: ['scene'], checks: [{ capability: 'media', kind: 'distribution', name: 'ffmpeg', ok: true }] });
    mocks.mcpClientConfig.mockResolvedValue('{"mcpServers":{"vidxp":{"command":"vidxp-mcp"}}}');
    mocks.installCodexPlugin.mockResolvedValue({
      plugin_name: 'vidxp', plugin_id: 'vidxp@vidxp-local', plugin_version: '0.4.0+codex.1234',
      marketplace_name: 'vidxp-local', marketplace_path: 'C:\\Data\\codex-marketplace\\.agents\\plugins\\marketplace.json',
      installed_path: 'C:\\Users\\test\\.codex\\plugins\\vidxp',
      detail: 'VidXP is installed in Codex with its MCP server and skills. Start a new Codex chat to use the updated plugin.',
    });
    mocks.browserServiceStatus.mockResolvedValue({ state: 'stopped', running: false, shared: false, port: null, local_url: null, network_url: null, detail: 'Stopped.' });
    mocks.startSharedBrowser.mockResolvedValue({ state: 'ready', running: true, shared: true, port: 8501, local_url: 'http://127.0.0.1:8501', network_url: 'http://192.168.1.20:8501', detail: 'Shared.' });
    mocks.stopBrowserService.mockResolvedValue({ state: 'stopped', running: false, shared: false, port: null, local_url: null, network_url: null, detail: 'Stopped.' });
    mocks.localServerStatus.mockResolvedValue({ state: 'stopped', running: false, shared: false, port: null, origin: null, health_url: null, mcp_url: null, bearer_token: null, detail: 'Stopped.' });
    mocks.startLocalServer.mockResolvedValue({ state: 'ready', running: true, shared: false, port: 32191, origin: 'http://127.0.0.1:32191', health_url: 'http://127.0.0.1:32191/health', mcp_url: 'http://127.0.0.1:32191/mcp', bearer_token: null, detail: 'Healthy.' });
    mocks.startSharedServer.mockResolvedValue({ state: 'ready', running: true, shared: true, port: 32191, origin: 'http://192.168.1.20:32191', health_url: 'http://192.168.1.20:32191/health', mcp_url: 'http://192.168.1.20:32191/mcp', bearer_token: 'secret-token', detail: 'Shared.' });
    mocks.stopLocalServer.mockResolvedValue({ state: 'stopped', running: false, shared: false, port: null, origin: null, health_url: null, mcp_url: null, bearer_token: null, detail: 'Stopped.' });
    mocks.localWorkerStatus.mockResolvedValue({ running: false, detail: 'Stopped.' });
    mocks.startLocalWorker.mockResolvedValue({ running: true, detail: 'Ready.' });
    mocks.stopLocalWorker.mockResolvedValue({ running: false, detail: 'Stopped.' });
    mocks.configureExternalInstallation.mockResolvedValue(localState);
  });

  it('shows the target-first choice without a remote placeholder', async () => {
    renderApp();
    expect(await screen.findByRole('radio', { name: /Use an existing installation/i })).toBeVisible();
    expect(screen.getByRole('radio', { name: /Set up VidXP for me/i })).toBeVisible();
    expect(screen.queryByText(/remote server/i)).not.toBeInTheDocument();
  });

  it('shows the restored control panel immediately while one startup recheck is pending', async () => {
    let resolve!: (value: typeof localState) => void;
    mocks.targetSetupState.mockResolvedValue(localState);
    mocks.recheckTargetState.mockReturnValue(new Promise((done) => { resolve = done; }));
    renderApp();
    expect(await screen.findByRole('heading', { name: 'Studio VidXP' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Check connection' })).toHaveAttribute('data-loading');
    expect(mocks.recheckTargetState).toHaveBeenCalledTimes(1);
    resolve(localState);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Check connection' })).not.toHaveAttribute('data-loading'));
  });

  it('opens the browser once and settles its loading state', async () => {
    mocks.targetSetupState.mockResolvedValue(localState);
    mocks.recheckTargetState.mockResolvedValue(localState);
    const user = userEvent.setup(); renderApp();
    const open = await screen.findByRole('button', { name: 'Open VidXP' });
    await user.click(open);
    expect(mocks.launchUi).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(open).not.toHaveAttribute('data-loading'));
  });

  it('uses doctor, exact MCP config, and supervised server controls for installed surfaces', async () => {
    const operational = {
      ...managedProfile,
      frontend,
      surfaces: ['worker', 'browser', 'mcp', 'server'],
      validation_error: null,
    };
    const state = { profiles: [operational], selected_profile_id: operational.id, issues: [] };
    mocks.targetSetupState.mockResolvedValue(state);
    mocks.recheckTargetState.mockResolvedValue(state);
    const user = userEvent.setup();
    renderApp();

    await screen.findByRole('heading', { name: 'Managed VidXP' });
    await waitFor(() => expect(mocks.localServerStatus).toHaveBeenCalled());
    await waitFor(() => expect(mocks.localWorkerStatus).toHaveBeenCalled());
    await waitFor(() => expect(mocks.browserServiceStatus).toHaveBeenCalled());

    await user.click(screen.getByRole('button', { name: 'Start processing' }));
    expect(mocks.startLocalWorker).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', { name: 'Stop processing' }));
    expect(mocks.stopLocalWorker).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole('button', { name: 'Check readiness' }));
    expect(await screen.findByText('VidXP is ready')).toBeVisible();
    expect(mocks.targetDoctor).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole('button', { name: 'Set up in Codex' }));
    expect(await screen.findByText('VidXP is installed in Codex with its MCP server and skills. Start a new Codex chat to use the updated plugin.')).toBeVisible();
    expect(mocks.installCodexPlugin).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole('button', { name: 'Copy MCP setup' }));
    expect(await screen.findByRole('heading', { name: 'Connect an AI assistant' })).toBeVisible();
    expect(mocks.mcpClientConfig).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole('button', { name: 'Share browser' }));
    expect(await screen.findByText('http://192.168.1.20:8501')).toBeVisible();
    expect(mocks.startSharedBrowser).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', { name: 'Stop sharing' }));
    expect(mocks.stopBrowserService).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole('button', { name: 'Start locally' }));
    expect(await screen.findByText('http://127.0.0.1:32191/mcp')).toBeVisible();
    expect(mocks.startLocalServer).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', { name: 'Share service' }));
    expect(await screen.findByText('http://192.168.1.20:32191/mcp')).toBeVisible();
    expect(mocks.startSharedServer).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', { name: 'Stop service' }));
    expect(mocks.stopLocalServer).toHaveBeenCalledTimes(1);
  });

  it('keeps worker timeouts with the worker control and clears them after recovery', async () => {
    const operational = {
      ...managedProfile,
      frontend,
      surfaces: ['worker', 'browser'],
      validation_error: null,
    };
    const state = { profiles: [operational], selected_profile_id: operational.id, issues: [] };
    mocks.targetSetupState.mockResolvedValue(state);
    mocks.recheckTargetState.mockResolvedValue(state);
    mocks.localWorkerStatus.mockRejectedValue('VidXP local processing failed: the operation exceeded 120 seconds');
    const user = userEvent.setup();
    renderApp();

    const workerFailure = await screen.findByRole('alert', { name: 'Local processing status could not be checked' });
    expect(workerFailure).toHaveTextContent('the operation exceeded 120 seconds');
    expect(screen.queryByRole('alert', { name: 'That did not work' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Start processing' }));

    expect(await screen.findByRole('button', { name: 'Stop processing' })).toBeVisible();
    expect(screen.queryByRole('alert', { name: 'Local processing status could not be checked' })).not.toBeInTheDocument();
  });

  it('adds optional features to the selected existing installation', async () => {
    const updated = { ...localProfile, surfaces: ['worker', 'browser', 'mcp'] };
    const updatedState = { profiles: [updated], selected_profile_id: updated.id, issues: [] };
    mocks.targetSetupState.mockResolvedValue(localState);
    mocks.recheckTargetState.mockResolvedValue(localState);
    mocks.configureExternalInstallation.mockResolvedValue(updatedState);
    const user = userEvent.setup();
    renderApp();

    await user.click(await screen.findByRole('button', { name: 'Setup options' }));
    expect(await screen.findByRole('heading', { name: 'Change features for this installation' })).toBeVisible();
    await user.click(screen.getByRole('checkbox', { name: /AI assistant integration/i }));
    await user.click(screen.getByRole('button', { name: 'Apply changes' }));

    await waitFor(() => expect(mocks.configureExternalInstallation).toHaveBeenCalledWith([], ['worker', 'browser', 'mcp']));
    expect(await screen.findByRole('button', { name: 'Set up in Codex' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Copy MCP setup' })).toBeVisible();
  });

  it('offers an in-place manifest update when the selected runtime contract is too old', async () => {
    const updateRequired = {
      ...localProfile,
      observed_vidxp_version: 'installed-release',
      probe_protocol_version: 1,
      capabilities: [],
      surfaces: [],
      validation_error: {
        code: 'runtime_update_required',
        message: 'This VidXP installation must be updated before Desktop can manage it.',
      },
    };
    const state = { profiles: [updateRequired], selected_profile_id: updateRequired.id, issues: [] };
    mocks.targetSetupState.mockResolvedValue(state);
    mocks.recheckTargetState.mockResolvedValue(state);
    mocks.runtimeManifest.mockResolvedValue({
      package_version: 'required-release',
      capabilities: { scene: { extra: 'scene', label: 'Visual scene search' } },
      surfaces: {
        worker: { extra: 'local-worker', label: 'Process videos on this computer', description: 'Run video work locally.', default: true },
        browser: { extra: 'frontend', label: 'Browser interface', description: 'Open VidXP in your browser.', default: true },
        mcp: { extra: 'mcp', label: 'AI assistant integration', description: 'Connect a compatible AI app.', default: false },
        server: { extra: 'server', label: 'App integration service', description: 'Let other local apps connect.', default: false },
      },
    });
    mocks.configureExternalInstallation.mockResolvedValue(localState);
    const user = userEvent.setup();
    renderApp();

    await user.click(await screen.findByRole('button', { name: 'Update this installation' }));
    expect(await screen.findByText(/from installed-release to required-release/i)).toBeVisible();
    expect(screen.getByRole('checkbox', { name: /Visual scene search/i })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /Process videos on this computer/i })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /Browser interface/i })).toBeChecked();
    await user.click(screen.getByRole('button', { name: 'Update and apply' }));

    await waitFor(() => expect(mocks.configureExternalInstallation).toHaveBeenCalledWith(['scene'], ['worker', 'browser']));
  });

  it('shows readiness progress and names the scope returned by doctor', async () => {
    const report = deferred<{ ok: boolean; modalities: string[]; checks: { capability: string; kind: string; name: string; ok: boolean }[] }>();
    mocks.targetSetupState.mockResolvedValue(localState);
    mocks.recheckTargetState.mockResolvedValue(localState);
    mocks.targetDoctor.mockReturnValue(report.promise);
    const user = userEvent.setup();
    renderApp();

    await user.click(await screen.findByRole('button', { name: 'Check readiness' }));
    expect(await screen.findByRole('heading', { name: 'Checking VidXP readiness' })).toBeVisible();
    expect(screen.getByText(/does not download or change anything/i)).toBeVisible();
    report.resolve({ ok: true, modalities: [], checks: [{ capability: 'media', kind: 'distribution', name: 'FFmpeg', ok: true }] });

    expect(await screen.findByText('No search features were checked')).toBeVisible();
    expect(screen.getByText('FFmpeg')).toBeVisible();
    expect(screen.getByText('Video tools are ready')).toBeVisible();
  });

  it('uses the parent exclusive operation while browser startup is pending', async () => {
    const browserManaged = {
      ...managedProfile,
      frontend,
      surfaces: ['browser'],
    };
    const setup = { profiles: [browserManaged], selected_profile_id: browserManaged.id, issues: [] };
    const opening = deferred<void>();
    mocks.targetSetupState.mockResolvedValue(setup);
    mocks.recheckTargetState.mockResolvedValue(setup);
    mocks.launchUi.mockReturnValue(opening.promise);
    const user = userEvent.setup(); renderApp();
    const open = await screen.findByRole('button', { name: 'Open VidXP' });
    await user.click(open);
    expect(open).toHaveAttribute('data-loading');
    expect(screen.getByRole('button', { name: 'Switch installation' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Check connection' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Setup options' })).toBeDisabled();
    opening.resolve();
    await waitFor(() => expect(open).not.toHaveAttribute('data-loading'));
  });

  it('adopts the inspected candidate without an installation action', async () => {
    mocks.activateLocalTarget.mockResolvedValue(localState);
    const user = userEvent.setup(); renderApp(); await enterLocal(user);
    await user.click(await screen.findByRole('radio', { name: /VidXP installation/i }));
    await user.click(await screen.findByRole('button', { name: 'Use this installation' }));
    expect(mocks.inspectLocalTarget).toHaveBeenCalledTimes(1);
    expect(mocks.activateLocalTarget).toHaveBeenCalledTimes(1);
    expect(mocks.installRuntime).not.toHaveBeenCalled();
    expect(mocks.configureExternalInstallation).not.toHaveBeenCalled();
  });

  it('keeps fresh discovery fields authoritative while retaining inspection UI', async () => {
    const user = userEvent.setup(); renderApp(); await enterLocal(user);
    await user.click(await screen.findByRole('radio', { name: /VidXP installation/i }));
    await screen.findByText('This VidXP installation is ready to connect.');
    mocks.discoverLocalTargets.mockResolvedValue([{ executable: 'C:\\Tools\\VidXP\\vidxp.exe', display_path: 'C:\\New\\display.exe', source: 'Fresh scan' }]);
    await user.click(screen.getByRole('button', { name: 'Scan again' }));
    await user.click(await screen.findByText('Technical details'));
    expect(await screen.findByText('C:\\New\\display.exe')).toBeVisible();
    expect(screen.getByText('This VidXP installation is ready to connect.')).toBeVisible();
    expect(screen.getAllByText('Found on this computer')).toHaveLength(2);
  });

  it('cancels managed setup back to the still-selected target', async () => {
    mocks.targetSetupState.mockResolvedValue(localState);
    mocks.recheckTargetState.mockResolvedValue(localState);
    mocks.beginManagedSetup.mockResolvedValue({ id: 'draft-1', previous_profile_id: localProfile.id });
    mocks.cancelManagedSetup.mockResolvedValue(localState);
    const user = userEvent.setup(); renderApp();
    await user.click(await screen.findByRole('button', { name: 'Switch installation' }));
    await user.click(screen.getByRole('radio', { name: /Set up VidXP for me/i }));
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await user.click(screen.getByRole('button', { name: 'Choose features' }));
    await user.click(await screen.findByRole('button', { name: 'Back' }));
    expect(mocks.cancelManagedSetup).toHaveBeenCalledWith('draft-1');
    expect(await screen.findByRole('heading', { name: 'Studio VidXP' })).toBeVisible();
  });

  it('supports selecting and forgetting saved profiles', async () => {
    const saved = { ...localProfile, id: 'local-2', display_name: 'Other VidXP' };
    const state = { profiles: [localProfile, saved], selected_profile_id: localProfile.id, issues: [] };
    mocks.targetSetupState.mockResolvedValue(state); mocks.recheckTargetState.mockResolvedValue(state);
    mocks.selectTargetProfile.mockResolvedValue({ ...state, selected_profile_id: saved.id });
    mocks.deleteTargetProfile.mockResolvedValue({ profiles: [saved], selected_profile_id: saved.id, issues: [] });
    const user = userEvent.setup(); renderApp();
    await user.click(await screen.findByRole('button', { name: 'Switch installation' }));
    await user.click(screen.getAllByRole('button', { name: 'Select' }).find((button) => !button.hasAttribute('disabled'))!);
    expect(mocks.selectTargetProfile).toHaveBeenCalledWith(saved.id);
    await user.click(screen.getByRole('button', { name: 'Switch installation' }));
    await user.click(screen.getAllByRole('button', { name: 'Forget' })[0]);
    expect(mocks.deleteTargetProfile).toHaveBeenCalled();
  });

  it('offers recheck recovery for an invalid restored target', async () => {
    const invalid = { ...localProfile, validation_error: { code: 'probe_timeout', message: 'Timed out.' } };
    const invalidState = { profiles: [invalid], selected_profile_id: invalid.id, issues: [] };
    mocks.targetSetupState.mockResolvedValue(invalidState); mocks.recheckTargetState.mockResolvedValue(invalidState);
    const user = userEvent.setup(); renderApp();
    expect(await screen.findByText('Timed out.')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Check connection' }));
    expect(mocks.recheckTargetState).toHaveBeenCalledTimes(2);
  });

  it('directs a managed target without browser surface back to managed setup', async () => {
    const setup = { profiles: [managedProfile], selected_profile_id: managedProfile.id, issues: [] };
    mocks.targetSetupState.mockResolvedValue(setup); mocks.recheckTargetState.mockResolvedValue(setup);
    renderApp();
    expect(await screen.findByText('The browser interface is not enabled')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Setup options' })).toBeEnabled();
  });

  it('keeps ready managed settings read-only until a draft is dirty, then offers Apply and Reset', async () => {
    mocks.runtimeStatus.mockResolvedValue({ state: 'ready', ready: true, runtime_profile: 'runtime-a', package_version: '0.4.0', capabilities: ['scene'], surfaces: [], model_directory: 'C:\\Models', detail: 'Ready.' });
    const user = userEvent.setup(); renderApp(); await enterManaged(user);
    const apply = await screen.findByRole('button', { name: 'Apply update' });
    expect(apply).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: /VidXP app|Browser interface/i }));
    expect(apply).toBeEnabled();
    expect(screen.getByText(/switches to the updated setup only after/i)).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Reset changes' }));
    expect(apply).toBeDisabled();
    await waitFor(() => expect(mocks.modelDirectoryInventory).toHaveBeenCalledTimes(2));
  });

  it('preserves a broken managed runtime draft instead of selecting every option', async () => {
    mocks.runtimeManifest.mockResolvedValue({
      package_version: '0.4.0',
      capabilities: {
        actor: { extra: 'actor', label: 'Actor recognition' },
        scene: { extra: 'scene', label: 'Visual scene search' },
      },
      surfaces: { browser: { extra: 'frontend', label: 'Browser interface', description: 'Browser UI', default: true } },
    });
    mocks.runtimeStatus.mockResolvedValue({
      state: 'broken', ready: false, runtime_profile: 'runtime-a', package_version: '0.4.0',
      capabilities: ['scene'], surfaces: [], model_directory: 'D:\\CustomModels', detail: 'FFmpeg was not found.',
    });
    const user = userEvent.setup(); renderApp(); await enterManaged(user);
    expect(screen.getByRole('checkbox', { name: /Visual scene search/i })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /Actor recognition/i })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: /VidXP app|Browser interface/i })).not.toBeChecked();
    await user.click(screen.getByText('Storage location'));
    expect(screen.getByText('D:\\CustomModels')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Repair VidXP' })).toBeEnabled();
  });

  it('uses manifest defaults and submits a replacement for a corrupt runtime pointer', async () => {
    mocks.runtimeManifest.mockResolvedValue({
      package_version: '0.4.0',
      capabilities: {
        actor: { extra: 'actor', label: 'Actor recognition' },
        scene: { extra: 'scene', label: 'Visual scene search' },
      },
      surfaces: { browser: { extra: 'frontend', label: 'Browser interface', description: 'Browser UI', default: true } },
    });
    mocks.runtimeStatus.mockResolvedValue({
      state: 'broken', ready: false, runtime_profile: null, package_version: '0.4.0',
      capabilities: [], surfaces: [], model_directory: 'C:\\Models', detail: 'The active runtime pointer is invalid.',
    });
    const user = userEvent.setup(); renderApp(); await enterManaged(user);
    expect(screen.getByRole('checkbox', { name: /Actor recognition/i })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /Visual scene search/i })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /VidXP app|Browser interface/i })).toBeChecked();
    expect(screen.getByText(/could not read the saved setup/i)).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Rebuild VidXP' }));
    await waitFor(() => expect(mocks.installRuntime).toHaveBeenCalledWith(expect.objectContaining({
      capabilities: expect.arrayContaining(['actor', 'scene']),
      surfaces: ['browser'],
      draft_id: 'draft-1',
    })));
    expect(screen.queryByText('Select at least one search feature.')).not.toBeInTheDocument();
  });

  it('optionally includes MCP and local sharing in a managed installation', async () => {
    const user = userEvent.setup();
    renderApp();
    await enterManaged(user);

    await user.click(screen.getByRole('checkbox', { name: /AI assistant integration/i }));
    await user.click(screen.getByRole('checkbox', { name: /App integration service/i }));
    await user.click(screen.getByRole('button', { name: 'Install VidXP' }));

    expect(mocks.installRuntime).toHaveBeenCalledWith(expect.objectContaining({
      surfaces: ['worker', 'browser', 'mcp', 'server'],
    }));
  });

  it('passes the scoped draft through first-time installation and does not auto-open the browser', async () => {
    const user = userEvent.setup(); renderApp(); await enterManaged(user);
    await user.click(await screen.findByRole('button', { name: 'Install VidXP' }));
    await waitFor(() => expect(mocks.installRuntime).toHaveBeenCalledWith(expect.objectContaining({ draft_id: 'draft-1' })));
    expect(mocks.installMediaRuntime).toHaveBeenCalledWith('draft-1');
    expect(mocks.launchUi).not.toHaveBeenCalled();
  });

  it('keeps a managed installation failure visible in the setup dialog until it is acknowledged', async () => {
    mocks.installRuntime.mockRejectedValueOnce('The installed runtime failed its compatibility check.');
    const user = userEvent.setup(); renderApp(); await enterManaged(user);

    await user.click(screen.getByRole('button', { name: 'Install VidXP' }));

    expect(await screen.findByRole('dialog', { name: 'Setup could not finish' })).toBeVisible();
    expect(screen.getByRole('alert', { name: 'VidXP was not installed' })).toHaveTextContent('The installed runtime failed its compatibility check.');
    expect(screen.getByText(/model files already downloaded remain cached/i)).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Review setup' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Setup could not finish' })).not.toBeInTheDocument());
  });

  it('blocks setup interaction and reports managed installation stages', async () => {
    const media = deferred<{ ready: boolean }>();
    mocks.installMediaRuntime.mockReturnValue(media.promise);
    let reportProgress: ((progress: { draft_id: string; current: number; total: number; stage: string; message: string; model_message?: string; model_current?: number; model_total?: number }) => void) | undefined;
    mocks.onManagedSetupProgress.mockImplementation(async (handler) => {
      reportProgress = handler;
      return vi.fn();
    });
    const user = userEvent.setup(); renderApp(); await enterManaged(user);

    await user.click(screen.getByRole('button', { name: 'Install VidXP' }));

    expect(screen.getByRole('dialog', { name: 'Setting up VidXP' })).toBeVisible();
    expect(screen.getByText('Step 1 of 8')).toBeVisible();
    expect(screen.getByText('Checking FFmpeg and required video codecs')).toBeVisible();
    reportProgress?.({ draft_id: 'draft-1', current: 4, total: 8, stage: 'dependencies', message: 'Installing the selected search features' });
    expect(await screen.findByText('Step 4 of 8')).toBeVisible();
    expect(screen.getByText('Installing the selected search features')).toBeVisible();
    reportProgress?.({
      draft_id: 'draft-1',
      current: 7,
      total: 8,
      stage: 'models',
      message: 'Verifying and downloading selected model files',
      model_message: 'Preparing model artifacts.',
    });
    expect(await screen.findByText('Preparing model artifacts.')).toBeVisible();
    reportProgress?.({
      draft_id: 'draft-1',
      current: 7,
      total: 8,
      stage: 'models',
      message: 'Verifying and downloading selected model files',
      model_message: 'Downloading dialogue transcription model.',
      model_current: 512 * 1024 * 1024,
      model_total: 1024 * 1024 * 1024,
    });
    expect(await screen.findByText('Downloading dialogue transcription model.')).toBeVisible();
    expect(screen.getByText('512.0 MiB of 1.00 GiB')).toBeVisible();
    expect(screen.getByRole('progressbar', { name: 'Current model download progress' })).toHaveAttribute('aria-valuenow', '50');

    media.resolve({ ready: true });
  });

  it('coalesces duplicate managed Continue actions', async () => {
    const pending = deferred<{ id: string; previous_profile_id: null }>();
    mocks.beginManagedSetup.mockReturnValue(pending.promise);
    const user = userEvent.setup(); renderApp();
    await screen.findByRole('heading', { name: 'How would you like to set up VidXP?' });
    await user.click(screen.getByRole('radio', { name: /Set up VidXP for me/i }));
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    const continueButton = screen.getByRole('button', { name: 'Choose features' });
    await user.dblClick(continueButton);
    expect(mocks.beginManagedSetup).toHaveBeenCalledTimes(1);
    pending.resolve({ id: 'draft-1', previous_profile_id: null });
    expect(await screen.findByRole('heading', { name: 'Choose your VidXP features' })).toBeVisible();
  });

  it('freezes managed controls and coalesces Apply while a replacement is running', async () => {
    mocks.runtimeStatus.mockResolvedValue({ state: 'ready', ready: true, runtime_profile: 'runtime-a', package_version: '0.4.0', capabilities: ['scene'], surfaces: [], model_directory: 'C:\\Models', detail: 'Ready.' });
    const media = deferred<{ ready: boolean }>();
    mocks.installMediaRuntime.mockReturnValue(media.promise);
    const user = userEvent.setup(); renderApp(); await enterManaged(user);
    const browser = screen.getByRole('checkbox', { name: /VidXP app|Browser interface/i });
    await user.click(browser);
    const apply = screen.getByRole('button', { name: 'Apply update' });
    await user.dblClick(apply);
    expect(mocks.installMediaRuntime).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Back' })).toBeDisabled();
    expect(browser).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Change location…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Reset changes' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Check downloaded models' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Open VidXP' })).toBeDisabled();
    media.resolve({ ready: true });
    expect(await screen.findByRole('heading', { name: 'Managed VidXP' })).toBeVisible();
  });

  it('prepares models for an unchanged ready runtime without making the draft dirty', async () => {
    const setup = { profiles: [managedProfile], selected_profile_id: managedProfile.id, issues: [] };
    mocks.targetSetupState.mockResolvedValue(setup);
    mocks.recheckTargetState.mockResolvedValue(setup);
    mocks.runtimeStatus.mockResolvedValue({ state: 'ready', ready: true, runtime_profile: 'runtime-a', package_version: '0.4.0', capabilities: ['scene'], surfaces: [], model_directory: 'C:\\Models', detail: 'Ready.' });
    const user = userEvent.setup(); renderApp();
    await user.click(await screen.findByRole('button', { name: 'Setup options' }));
    await user.click(screen.getByRole('button', { name: 'Choose features' }));
    await screen.findByRole('heading', { name: 'Choose your VidXP features' });
    expect(screen.getByRole('button', { name: 'Apply update' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Check downloaded models' }));
    expect(mocks.prepareManagedModels).toHaveBeenCalledWith('draft-1');
  });

  it('disables managed runtime actions while an external target is selected', async () => {
    const setup = {
      profiles: [localProfile, managedProfile],
      selected_profile_id: localProfile.id,
      issues: [],
    };
    mocks.targetSetupState.mockResolvedValue(setup);
    mocks.recheckTargetState.mockResolvedValue(setup);
    mocks.runtimeStatus.mockResolvedValue({ state: 'ready', ready: true, runtime_profile: 'runtime-a', package_version: '0.4.0', capabilities: ['scene'], surfaces: ['browser'], model_directory: 'C:\\Models', detail: 'Ready.' });
    const user = userEvent.setup(); renderApp();
    await user.click(await screen.findByRole('button', { name: 'Switch installation' }));
    await enterManaged(user);
    expect(screen.getByText(/not your active installation/i)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Check downloaded models' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Open VidXP' })).toBeDisabled();
    expect(mocks.prepareManagedModels).not.toHaveBeenCalled();
    expect(mocks.launchUi).not.toHaveBeenCalled();
  });

  it('uses the committed install state without a fallible status refresh', async () => {
    const user = userEvent.setup(); renderApp(); await enterManaged(user);
    await user.click(screen.getByRole('button', { name: 'Install VidXP' }));
    expect(await screen.findByRole('heading', { name: 'Managed VidXP' })).toBeVisible();
    expect(mocks.runtimeStatus).toHaveBeenCalledTimes(1);
    expect(mocks.targetSetupState).toHaveBeenCalledTimes(1);
  });

  it('coalesces duplicate saved-profile Select actions', async () => {
    const saved = { ...localProfile, id: 'local-2', display_name: 'Other VidXP' };
    const state = { profiles: [localProfile, saved], selected_profile_id: localProfile.id, issues: [] };
    const selection = deferred<typeof state>();
    mocks.targetSetupState.mockResolvedValue(state);
    mocks.recheckTargetState.mockResolvedValue(state);
    mocks.selectTargetProfile.mockReturnValue(selection.promise);
    const user = userEvent.setup(); renderApp();
    await user.click(await screen.findByRole('button', { name: 'Switch installation' }));
    const select = screen.getAllByRole('button', { name: 'Select' }).find((button) => !button.hasAttribute('disabled'))!;
    await user.dblClick(select);
    expect(mocks.selectTargetProfile).toHaveBeenCalledTimes(1);
    selection.resolve({ ...state, selected_profile_id: saved.id });
    expect(await screen.findByRole('heading', { name: 'Other VidXP' })).toBeVisible();
  });
});
