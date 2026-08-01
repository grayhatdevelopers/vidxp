import {
  Alert,
  Badge,
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
  modelDirectoryInventory,
  runtimeManifest,
  runtimeStatus,
  type RuntimeManifest,
  type RuntimeStatus,
  type ModelDirectoryInventory,
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
  const [inventory, setInventory] = useState<ModelDirectoryInventory | null>(null);
  const [inventoryLoading, setInventoryLoading] = useState(false);
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
      setInventoryLoading(true);
      setInventory(await modelDirectoryInventory(nextStatus.model_directory));
      setMessage(nextStatus.ready ? 'The managed runtime is ready.' : nextStatus.detail);
    } catch (error) {
      setFailure(errorMessage(error, 'Managed setup could not be loaded.'));
    } finally {
      setInventoryLoading(false);
      setBusy((current) => (current === 'load' ? null : current));
    }
  }

  async function refreshInventory(directory: string) {
    setInventoryLoading(true);
    try {
      setInventory(await modelDirectoryInventory(directory));
    } catch (error) {
      setInventory(null);
      setFailure(errorMessage(error, 'The selected model folder could not be inventoried.'));
    } finally {
      setInventoryLoading(false);
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
      if (selected) {
        setModelDirectory(selected);
        await refreshInventory(selected);
      }
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
          ? 'Creating the managed runtime, verifying cached models, and downloading anything missing…'
          : 'Creating the managed runtime…',
      );
      const result = await installRuntime({
        capabilities,
        surfaces,
        prepare_models: prepareModels,
        model_directory: modelDirectory || undefined,
      });
      setModelDirectory(result.model_directory);
      await refreshInventory(result.model_directory);
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
  const attentionTitle = /ffmpeg|ffprobe/i.test(message) ? 'Media tools required' : 'Managed runtime needs attention';

  function formatBytes(bytes: number) {
    if (bytes < 1024) return `${bytes} B`;
    const units = ['KiB', 'MiB', 'GiB', 'TiB'];
    let value = bytes;
    let unit = -1;
    do {
      value /= 1024;
      unit += 1;
    } while (value >= 1024 && unit < units.length - 1);
    return `${value.toFixed(value >= 10 ? 1 : 2)} ${units[unit]}`;
  }

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
            <div className="cacheInventory" aria-live="polite">
              {inventoryLoading ? (
                <Text size="sm" mt="md"><Loader size="xs" /> Checking cached model files…</Text>
              ) : inventory && !inventory.readable ? (
                <Alert mt="md" color="red" title="Model folder cannot be read">{inventory.detail}</Alert>
              ) : inventory?.empty ? (
                <Text size="sm" mt="md">No cached models were found in this folder.</Text>
              ) : inventory ? (
                <>
                  <Text fw={650} mt="md">{formatBytes(inventory.total_bytes)} of cached model files found <Text span fw={400} className="mutedText">({inventory.file_count} files)</Text></Text>
                  {inventory.recognized_models.length > 0 && (
                    <Group gap="xs" mt="xs">{inventory.recognized_models.map((model) => <Badge key={model.id} variant="light">{model.label}</Badge>)}</Group>
                  )}
                  <Text size="sm" mt="xs">Cached files detected; verification required. VidXP will reuse valid cached files and download only missing material.</Text>
                </>
              ) : null}
            </div>
            <Switch mt="lg" checked={prepareModels} onChange={(event) => setPrepareModels(event.currentTarget.checked)} label="Verify cached models and download anything missing" description={prepareModels ? 'Valid cached files in this folder will be reused.' : 'Preparation is deferred; missing models can be downloaded later.'} />
          </div>
        </Stack>
      )}

      {status?.state === 'never_configured' && busy !== 'install' && (
        <div className="neutralSetupNote" role="status">{status.detail}</div>
      )}

      {status?.state === 'broken' && busy !== 'install' && (
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

      {status?.state === 'ready' && (
        <div className="runtimeSummary">
          <Text fw={700}>Existing managed runtime is ready</Text>
          <Text size="sm">VidXP {status.package_version} · Capabilities: {status.capabilities.join(', ') || 'none'} · Interfaces: {status.surfaces.join(', ') || 'processing only'}</Text>
          <Text size="sm" className="pathText">Models: {displayPath(status.model_directory)}</Text>
        </div>
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
