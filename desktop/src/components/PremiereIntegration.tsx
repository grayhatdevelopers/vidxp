import { Alert, Badge, Button, Code, Group, Loader, Stack, Text, Title } from '@mantine/core';
import { IconDownload, IconRefresh, IconTrash } from '@tabler/icons-react';
import { useEffect, useState } from 'react';

import {
  errorMessage,
  installPremiereExtensions,
  localServerStatus,
  premiereIntegrationState,
  startLocalServer,
  uninstallPremiereExtensions,
  type PremiereInstallResult,
  type PremiereIntegrationState as IntegrationState,
} from '../tauri';

interface PremiereIntegrationProps {
  operationPending?: boolean;
  serverEnabled: boolean;
  canConfigureDependency: boolean;
  onConfigureDependency: () => void;
}

export function PremiereIntegration({ operationPending, serverEnabled, canConfigureDependency, onConfigureDependency }: PremiereIntegrationProps) {
  const [state, setState] = useState<IntegrationState | null>(null);
  const [busy, setBusy] = useState<'refresh' | 'install' | 'remove' | null>('refresh');
  const [failure, setFailure] = useState<string | null>(null);
  const [result, setResult] = useState<PremiereInstallResult | null>(null);

  async function refresh() {
    setBusy('refresh');
    setFailure(null);
    try {
      setState(await premiereIntegrationState());
    } catch (error) {
      setFailure(errorMessage(error, 'VidXP could not inspect Premiere installations.'));
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => { void refresh(); }, []);

  async function install() {
    setBusy('install');
    setFailure(null);
    setResult(null);
    try {
      const server = await localServerStatus();
      if (!server.running) await startLocalServer();
      setResult(await installPremiereExtensions());
      setState(await premiereIntegrationState());
    } catch (error) {
      setFailure(errorMessage(error, 'VidXP could not install the Premiere extension.'));
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    setBusy('remove');
    setFailure(null);
    setResult(null);
    try {
      await uninstallPremiereExtensions();
      setState(await premiereIntegrationState());
    } catch (error) {
      setFailure(errorMessage(error, 'VidXP could not remove the Premiere extension.'));
    } finally {
      setBusy(null);
    }
  }

  const installed = Boolean(state?.cep_installed || state?.uxp_installed);
  const unavailablePackage = state && (!state.cep_package_available || !state.uxp_package_available);

  return (
    <div className="setupPanel runtimeControlPanel">
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2} className="panelTitle">Premiere Pro extension</Title>
          <Text size="sm" className="mutedText">Desktop installs the matching extension for every compatible Premiere version it finds. No repository, Node.js, or developer mode is required.</Text>
        </div>
        <Button variant="subtle" leftSection={<IconRefresh size={16} />} loading={busy === 'refresh'} disabled={Boolean(operationPending || (busy && busy !== 'refresh'))} onClick={() => void refresh()}>Refresh</Button>
      </Group>

      {busy === 'refresh' && !state ? <Loader size="sm" mt="lg" /> : state && <Stack gap="sm" mt="lg">
        {!state.platform_supported
          ? <Alert color="gray" title="Premiere integration is unavailable">Desktop can install the Premiere extension on Windows and macOS.</Alert>
          : state.installations.length === 0
          ? <Alert color="yellow" title="Premiere was not found">{state.detail} Installing is still safe: the CEP and UXP packages have non-overlapping host ranges.</Alert>
          : state.installations.map((installation) => <Group key={installation.executable} justify="space-between" className="runtimeControlRow" wrap="nowrap">
              <div><Text fw={650}>{installation.display_name}</Text><Text size="xs" className="mutedText">Version {installation.version} · {installation.host_kind === 'cep' ? 'CEP extension' : installation.host_kind === 'uxp' ? 'UXP extension' : 'Not supported'}</Text><Code className="pathCode">{installation.executable}</Code></div>
              <Badge color={installation.compatible ? 'teal' : 'gray'} variant="light">{installation.compatible ? 'Compatible' : 'Unsupported'}</Badge>
            </Group>)}

        <Group gap="xs">
          <Badge color={state.cep_installed ? 'teal' : 'gray'} variant="light">Premiere 23–25.5 {state.cep_installed ? 'installed' : 'available'}</Badge>
          <Badge color={state.uxp_installed ? 'teal' : 'gray'} variant="light">Premiere 25.6+ {state.uxp_installed ? 'installed' : 'available'}</Badge>
        </Group>
        {!serverEnabled && <Alert color="blue" title={canConfigureDependency ? 'Premiere setup includes its connection' : 'Add the app connection first'}>{canConfigureDependency ? 'VidXP will add local processing and its private app service when you continue.' : 'This externally managed VidXP installation needs the App integration service. Add that feature with its setup, then return here.'}</Alert>}
        {!state.installer_available && state.platform_supported && <Alert color="blue" title="Creative Cloud confirmation required">Adobe's background plugin installer was not found. VidXP will open the packaged extension so Creative Cloud can finish the installation.</Alert>}
        {unavailablePackage && <Alert color="red" title="Extension packages are missing">Reinstall or update VidXP Desktop. Release installers include both Premiere packages.</Alert>}
        {result && <Alert color="teal" title={result.opened_packages.length ? 'Finish in Creative Cloud' : 'Premiere extension installed'}>{result.detail}</Alert>}
        {failure && <Alert color="red" title="Premiere setup did not finish" role="alert">{failure}</Alert>}
        <Group justify="flex-end">
          {installed && <Button color="red" variant="subtle" leftSection={<IconTrash size={16} />} loading={busy === 'remove'} disabled={Boolean(operationPending || (busy && busy !== 'remove'))} onClick={() => void remove()}>Remove extension</Button>}
          <Button leftSection={<IconDownload size={16} />} loading={busy === 'install'} disabled={!state.platform_supported || Boolean(unavailablePackage) || Boolean(operationPending || (busy && busy !== 'install'))} onClick={() => serverEnabled ? void install() : onConfigureDependency()}>{serverEnabled ? installed ? 'Reinstall for Premiere' : 'Install for Premiere' : canConfigureDependency ? 'Set up Premiere' : 'Open setup options'}</Button>
        </Group>
      </Stack>}
    </div>
  );
}
