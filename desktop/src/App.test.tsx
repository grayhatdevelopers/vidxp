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
  prepareManagedModels: vi.fn(),
  runtimeManifest: vi.fn(), runtimeStatus: vi.fn(), launchUi: vi.fn(),
  chooseModelDirectory: vi.fn(), modelDirectoryInventory: vi.fn(),
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
  last_validated_at: '2026-08-01T10:00:00Z', validation_error: null, capabilities: [], surfaces: ['browser'],
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
  await screen.findByRole('heading', { name: 'Where should VidXP run?' });
  await user.click(screen.getByRole('radio', { name: /Use an existing installation/i }));
  await user.click(screen.getByRole('button', { name: 'Continue' }));
}

async function enterManaged(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole('heading', { name: 'Where should VidXP run?' });
  await user.click(screen.getByRole('radio', { name: /Set up VidXP for me/i }));
  await user.click(screen.getByRole('button', { name: 'Continue' }));
  await user.click(screen.getByRole('button', { name: 'Continue to setup' }));
  await screen.findByRole('heading', { name: 'Set up local processing' });
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
    mocks.runtimeManifest.mockResolvedValue({ package_version: '0.4.0', capabilities: { scene: { extra: 'scene', label: 'Visual scene search' } }, surfaces: { browser: { extra: 'frontend', label: 'Browser interface', description: 'Browser UI', default: true } } });
    mocks.runtimeStatus.mockResolvedValue({ state: 'never_configured', ready: false, runtime_profile: null, package_version: '0.4.0', capabilities: [], surfaces: [], model_directory: 'C:\\Models', detail: 'No managed runtime yet.' });
    mocks.modelDirectoryInventory.mockResolvedValue({ directory: 'C:\\Models', exists: false, readable: true, total_bytes: 0, file_count: 0, recognized_models: [], empty: true, verification_required: false, truncated: false, detail: 'Empty.' });
    mocks.installMediaRuntime.mockResolvedValue({ ready: true });
    mocks.installRuntime.mockResolvedValue({
      install: { package_version: '0.4.0', capabilities: ['scene'], surfaces: ['browser'], model_directory: 'C:\\Models', prepared: true },
      setup: { profiles: [managedProfile], selected_profile_id: managedProfile.id, issues: [] },
    });
    mocks.prepareManagedModels.mockResolvedValue({ profiles: [managedProfile], selected_profile_id: managedProfile.id, issues: [] });
    mocks.launchUi.mockResolvedValue(undefined);
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
    expect(screen.getByRole('button', { name: 'Recheck target' })).toHaveAttribute('data-loading');
    expect(mocks.recheckTargetState).toHaveBeenCalledTimes(1);
    resolve(localState);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Recheck target' })).not.toHaveAttribute('data-loading'));
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
    expect(screen.getByRole('button', { name: 'Manage targets' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Recheck target' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Manage setup' })).toBeDisabled();
    opening.resolve();
    await waitFor(() => expect(open).not.toHaveAttribute('data-loading'));
  });

  it('adopts the inspected candidate without an installation action', async () => {
    mocks.activateLocalTarget.mockResolvedValue(localState);
    const user = userEvent.setup(); renderApp(); await enterLocal(user);
    await user.click(await screen.findByRole('radio', { name: /VidXP executable/i }));
    await user.click(await screen.findByRole('button', { name: 'Use this installation' }));
    expect(mocks.inspectLocalTarget).toHaveBeenCalledTimes(1);
    expect(mocks.activateLocalTarget).toHaveBeenCalledTimes(1);
    expect(mocks.installRuntime).not.toHaveBeenCalled();
  });

  it('keeps fresh discovery fields authoritative while retaining inspection UI', async () => {
    const user = userEvent.setup(); renderApp(); await enterLocal(user);
    await user.click(await screen.findByRole('radio', { name: /VidXP executable/i }));
    await screen.findByText('Compatible contracts.');
    mocks.discoverLocalTargets.mockResolvedValue([{ executable: 'C:\\Tools\\VidXP\\vidxp.exe', display_path: 'C:\\New\\display.exe', source: 'Fresh scan' }]);
    await user.click(screen.getByRole('button', { name: 'Scan again' }));
    expect(await screen.findByText('C:\\New\\display.exe')).toBeVisible();
    expect(screen.getByText('Compatible contracts.')).toBeVisible();
    expect(screen.getByText('Discovered via Fresh scan')).toBeVisible();
  });

  it('cancels managed setup back to the still-selected target', async () => {
    mocks.targetSetupState.mockResolvedValue(localState);
    mocks.recheckTargetState.mockResolvedValue(localState);
    mocks.beginManagedSetup.mockResolvedValue({ id: 'draft-1', previous_profile_id: localProfile.id });
    mocks.cancelManagedSetup.mockResolvedValue(localState);
    const user = userEvent.setup(); renderApp();
    await user.click(await screen.findByRole('button', { name: 'Manage targets' }));
    await user.click(screen.getByRole('radio', { name: /Set up VidXP for me/i }));
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await user.click(screen.getByRole('button', { name: 'Continue to setup' }));
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
    await user.click(await screen.findByRole('button', { name: 'Manage targets' }));
    await user.click(screen.getAllByRole('button', { name: 'Select' }).find((button) => !button.hasAttribute('disabled'))!);
    expect(mocks.selectTargetProfile).toHaveBeenCalledWith(saved.id);
    await user.click(screen.getByRole('button', { name: 'Manage targets' }));
    await user.click(screen.getAllByRole('button', { name: 'Forget' })[0]);
    expect(mocks.deleteTargetProfile).toHaveBeenCalled();
  });

  it('offers recheck recovery for an invalid restored target', async () => {
    const invalid = { ...localProfile, validation_error: { code: 'probe_timeout', message: 'Timed out.' } };
    const invalidState = { profiles: [invalid], selected_profile_id: invalid.id, issues: [] };
    mocks.targetSetupState.mockResolvedValue(invalidState); mocks.recheckTargetState.mockResolvedValue(invalidState);
    const user = userEvent.setup(); renderApp();
    expect(await screen.findByText('Timed out.')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Recheck target' }));
    expect(mocks.recheckTargetState).toHaveBeenCalledTimes(2);
  });

  it('directs a managed target without browser surface back to managed setup', async () => {
    const setup = { profiles: [managedProfile], selected_profile_id: managedProfile.id, issues: [] };
    mocks.targetSetupState.mockResolvedValue(setup); mocks.recheckTargetState.mockResolvedValue(setup);
    renderApp();
    expect(await screen.findByText('Unavailable · return to managed setup to enable the browser surface')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Manage setup' })).toBeEnabled();
  });

  it('keeps ready managed settings read-only until a draft is dirty, then offers Apply and Reset', async () => {
    mocks.runtimeStatus.mockResolvedValue({ state: 'ready', ready: true, runtime_profile: 'runtime-a', package_version: '0.4.0', capabilities: ['scene'], surfaces: [], model_directory: 'C:\\Models', detail: 'Ready.' });
    const user = userEvent.setup(); renderApp(); await enterManaged(user);
    const apply = await screen.findByRole('button', { name: 'Apply update' });
    expect(apply).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: /Browser interface/i }));
    expect(apply).toBeEnabled();
    expect(screen.getByText(/installed runtime remains active while Desktop creates/i)).toBeVisible();
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
    expect(screen.getByRole('checkbox', { name: /Browser interface/i })).not.toBeChecked();
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
    expect(screen.getByRole('checkbox', { name: /Browser interface/i })).toBeChecked();
    expect(screen.getByText(/cannot recover settings from the unreadable pointer/i)).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Configure replacement' }));
    await waitFor(() => expect(mocks.installRuntime).toHaveBeenCalledWith(expect.objectContaining({
      capabilities: expect.arrayContaining(['actor', 'scene']),
      surfaces: ['browser'],
      draft_id: 'draft-1',
    })));
    expect(screen.queryByText('Select at least one capability.')).not.toBeInTheDocument();
  });

  it('passes the scoped draft through first-time installation and does not auto-open the browser', async () => {
    const user = userEvent.setup(); renderApp(); await enterManaged(user);
    await user.click(await screen.findByRole('button', { name: 'Configure VidXP' }));
    await waitFor(() => expect(mocks.installRuntime).toHaveBeenCalledWith(expect.objectContaining({ draft_id: 'draft-1' })));
    expect(mocks.installMediaRuntime).toHaveBeenCalledWith('draft-1');
    expect(mocks.launchUi).not.toHaveBeenCalled();
  });

  it('coalesces duplicate managed Continue actions', async () => {
    const pending = deferred<{ id: string; previous_profile_id: null }>();
    mocks.beginManagedSetup.mockReturnValue(pending.promise);
    const user = userEvent.setup(); renderApp();
    await screen.findByRole('heading', { name: 'Where should VidXP run?' });
    await user.click(screen.getByRole('radio', { name: /Set up VidXP for me/i }));
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    const continueButton = screen.getByRole('button', { name: 'Continue to setup' });
    await user.dblClick(continueButton);
    expect(mocks.beginManagedSetup).toHaveBeenCalledTimes(1);
    pending.resolve({ id: 'draft-1', previous_profile_id: null });
    expect(await screen.findByRole('heading', { name: 'Set up local processing' })).toBeVisible();
  });

  it('freezes managed controls and coalesces Apply while a replacement is running', async () => {
    mocks.runtimeStatus.mockResolvedValue({ state: 'ready', ready: true, runtime_profile: 'runtime-a', package_version: '0.4.0', capabilities: ['scene'], surfaces: [], model_directory: 'C:\\Models', detail: 'Ready.' });
    const media = deferred<{ ready: boolean }>();
    mocks.installMediaRuntime.mockReturnValue(media.promise);
    const user = userEvent.setup(); renderApp(); await enterManaged(user);
    const browser = screen.getByRole('checkbox', { name: /Browser interface/i });
    await user.click(browser);
    const apply = screen.getByRole('button', { name: 'Apply update' });
    await user.dblClick(apply);
    expect(mocks.installMediaRuntime).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Back' })).toBeDisabled();
    expect(browser).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Choose folder…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Reset changes' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Prepare / verify models' })).toBeDisabled();
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
    await user.click(await screen.findByRole('button', { name: 'Manage setup' }));
    await user.click(screen.getByRole('button', { name: 'Continue to setup' }));
    await screen.findByRole('heading', { name: 'Set up local processing' });
    expect(screen.getByRole('button', { name: 'Apply update' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Prepare / verify models' }));
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
    await user.click(await screen.findByRole('button', { name: 'Manage targets' }));
    await enterManaged(user);
    expect(screen.getByText(/another target is currently selected/i)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Prepare / verify models' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Open VidXP' })).toBeDisabled();
    expect(mocks.prepareManagedModels).not.toHaveBeenCalled();
    expect(mocks.launchUi).not.toHaveBeenCalled();
  });

  it('uses the committed install state without a fallible status refresh', async () => {
    const user = userEvent.setup(); renderApp(); await enterManaged(user);
    await user.click(screen.getByRole('button', { name: 'Configure VidXP' }));
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
    await user.click(await screen.findByRole('button', { name: 'Manage targets' }));
    const select = screen.getAllByRole('button', { name: 'Select' }).find((button) => !button.hasAttribute('disabled'))!;
    await user.dblClick(select);
    expect(mocks.selectTargetProfile).toHaveBeenCalledTimes(1);
    selection.resolve({ ...state, selected_profile_id: saved.id });
    expect(await screen.findByRole('heading', { name: 'Other VidXP' })).toBeVisible();
  });
});
