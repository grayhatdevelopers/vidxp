import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  targetSetupState: vi.fn(),
  discoverLocalTargets: vi.fn(),
  chooseLocalExecutable: vi.fn(),
  validateLocalTarget: vi.fn(),
  activateLocalTarget: vi.fn(),
  chooseManagedTarget: vi.fn(),
  installMediaRuntime: vi.fn(),
  installRuntime: vi.fn(),
  runtimeManifest: vi.fn(),
  runtimeStatus: vi.fn(),
  launchUi: vi.fn(),
  hideToTray: vi.fn(),
  chooseModelDirectory: vi.fn(),
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
    mocks.targetSetupState.mockResolvedValue(emptyState);
    mocks.discoverLocalTargets.mockResolvedValue([
      {
        executable: 'C:\\Tools\\VidXP\\vidxp.exe',
        canonical_executable: 'C:\\Tools\\VidXP\\vidxp.exe',
        display_name: 'VidXP 0.4',
        source: 'PATH',
      },
    ]);
    mocks.validateLocalTarget.mockResolvedValue({
      compatible: true,
      canonical_executable: 'C:\\Tools\\VidXP\\vidxp.exe',
      vidxp_version: '0.4.0',
      protocol_version: 1,
      python_version: '3.14.6',
      can_launch_frontend: true,
    });
    mocks.activateLocalTarget.mockResolvedValue(localProfile);
  });

  it('opens with two real target choices and no remote placeholder', async () => {
    renderApp();

    expect(await screen.findByRole('radio', { name: /Use an existing installation/i })).toBeVisible();
    expect(screen.getByRole('radio', { name: /Set up VidXP for me/i })).toBeVisible();
    expect(screen.queryByText(/remote server/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled();
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

    const candidate = await screen.findByRole('radio', { name: /VidXP 0.4/i });
    expect(candidate).toHaveAttribute('aria-checked', 'false');
    expect(screen.queryByRole('heading', { name: 'Review and validate' })).not.toBeInTheDocument();
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

    await user.click(await screen.findByRole('radio', { name: /VidXP 0.4/i }));
    expect(screen.getAllByText('C:\\Tools\\VidXP\\vidxp.exe')).toHaveLength(2);
    await user.click(screen.getByRole('button', { name: 'Validate installation' }));
    expect(await screen.findByText('Compatible VidXP installation')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Use this installation' }));

    await screen.findByRole('heading', { name: 'Studio VidXP' });
    expect(mocks.validateLocalTarget).toHaveBeenCalledWith('C:\\Tools\\VidXP\\vidxp.exe');
    expect(mocks.activateLocalTarget).toHaveBeenCalledTimes(1);
    expect(mocks.installMediaRuntime).not.toHaveBeenCalled();
    expect(mocks.installRuntime).not.toHaveBeenCalled();
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
    mocks.validateLocalTarget.mockResolvedValue({
      compatible: true,
      canonical_executable: 'C:\\Tools\\VidXP\\vidxp.exe',
      vidxp_version: '0.3.0',
      protocol_version: 1,
      launch_protocol_version: 1,
      python_version: '3.14.6',
      can_launch_frontend: false,
      frontend: externalWithoutFrontend.frontend,
    });
    mocks.activateLocalTarget.mockResolvedValue(externalWithoutFrontend);
    renderApp();
    await enterLocalFlow(user);

    await user.click(await screen.findByRole('radio', { name: /VidXP 0.4/i }));
    await user.click(screen.getByRole('button', { name: 'Validate installation' }));

    expect(await screen.findByText('Compatible installation · desktop action required')).toBeVisible();
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

  it('allows explicit browsing when discovery is empty', async () => {
    const user = userEvent.setup();
    mocks.discoverLocalTargets.mockResolvedValue([]);
    mocks.chooseLocalExecutable.mockResolvedValue({ executable: '/opt/vidxp/bin/vidxp' });
    renderApp();
    await enterLocalFlow(user);

    expect(await screen.findByText(/No candidates were found automatically/)).toBeVisible();
    await user.click(screen.getByRole('button', { name: /Browse for an executable/ }));

    expect(await screen.findAllByText('/opt/vidxp/bin/vidxp')).toHaveLength(2);
    expect(screen.getByRole('heading', { name: 'Review and validate' })).toBeVisible();
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

  it('reveals managed installation only after an explicit confirmation', async () => {
    const user = userEvent.setup();
    mocks.chooseManagedTarget.mockResolvedValue({});
    mocks.runtimeManifest.mockResolvedValue({ package_version: '0.4.0', capabilities: {}, surfaces: {} });
    mocks.runtimeStatus.mockResolvedValue({ ready: false, package_version: '0.4.0', capabilities: [], surfaces: [], model_directory: 'models', detail: 'Not installed.' });
    renderApp();
    await screen.findByRole('heading', { name: 'Where should VidXP run?' });

    await user.click(screen.getByRole('radio', { name: /Set up VidXP for me/i }));
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    expect(screen.getByRole('heading', { name: /Let VidXP Desktop manage the runtime/ })).toBeVisible();
    expect(mocks.chooseManagedTarget).not.toHaveBeenCalled();
    expect(mocks.installRuntime).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Continue to setup' }));
    await screen.findByRole('heading', { name: 'Set up local processing' });
    expect(mocks.chooseManagedTarget).toHaveBeenCalledTimes(1);
    expect(mocks.installRuntime).not.toHaveBeenCalled();
  });

  it('renders the setup flow without console errors', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    renderApp();
    await screen.findByRole('heading', { name: 'Where should VidXP run?' });
    await waitFor(() => expect(consoleError).not.toHaveBeenCalled());
    consoleError.mockRestore();
  });
});
