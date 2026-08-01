import { MantineProvider } from '@mantine/core';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  targetSetupState: vi.fn(),
  discoverLocalTargets: vi.fn(),
  chooseLocalExecutable: vi.fn(),
  inspectLocalTarget: vi.fn(),
  activateLocalTarget: vi.fn(),
  chooseManagedTarget: vi.fn(),
  installMediaRuntime: vi.fn(),
  installRuntime: vi.fn(),
  runtimeManifest: vi.fn(),
  runtimeStatus: vi.fn(),
  launchUi: vi.fn(),
  hideToTray: vi.fn(),
  chooseModelDirectory: vi.fn(),
  modelDirectoryInventory: vi.fn(),
}));

const windowMocks = vi.hoisted(() => ({
  close: vi.fn(),
  isMaximized: vi.fn(),
  minimize: vi.fn(),
  onResized: vi.fn(),
  toggleMaximize: vi.fn(),
}));

vi.mock('@tauri-apps/api/window', () => ({
  getCurrentWindow: () => windowMocks,
}));

vi.mock('./tauri', () => ({
  ...mocks,
  selectedProfile: (state: any) =>
    state.selected_profile ??
    state.profiles.find((profile: any) => profile.id === state.selected_profile_id) ??
    null,
  isCompatible: (validation: any) =>
    validation.compatible === true || validation.status === 'compatible',
  errorMessage: (error: unknown, fallback: string) =>
    typeof error === 'string' ? error : fallback,
  displayPath: (path: string) => path,
}));

import { App } from './App';

const emptyState = { profiles: [], selected_profile_id: null, selected_profile: null };
const localProfile = {
  id: 'local-1',
  display_name: 'Studio VidXP',
  schema_version: 1,
  kind: 'existing_local',
  lifecycle_ownership: 'external',
  canonical_executable: 'C:\\Tools\\VidXP\\vidxp.exe',
  vidxp_version: '0.4.0',
  probe_version: 1,
  launch_protocol_version: 1,
  last_validated_at: '2026-08-01T10:00:00Z',
  can_launch_frontend: true,
};

function renderApp() {
  return render(
    <MantineProvider env="test" defaultColorScheme="dark">
      <App />
    </MantineProvider>,
  );
}

async function enterLocalFlow(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole('heading', { name: 'Where should VidXP run?' });
  await user.click(screen.getByRole('radio', { name: /Use an existing installation/i }));
  await user.click(screen.getByRole('button', { name: 'Continue' }));
  await screen.findByRole('heading', { name: 'Connect this desktop to VidXP' });
}

