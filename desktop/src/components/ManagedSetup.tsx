import {
  Alert,
  Button,
  Checkbox,
  Group,
  Loader,
  Stack,
  Switch,
  Text,
  Title,
} from '@mantine/core';
import { IconAlertCircle, IconArrowLeft, IconExternalLink, IconFolderOpen } from '@tabler/icons-react';
import { useEffect, useState } from 'react';

import {
  chooseModelDirectory,
  displayPath,
  errorMessage,
  hideToTray,
  installMediaRuntime,
  installRuntime,
  launchUi,
  runtimeManifest,
  runtimeStatus,
  type RuntimeManifest,
  type RuntimeStatus,
} from '../tauri';

interface ManagedSetupProps {
  onBack: () => void;
  onReady: () => Promise<void>;
}

export function ManagedSetup({ onBack, onReady }: ManagedSetupProps) {
  const [manifest, setManifest] = useState<RuntimeManifest | null>(null);
  const [status, setStatus] = useState<RuntimeStatus | null>(null);
  const [capabilities, setCapabilities] = useState<string[]>([]);
  const [surfaces, setSurfaces] = useState<string[]>([]);
  const [prepareModels, setPrepareModels] = useState(true);
  const [modelDirectory, setModelDirectory] = useState('');
  const [busy, setBusy] = useState<'load' | 'folder' | 'install' | 'launch' | null>('load');
  const [message, setMessage] = useState('Loading managed runtime options…');
  const [failure, setFailure] = useState<string | null>(null);

  async function load() {
    setBusy('load');
    setFailure(null);
    try {
      const [nextManifest, nextStatus] = await Promise.all([runtimeManifest(), runtimeStatus()]);
      setManifest(nextManifest);
      setStatus(nextStatus);
      setCapabilities(
        nextStatus.ready ? nextStatus.capabilities : Object.keys(nextManifest.capabilities),
      );
      setSurfaces(
        nextStatus.ready
          ? nextStatus.surfaces
          : Object.entries(nextManifest.surfaces)
              .filter(([, surface]) => surface.default)
              .map(([id]) => id),
      );
      setModelDirectory(nextStatus.model_directory);
      setMessage(nextStatus.ready ? 'The managed runtime is ready.' : nextStatus.detail);
    } catch (error) {
      setFailure(errorMessage(error, 'Managed setup could not be loaded.'));
    } finally {
      setBusy((current) => (current === 'load' ? null : current));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  function toggleValue(value: string, checked: boolean, setter: (next: string[]) => void, current: string[]) {
    setter(checked ? [...current, value] : current.filter((item) => item !== value));
  }

  async function chooseFolder() {
    setBusy('folder');
    try {
      const selected = await chooseModelDirectory();
      if (selected) setModelDirectory(selected);
    } catch (error) {
      setFailure(errorMessage(error, 'The model folder could not be selected.'));
    } finally {
      setBusy((current) => (current === 'folder' ? null : current));
    }
  }

  async function install() {
    if (capabilities.length === 0) {
      setFailure('Select at least one capability.');
      return;
    }
    setBusy('install');
    setFailure(null);
    try {
      setMessage('Checking FFmpeg and required codecs…');
      await installMediaRuntime();
      setMessage(
        prepareModels
          ? 'Creating the managed runtime and downloading selected models…'
          : 'Creating the managed runtime…',
      );
      const result = await installRuntime({
        capabilities,
        surfaces,
        prepare_models: prepareModels,
        model_directory: modelDirectory || undefined,
      });
      setModelDirectory(result.model_directory);
      const nextStatus = await runtimeStatus();
      setStatus(nextStatus);
      setMessage(result.prepared ? 'Runtime and selected models are ready.' : 'Runtime ready. Model downloads were deferred.');
      await onReady();
      if (result.surfaces.includes('browser')) await launchUi();
      else await hideToTray();
    } catch (error) {
      setFailure(errorMessage(error, 'Managed setup failed. No active runtime was replaced.'));
      setBusy(null);
    }
  }

  async function launch() {
    setBusy('launch');
    setFailure(null);
    try {
      await launchUi();
    } catch (error) {
      setFailure(errorMessage(error, 'VidXP could not be opened.'));
      setBusy(null);
    }
  }

  const isBusy = busy === 'load' || busy === 'folder' || busy === 'install' || busy === 'launch';
  const attentionTitle = /ffmpeg|ffprobe/i.test(message)
    ? 'Media tools required'
    : 'Managed runtime not ready';

  return (
    <section aria-labelledby="managed-setup-title">
      <Button variant="subtle" leftSection={<IconArrowLeft aria-hidden="true" size={17} />} onClick={onBack} disabled={busy === 'install'}>Back</Button>
      <div className="sectionHeading compactHeading">
        <Text className="eyebrow">DESKTOP-MANAGED RUNTIME</Text>
        <Title id="managed-setup-title" order={1} className="pageTitle">Set up local processing</Title>
        <Text className="lede">VidXP Desktop owns this private runtime. Installation starts only when you confirm below.</Text>
      </div>

      {!manifest ? (
        <div className="emptyState" role="status" aria-live="polite"><Loader size="sm" /> Loading setup options…</div>
      ) : (
        <Stack gap="md">
          <div className="setupPanel">
            <Title order={2} className="panelTitle" mb="md">Capabilities</Title>
            <div className="optionGrid">
              {Object.entries(manifest.capabilities).map(([id, capability]) => (
                <Checkbox.Card
                  className="optionCard"
                  key={id}
                  checked={capabilities.includes(id)}
                  onClick={() => toggleValue(id, !capabilities.includes(id), setCapabilities, capabilities)}
                >
                  <Group wrap="nowrap" align="flex-start"><Checkbox.Indicator aria-hidden="true" /><div><Text fw={650}>{capability.label}</Text><Text size="sm" className="mutedText">{capability.description || `Installs the ${capability.extra} capability.`}</Text></div></Group>
                </Checkbox.Card>
              ))}
            </div>
          </div>

          <div className="setupPanel">
            <Title order={2} className="panelTitle" mb="md">Interface</Title>
            <Stack gap="xs">
              {Object.entries(manifest.surfaces).map(([id, surface]) => (
                <Checkbox key={id} checked={surfaces.includes(id)} onChange={(event) => toggleValue(id, event.currentTarget.checked, setSurfaces, surfaces)} label={surface.label} description={surface.description} />
              ))}
            </Stack>
          </div>

          <div className="setupPanel">
            <Group justify="space-between" align="center" wrap="nowrap">
              <div className="folderCopy"><Text fw={650}>Model storage</Text><Text size="sm" className="pathText">{modelDirectory ? displayPath(modelDirectory) : 'Using the default location'}</Text></div>
              <Button variant="default" leftSection={<IconFolderOpen aria-hidden="true" size={16} />} loading={busy === 'folder'} onClick={() => void chooseFolder()}>Choose folder…</Button>
            </Group>
            <Switch mt="lg" checked={prepareModels} onChange={(event) => setPrepareModels(event.currentTarget.checked)} label="Download selected models now" description="Turn this off to defer model downloads until later." />
          </div>
        </Stack>
      )}

      {status && !status.ready && busy !== 'install' && (
        <Alert
          className="managedAttention"
          icon={<IconAlertCircle aria-hidden="true" />}
          color="yellow"
          title={attentionTitle}
          role="alert"
        >
          {message}
        </Alert>
      )}

      <div className="managedFooter">
        <div className="statusRegion" role="status" aria-live="polite" aria-atomic="true">{isBusy && <Loader size="xs" />}{(isBusy || status?.ready) && message}</div>
        {status?.ready ? (
          <Button leftSection={<IconExternalLink aria-hidden="true" size={17} />} loading={busy === 'launch'} onClick={() => void launch()}>Open VidXP</Button>
        ) : (
          <Button loading={busy === 'install'} disabled={!manifest || busy === 'load'} onClick={() => void install()}>Configure VidXP</Button>
        )}
      </div>
      {failure && <Alert mt="md" icon={<IconAlertCircle aria-hidden="true" />} color="red" title="Could not continue" role="alert">{failure}</Alert>}
    </section>
  );
}