describe('target-first setup', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    windowMocks.isMaximized.mockResolvedValue(false);
    windowMocks.onResized.mockResolvedValue(vi.fn());
    windowMocks.toggleMaximize.mockResolvedValue(undefined);
    windowMocks.minimize.mockResolvedValue(undefined);
    windowMocks.close.mockResolvedValue(undefined);
    mocks.targetSetupState.mockResolvedValue(emptyState);
    mocks.discoverLocalTargets.mockResolvedValue([
      {
        executable: 'C:\\Tools\\VidXP\\vidxp.exe',
        canonical_executable: 'C:\\Tools\\VidXP\\vidxp.exe',
        source: 'PATH',
      },
    ]);
    mocks.inspectLocalTarget.mockResolvedValue({
      state: 'ready_to_use',
      adoptable: true,
      executable: 'C:\\Tools\\VidXP\\vidxp.exe',
      reported_version: '0.4.0',
      probe_compatible: true,
      launch_compatible: true,
      message: 'Compatible contracts.',
      remediation: '',
      validation: {
        compatible: true,
        canonical_executable: 'C:\\Tools\\VidXP\\vidxp.exe',
        vidxp_version: '0.4.0',
        protocol_version: 1,
        launch_protocol_version: 1,
        python_version: '3.14.6',
        can_launch_frontend: true,
      },
    });
    mocks.modelDirectoryInventory.mockResolvedValue({ directory: 'models', exists: false, readable: true, total_bytes: 0, file_count: 0, recognized_models: [], empty: true, verification_required: false, truncated: false, detail: 'No cached models were found.' });
    mocks.activateLocalTarget.mockResolvedValue(localProfile);
  });

  it('opens with two real target choices and no remote placeholder', async () => {
    renderApp();

    expect(await screen.findByRole('radio', { name: /Use an existing installation/i })).toBeVisible();
    expect(screen.getByRole('radio', { name: /Set up VidXP for me/i })).toBeVisible();
    expect(screen.queryByText(/remote server/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled();
  });

  it('renders the real VidXP identity once in an accessible custom title bar', async () => {
    renderApp();

    await screen.findByRole('heading', { name: 'Where should VidXP run?' });
    expect(screen.getAllByText('VidXP', { exact: true })).toHaveLength(1);
    expect(screen.getByTestId('vidxp-logo')).toHaveAttribute('src', '/icon.png');
    expect(document.querySelector('.brandMark')).not.toBeInTheDocument();
  });

  it('exposes minimize, maximize, restore, and normal close requests', async () => {
    const user = userEvent.setup();
    const { unmount } = renderApp();
    await screen.findByRole('heading', { name: 'Where should VidXP run?' });

    await user.click(screen.getByRole('button', { name: 'Minimize window' }));
    await user.click(screen.getByRole('button', { name: 'Maximize window' }));
    await user.click(screen.getByRole('button', { name: 'Close window' }));
    await user.dblClick(document.querySelector('.titleBar') as HTMLElement);

    expect(windowMocks.minimize).toHaveBeenCalledTimes(1);
    expect(windowMocks.toggleMaximize).toHaveBeenCalledTimes(2);
    expect(windowMocks.close).toHaveBeenCalledTimes(1);
    expect(document.querySelector('.titleBar')).toHaveAttribute('data-tauri-drag-region');
    expect(screen.getByRole('button', { name: 'Minimize window' })).not.toHaveAttribute('data-tauri-drag-region');
    unmount();
  });

  it('updates the maximize control when the native window state changes', async () => {
    let resized: (() => void) | undefined;
    windowMocks.onResized.mockImplementation(async (handler: () => void) => {
      resized = handler;
      return vi.fn();
    });
    renderApp();
    expect(await screen.findByRole('button', { name: 'Maximize window' })).toBeVisible();

    windowMocks.isMaximized.mockResolvedValue(true);
    await act(async () => resized?.());
    expect(await screen.findByRole('button', { name: 'Restore window' })).toBeVisible();
  });

  it('supports keyboard target selection and exposes visible semantic controls', async () => {
    const user = userEvent.setup();
    renderApp();
    const local = await screen.findByRole('radio', { name: /Use an existing installation/i });

    local.focus();
    await user.keyboard('[Space]');

    expect(local).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled();
  });

  it('does not silently select a discovered executable', async () => {
    const user = userEvent.setup();
    renderApp();
    await enterLocalFlow(user);

    const candidate = await screen.findByRole('radio', { name: /VidXP executable/i });
    expect(candidate).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByText('Found')).toBeVisible();
    expect(screen.getByText('Not checked')).toBeVisible();
    expect(screen.queryByText(/VidXP 0\.4/)).not.toBeInTheDocument();
    expect(mocks.inspectLocalTarget).not.toHaveBeenCalled();
  });

  it('validates and activates a local target without invoking any install command', async () => {
    const user = userEvent.setup();
    renderApp();
    await enterLocalFlow(user);
    mocks.targetSetupState.mockResolvedValue({
      profiles: [localProfile],
      selected_profile_id: localProfile.id,
      selected_profile: localProfile,
    });

    await user.click(await screen.findByRole('radio', { name: /VidXP executable/i }));
    expect((await screen.findAllByText('Ready to use'))[0]).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Use this installation' }));

    await screen.findByRole('heading', { name: 'Studio VidXP' });
    expect(mocks.inspectLocalTarget).toHaveBeenCalledWith('C:\\Tools\\VidXP\\vidxp.exe');
    expect(mocks.activateLocalTarget).toHaveBeenCalledTimes(1);
    expect(mocks.installMediaRuntime).not.toHaveBeenCalled();
    expect(mocks.installRuntime).not.toHaveBeenCalled();
  });

  it('moves a selected candidate through checking into a compatible result', async () => {
    const user = userEvent.setup();
    let resolveInspection!: (value: any) => void;
    mocks.inspectLocalTarget.mockReturnValue(new Promise((resolve) => { resolveInspection = resolve; }));
    renderApp();
    await enterLocalFlow(user);

    await user.click(await screen.findByRole('radio', { name: /VidXP executable/i }));
    expect(screen.getByText('Checking…')).toBeVisible();
    await act(async () => resolveInspection({ state: 'ready_to_use', adoptable: true, executable: 'C:\\Tools\\VidXP\\vidxp.exe', reported_version: '0.3.0', probe_compatible: true, launch_compatible: true, message: 'Compatible contracts.', remediation: '', validation: { compatible: true, canonical_executable: 'C:\\Tools\\VidXP\\vidxp.exe', protocol_version: 1, launch_protocol_version: 1, python_version: '3.14.6', can_launch_frontend: true } }));
    expect((await screen.findAllByText('Ready to use'))[0]).toBeVisible();
    expect(screen.getAllByText('Compatible · protocol 1')).toHaveLength(2);
  });

  it('shows a broken executable as cannot start with remediation', async () => {
    const user = userEvent.setup();
    mocks.inspectLocalTarget.mockResolvedValue({ state: 'cannot_start', adoptable: false, executable: 'C:\\Tools\\VidXP\\vidxp.exe', reported_version: null, probe_compatible: false, launch_compatible: false, message: 'This executable could not start well enough to report its version.', remediation: 'Repair this external installation with its own package-management workflow, then check it again.', technical_details: 'ModuleNotFoundError: SQLAlchemy' });
    renderApp();
    await enterLocalFlow(user);

    await user.click(await screen.findByRole('radio', { name: /VidXP executable/i }));
    expect((await screen.findAllByText('Cannot start'))[0]).toBeVisible();
    expect(screen.getByText(/Repair this external installation/)).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Use this installation' })).not.toBeInTheDocument();
  });

  it('saves an external target without a frontend as action-required, not operationally complete', async () => {
    const user = userEvent.setup();
    const remediation = "Use this installation's own package-management workflow to install VidXP with the 'frontend' extra, then revalidate.";
    const externalWithoutFrontend = {
      ...localProfile,
      can_launch_frontend: false,
      frontend: {
        available: false,
        launchable: false,
        optional: true,
        code: 'frontend_unavailable',
        message: 'The command-line installation is usable, but the browser interface is missing.',
        remediation,
      },
    };
    mocks.inspectLocalTarget.mockResolvedValue({
      state: 'ready_to_use', adoptable: true, executable: 'C:\\Tools\\VidXP\\vidxp.exe', reported_version: '0.3.0', probe_compatible: true, launch_compatible: true, message: 'Compatible contracts.', remediation: '',
      validation: {
        compatible: true, canonical_executable: 'C:\\Tools\\VidXP\\vidxp.exe', vidxp_version: '0.3.0', protocol_version: 1, launch_protocol_version: 1, python_version: '3.14.6', can_launch_frontend: false, frontend: externalWithoutFrontend.frontend,
      },
    });
    mocks.activateLocalTarget.mockResolvedValue(externalWithoutFrontend);
    renderApp();
    await enterLocalFlow(user);

    await user.click(await screen.findByRole('radio', { name: /VidXP executable/i }));
    expect(await screen.findByText('Desktop action required')).toBeVisible();
    expect(screen.getByText(/command-line installation is usable/i)).toBeVisible();
    expect(screen.getByText(remediation)).toBeVisible();
    expect(screen.queryByText('Ready to launch')).not.toBeInTheDocument();

    mocks.targetSetupState.mockResolvedValue({
      profiles: [externalWithoutFrontend],
      selected_profile_id: externalWithoutFrontend.id,
      selected_profile: externalWithoutFrontend,
    });
    await user.click(screen.getByRole('button', { name: 'Save external target' }));

    expect(await screen.findByText('Action required · no usable desktop surface')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Open VidXP' })).toBeDisabled();
    expect(mocks.installMediaRuntime).not.toHaveBeenCalled();
    expect(mocks.installRuntime).not.toHaveBeenCalled();
  });

  it('keeps validated details associated with the canonical candidate identity', async () => {
    const user = userEvent.setup();
    mocks.discoverLocalTargets.mockResolvedValue([
      {
        executable: '\\\\?\\C:\\Tools\\VidXP\\vidxp.exe',
        canonical_executable: '\\\\?\\C:\\Tools\\VidXP\\vidxp.exe',
        display_path: 'C:\\Tools\\VidXP\\vidxp.exe',
        source: 'PATH',
      },
      {
        executable: '\\\\?\\D:\\Apps\\vidxp.exe',
        canonical_executable: '\\\\?\\D:\\Apps\\vidxp.exe',
        display_path: 'D:\\Apps\\vidxp.exe',
        source: 'PATH',
      },
    ]);
    mocks.inspectLocalTarget.mockResolvedValue({
      state: 'ready_to_use', adoptable: true, executable: '\\\\?\\C:\\Tools\\VidXP\\vidxp.exe', reported_version: '0.3.0', probe_compatible: true, launch_compatible: true, message: 'Compatible contracts.', remediation: '',
      validation: { compatible: true, canonical_executable: '\\\\?\\C:\\Tools\\VidXP\\vidxp.exe', display_executable: 'C:\\Tools\\VidXP\\vidxp.exe', vidxp_version: '0.3.0', protocol_version: 1, launch_protocol_version: 1, python_version: '3.14.6', can_launch_frontend: true },
    });
    renderApp();
    await enterLocalFlow(user);

    const candidate = await screen.findByRole('radio', { name: /C:\\Tools\\VidXP\\vidxp\.exe/i });
    await user.click(candidate);
    expect((await screen.findAllByText('VidXP 0.3.0'))[0]).toBeVisible();
    expect(screen.getAllByText('Compatible · protocol 1')).toHaveLength(2);
    expect(screen.getByText('Python')).toBeVisible();
    expect(screen.getAllByText('Found')).toHaveLength(1);
    expect(mocks.inspectLocalTarget).toHaveBeenCalledWith('\\\\?\\C:\\Tools\\VidXP\\vidxp.exe');
    expect(screen.queryByText('\\\\?\\C:\\Tools\\VidXP\\vidxp.exe')).not.toBeInTheDocument();
  });

  it('shows an old executable as update required without making it adoptable', async () => {
    const user = userEvent.setup();
    mocks.inspectLocalTarget.mockResolvedValue({ state: 'update_required', adoptable: false, executable: 'C:\\Tools\\VidXP\\vidxp.exe', reported_version: '0.4.0b0', probe_compatible: false, launch_compatible: false, message: 'This VidXP installation does not provide a compatible Desktop probe and launch contract.', remediation: 'Update this external installation with its own package-management workflow, then check it again.', technical_details: 'No such command: desktop-probe' });
    renderApp();
    await enterLocalFlow(user);

    await user.click(await screen.findByRole('radio', { name: /VidXP executable/i }));
    expect((await screen.findAllByText('Update required'))[0]).toBeVisible();
    expect(screen.getAllByText('VidXP 0.4.0b0')[0]).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Use this installation' })).not.toBeInTheDocument();
  });

  it('allows explicit browsing when discovery is empty', async () => {
    const user = userEvent.setup();
    mocks.discoverLocalTargets.mockResolvedValue([]);
    mocks.chooseLocalExecutable.mockResolvedValue({ executable: '/opt/vidxp/bin/vidxp' });
    renderApp();
    await enterLocalFlow(user);

    expect(await screen.findByText(/No candidates were found automatically/)).toBeVisible();
    await user.click(screen.getByRole('button', { name: /Browse for an executable/ }));

    expect(await screen.findByText('/opt/vidxp/bin/vidxp')).toBeVisible();
    expect(mocks.inspectLocalTarget).toHaveBeenCalledWith('/opt/vidxp/bin/vidxp');
  });

  it('restores the selected profile and reports validation problems accessibly', async () => {
    mocks.targetSetupState.mockResolvedValue({
      profiles: [localProfile],
      selected_profile_id: localProfile.id,
      selected_profile: localProfile,
      selected_profile_error: {
        code: 'TARGET_INCOMPATIBLE',
        message: 'This installation requires a newer desktop.',
        action: 'Update VidXP Desktop, then retry.',
      },
    });
    renderApp();

    expect(await screen.findByRole('heading', { name: 'Studio VidXP' })).toBeVisible();
    expect(screen.getByRole('alert')).toHaveTextContent('TARGET_INCOMPATIBLE');
    expect(screen.getByRole('button', { name: 'Open VidXP' })).toBeDisabled();
  });

  it('directs a managed target without a browser surface back to managed setup', async () => {
    const managedProfile = {
      ...localProfile,
      id: 'managed-default',
      display_name: 'Desktop-managed VidXP',
      kind: 'managed',
      lifecycle_ownership: 'desktop',
      can_launch_frontend: false,
    };
    mocks.targetSetupState.mockResolvedValue({
      profiles: [managedProfile],
      selected_profile_id: managedProfile.id,
      selected_profile: managedProfile,
    });
    renderApp();

    expect(await screen.findByRole('heading', { name: 'Desktop-managed VidXP' })).toBeVisible();
    expect(screen.getByText('Unavailable · return to managed setup to enable the browser surface')).toBeVisible();
  });

  it('reveals managed installation only after an explicit confirmation', async () => {
    const user = userEvent.setup();
    mocks.chooseManagedTarget.mockResolvedValue({});
    mocks.runtimeManifest.mockResolvedValue({ package_version: '0.4.0', capabilities: {}, surfaces: {} });
    mocks.runtimeStatus.mockResolvedValue({ state: 'broken', ready: false, package_version: '0.4.0', capabilities: [], surfaces: [], model_directory: 'models', detail: 'FFmpeg was not found. ffprobe was not found. Run guided setup, then retry.' });
    renderApp();
    await screen.findByRole('heading', { name: 'Where should VidXP run?' });

    await user.click(screen.getByRole('radio', { name: /Set up VidXP for me/i }));
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    expect(screen.getByRole('heading', { name: /Let VidXP Desktop manage the runtime/ })).toBeVisible();
    expect(mocks.chooseManagedTarget).not.toHaveBeenCalled();
    expect(mocks.installRuntime).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Continue to setup' }));
    await screen.findByRole('heading', { name: 'Set up local processing' });
    expect(screen.getByRole('alert')).toHaveTextContent('Media tools required');
    expect(mocks.chooseManagedTarget).toHaveBeenCalledTimes(1);
    expect(mocks.installRuntime).not.toHaveBeenCalled();
  });

  it('renders first-time managed setup as neutral and shows cached model reuse', async () => {
    const user = userEvent.setup();
    mocks.chooseManagedTarget.mockResolvedValue({});
    mocks.runtimeManifest.mockResolvedValue({ package_version: '0.4.0', capabilities: {}, surfaces: {} });
    mocks.runtimeStatus.mockResolvedValue({ state: 'never_configured', ready: false, package_version: '0.4.0', capabilities: [], surfaces: [], model_directory: 'C:\\Models', detail: 'No Desktop-managed runtime has been created yet.' });
    mocks.modelDirectoryInventory.mockResolvedValue({ directory: 'C:\\Models', exists: true, readable: true, total_bytes: 4413072968, file_count: 34, recognized_models: [{ id: 'siglip2-base', label: 'Google SigLIP2 base' }, { id: 'yunet', label: 'YuNet' }], empty: false, verification_required: true, truncated: false, detail: 'Cached files detected; verification required.' });
    renderApp();
    await screen.findByRole('heading', { name: 'Where should VidXP run?' });
    await user.click(screen.getByRole('radio', { name: /Set up VidXP for me/i }));
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await user.click(screen.getByRole('button', { name: 'Continue to setup' }));

    expect(await screen.findByText('No Desktop-managed runtime has been created yet.')).toBeVisible();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByText(/4\.11 GiB of cached model files found/)).toBeVisible();
    expect(screen.getByText('Google SigLIP2 base')).toBeVisible();
    expect(screen.getByText(/verification required.*reuse valid cached files/i)).toBeVisible();
    expect(screen.getByRole('switch', { name: /Verify cached models and download anything missing/i })).toBeChecked();
  });

  it('refreshes inventory after a folder change and installs with that same path', async () => {
    const user = userEvent.setup();
    const firstStatus = { state: 'never_configured', ready: false, package_version: '0.4.0', capabilities: [], surfaces: [], model_directory: 'C:\\Models', detail: 'No Desktop-managed runtime has been created yet.' };
    mocks.chooseManagedTarget.mockResolvedValue({});
    mocks.runtimeManifest.mockResolvedValue({ package_version: '0.4.0', capabilities: { scene: { extra: 'scene', label: 'Visual scene search', description: 'Scene models' } }, surfaces: {} });
    mocks.runtimeStatus.mockResolvedValueOnce(firstStatus).mockResolvedValue({ ...firstStatus, state: 'ready', ready: true, capabilities: ['scene'] });
    mocks.chooseModelDirectory.mockResolvedValue('D:\\VidXP models');
    mocks.modelDirectoryInventory.mockImplementation(async (directory: string) => ({ directory, exists: true, readable: true, total_bytes: 10, file_count: 1, recognized_models: [], empty: false, verification_required: true, truncated: false, detail: 'Cached files detected; verification required.' }));
    mocks.installMediaRuntime.mockResolvedValue({ ready: true });
    mocks.installRuntime.mockResolvedValue({ package_version: '0.4.0', capabilities: ['scene'], surfaces: [], model_directory: 'D:\\VidXP models', prepared: true });
    renderApp();
    await screen.findByRole('heading', { name: 'Where should VidXP run?' });
    await user.click(screen.getByRole('radio', { name: /Set up VidXP for me/i }));
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await user.click(screen.getByRole('button', { name: 'Continue to setup' }));
    await screen.findByText('Visual scene search');

    await user.click(screen.getByRole('button', { name: 'Choose folder…' }));
    await waitFor(() => expect(mocks.modelDirectoryInventory).toHaveBeenCalledWith('D:\\VidXP models'));
    await user.click(screen.getByRole('button', { name: 'Configure VidXP' }));

    await waitFor(() => expect(mocks.installRuntime).toHaveBeenCalledWith(expect.objectContaining({ model_directory: 'D:\\VidXP models' })));
    expect(mocks.modelDirectoryInventory).toHaveBeenCalledWith('D:\\VidXP models');
  });

  it('labels bounded model inventory totals as partial when the scan is truncated', async () => {
    const user = userEvent.setup();
    mocks.chooseManagedTarget.mockResolvedValue({});
    mocks.runtimeManifest.mockResolvedValue({ package_version: '0.4.0', capabilities: {}, surfaces: {} });
    mocks.runtimeStatus.mockResolvedValue({ state: 'never_configured', ready: false, package_version: '0.4.0', capabilities: [], surfaces: [], model_directory: 'C:\\Models', detail: 'No Desktop-managed runtime has been created yet.' });
    mocks.modelDirectoryInventory.mockResolvedValue({ directory: 'C:\\Models', exists: true, readable: true, total_bytes: 1073741824, file_count: 100000, recognized_models: [], empty: false, verification_required: true, truncated: true, detail: 'Cached files were found. The bounded inventory is partial; preparation must verify required artifacts.' });
    renderApp();
    await screen.findByRole('heading', { name: 'Where should VidXP run?' });
    await user.click(screen.getByRole('radio', { name: /Set up VidXP for me/i }));
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await user.click(screen.getByRole('button', { name: 'Continue to setup' }));

    expect(await screen.findByText(/At least 1\.00 GiB across 100000 cached files scanned/)).toBeVisible();
    expect(screen.getByText(/bounded inventory is partial/)).toBeVisible();
    expect(screen.queryByText(/1\.00 GiB of cached model files found/)).not.toBeInTheDocument();
    expect(screen.getByText(/verification required.*reuse valid cached files/i)).toBeVisible();
  });

  it('renders the setup flow without console errors', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    renderApp();
    await screen.findByRole('heading', { name: 'Where should VidXP run?' });
    await waitFor(() => expect(consoleError).not.toHaveBeenCalled());
    consoleError.mockRestore();
  });
});
