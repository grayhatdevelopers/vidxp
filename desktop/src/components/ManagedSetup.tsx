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
import { useCallback, useEffect, useRef, useState } from 'react';

import {
  chooseModelDirectory,
  displayPath,
  errorMessage,
  installMediaRuntime,
  installRuntime,
  launchUi,
  modelDirectoryInventory,
  prepareManagedModels,
  runtimeManifest,
  runtimeStatus,
  type RuntimeManifest,
  type RuntimeStatus,
  type ModelDirectoryInventory,
  type TargetSetupState,
} from '../tauri';
import { useExclusiveOperation } from '../useAsyncAction';

interface ManagedSetupProps {
  draftId: string;
  selectedManagedRuntimeProfile: string | null;
  onBack: () => Promise<void>;
  onCommitted: (setup: TargetSetupState) => void;
}

type ManagedOperation = 'load' | 'folder' | 'reset' | 'install' | 'prepare' | 'launch';

export function ManagedSetup({ draftId, selectedManagedRuntimeProfile, onBack, onCommitted }: ManagedSetupProps) {
  const [manifest, setManifest] = useState<RuntimeManifest | null>(null);
  const [status, setStatus] = useState<RuntimeStatus | null>(null);
  const [capabilities, setCapabilities] = useState<string[]>([]);
  const [surfaces, setSurfaces] = useState<string[]>([]);
  const [prepareDuringInstall, setPrepareDuringInstall] = useState(true);
  const [modelDirectory, setModelDirectory] = useState('');
  const [inventory, setInventory] = useState<ModelDirectoryInventory | null>(null);
  const [operation, setOperation] = useState<ManagedOperation | null>('load');
  const [message, setMessage] = useState('Loading VidXP options…');
  const [failure, setFailure] = useState<string | null>(null);
  const operations = useExclusiveOperation<ManagedOperation>();
  const initialLoad = useRef<Promise<{
    manifest: RuntimeManifest;
    status: RuntimeStatus;
    inventory: ModelDirectoryInventory;
  }> | null>(null);

  const beginOperation = useCallback((kind: ManagedOperation): number | null => {
    const id = operations.begin(kind);
    if (id === null) return null;
    setOperation(kind);
    return id;
  }, [operations]);

  const settleOperation = useCallback((id: number) => {
    if (!operations.settle(id)) return;
    setOperation(null);
  }, [operations]);

  const load = useCallback(async () => {
    const operationId = beginOperation('load');
    if (operationId === null) return;
    setFailure(null);
    try {
      const request = initialLoad.current ?? Promise.all([runtimeManifest(), runtimeStatus()])
        .then(async ([nextManifest, nextStatus]) => ({
          manifest: nextManifest,
          status: nextStatus,
          inventory: await modelDirectoryInventory(nextStatus.model_directory),
        }));
      initialLoad.current = request;
      const { manifest: nextManifest, status: nextStatus, inventory: nextInventory } = await request;
      if (!operations.current(operationId)) return;
      setManifest(nextManifest);
      setStatus(nextStatus);
      const recoverable = Boolean(nextStatus.runtime_profile);
      setCapabilities(recoverable ? nextStatus.capabilities : Object.keys(nextManifest.capabilities));
      setSurfaces(
        recoverable
          ? nextStatus.surfaces
          : Object.entries(nextManifest.surfaces)
              .filter(([, surface]) => surface.default)
              .map(([id]) => id),
      );
      setModelDirectory(nextStatus.model_directory);
      setPrepareDuringInstall(!recoverable);
      setInventory(nextInventory);
      setMessage(nextStatus.ready ? 'VidXP is ready.' : nextStatus.detail);
    } catch (error) {
      if (operations.current(operationId)) {
        setFailure(errorMessage(error, 'Managed setup could not be loaded.'));
      }
    } finally {
      settleOperation(operationId);
    }
  }, [beginOperation, operations, settleOperation]);

  async function refreshInventory(directory: string): Promise<boolean> {
    try {
      setInventory(await modelDirectoryInventory(directory));
      return true;
    } catch (error) {
      setInventory(null);
      setFailure(errorMessage(error, 'The selected model folder could not be inventoried.'));
      return false;
    }
  }

  useEffect(() => {
    void load();
    return undefined;
  }, [load]);

  function toggleValue(value: string, checked: boolean, setter: (next: string[]) => void, current: string[]) {
    setter(checked ? [...current, value] : current.filter((item) => item !== value));
  }

  function toggleSurface(id: string, checked: boolean) {
    toggleValue(id, checked, setSurfaces, surfaces);
    if (id === 'worker' && checked && manifest) {
      setCapabilities(Object.keys(manifest.capabilities));
    }
  }

  function toggleCapability(id: string, checked: boolean) {
    toggleValue(id, checked, setCapabilities, capabilities);
    if (!checked && surfaces.includes('worker')) {
      setSurfaces(surfaces.filter((surface) => surface !== 'worker'));
    }
  }

  async function chooseFolder() {
    const operationId = beginOperation('folder');
    if (operationId === null) return;
    try {
      const selected = await chooseModelDirectory();
      if (selected) {
        setModelDirectory(selected);
        await refreshInventory(selected);
      }
    } catch (error) {
      setFailure(errorMessage(error, 'The model folder could not be selected.'));
    } finally {
      settleOperation(operationId);
    }
  }

  async function install() {
    if (capabilities.length === 0) {
      setFailure('Select at least one search feature.');
      return;
    }
    const operationId = beginOperation('install');
    if (operationId === null) return;
    const captured = {
      capabilities: [...capabilities],
      surfaces: [...surfaces],
      prepare_models: prepareDuringInstall,
      model_directory: modelDirectory || undefined,
      draft_id: draftId,
    };
    setFailure(null);
    try {
      setMessage('Checking FFmpeg and required codecs…');
      await installMediaRuntime(draftId);
      if (status?.state === 'broken' && status.runtime_profile && !dirty) {
        const repaired = await runtimeStatus();
        if (repaired.ready) {
          setStatus(repaired);
          setMessage('VidXP is ready.');
          return;
        }
      }
      setMessage(
        captured.prepare_models
          ? 'Installing VidXP and preparing the selected search features…'
          : 'Installing VidXP…',
      );
      const result = await installRuntime(captured);
      setMessage(result.install.prepared ? 'VidXP and the selected search features are ready.' : 'VidXP is installed. Search files can be downloaded later.');
      onCommitted(result.setup);
    } catch (error) {
      setFailure(errorMessage(error, 'Setup did not finish. Your previous VidXP installation is unchanged.'));
    } finally {
      settleOperation(operationId);
    }
  }

  async function launch() {
    const operationId = beginOperation('launch');
    if (operationId === null) return;
    setFailure(null);
    try {
      await launchUi();
    } catch (error) {
      setFailure(errorMessage(error, 'VidXP could not be opened.'));
    } finally {
      settleOperation(operationId);
    }
  }

  async function prepareModels() {
    const operationId = beginOperation('prepare');
    if (operationId === null) return;
    setFailure(null);
    setMessage('Verifying cached model files and downloading anything missing…');
    try {
      await prepareManagedModels(draftId);
      setMessage('Selected models are prepared.');
      const refreshed = await refreshInventory(modelDirectory);
      if (!refreshed) {
        setFailure('Models were prepared, but the cache inventory could not be refreshed.');
      }
    } catch (error) {
      setFailure(errorMessage(error, 'The search files could not be prepared. Your installed VidXP remains available.'));
    } finally {
      settleOperation(operationId);
    }
  }

  const sameValues = (left: string[], right: string[]) =>
    [...left].sort().join('\u0000') === [...right].sort().join('\u0000');
  const recoverableConfiguration = Boolean(status?.runtime_profile);
  const corruptPointer = status?.state === 'broken' && !recoverableConfiguration;
  const displayedRuntimeSelected = Boolean(
    status?.runtime_profile
    && status.runtime_profile === selectedManagedRuntimeProfile,
  );
  const dirty = recoverableConfiguration && (
    !sameValues(capabilities, status?.capabilities ?? [])
    || !sameValues(surfaces, status?.surfaces ?? [])
    || modelDirectory !== status?.model_directory
  );

  async function resetDraft() {
    if (!recoverableConfiguration || !status) return;
    const operationId = beginOperation('reset');
    if (operationId === null) return;
    setCapabilities(status.capabilities);
    setSurfaces(status.surfaces);
    setModelDirectory(status.model_directory);
    setInventory(null);
    setFailure(null);
    try {
      await refreshInventory(status.model_directory);
    } finally {
      settleOperation(operationId);
    }
  }

  const isBusy = operation !== null;
  const attentionTitle = /ffmpeg|ffprobe/i.test(message) ? 'Video tools need attention' : 'VidXP needs attention';

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
      <Button variant="subtle" leftSection={<IconArrowLeft aria-hidden="true" size={17} />} onClick={() => void onBack()} disabled={isBusy}>Back</Button>
      <div className="sectionHeading compactHeading">
        <Text className="eyebrow">SETUP OPTIONS</Text>
        <Title id="managed-setup-title" order={1} className="pageTitle">Choose your VidXP features</Title>
        <Text className="lede">Choose what VidXP can search, where video work runs, and how you want to open or connect to it. You can change these later.</Text>
      </div>

      {!manifest ? (
        <div className="emptyState" role="status" aria-live="polite"><Loader size="sm" /> Loading setup options…</div>
      ) : (
        <Stack gap="md">
          <div className="setupPanel">
            <Title order={2} className="panelTitle" mb="md">Search features</Title>
            <div className="optionGrid">
              {Object.entries(manifest.capabilities).map(([id, capability]) => (
                <Checkbox.Card
                  className="optionCard"
                  key={id}
                  checked={capabilities.includes(id)}
                  disabled={isBusy}
                  onClick={() => { if (!isBusy) toggleCapability(id, !capabilities.includes(id)); }}
                >
                  <Group wrap="nowrap" align="flex-start"><Checkbox.Indicator aria-hidden="true" /><div><Text fw={650}>{capability.label}</Text>{capability.description && <Text size="sm" className="mutedText">{capability.description}</Text>}</div></Group>
                </Checkbox.Card>
              ))}
            </div>
          </div>

          <div className="setupPanel">
            <Title order={2} className="panelTitle" mb="md">Video processing</Title>
            <Stack gap="xs">
              {Object.entries(manifest.surfaces).filter(([id]) => id === 'worker').map(([id, surface]) => (
                <Checkbox key={id} checked={surfaces.includes(id)} disabled={isBusy} onChange={(event) => toggleSurface(id, event.currentTarget.checked)} label={surface.label} description={surface.description} />
              ))}
            </Stack>
          </div>

          <div className="setupPanel">
            <Title order={2} className="panelTitle" mb="md">Interfaces and integrations</Title>
            <Stack gap="xs">
              {Object.entries(manifest.surfaces).filter(([id]) => id !== 'worker').map(([id, surface]) => (
                <Checkbox key={id} checked={surfaces.includes(id)} disabled={isBusy} onChange={(event) => toggleSurface(id, event.currentTarget.checked)} label={surface.label} description={surface.description} />
              ))}
            </Stack>
          </div>

          <div className="setupPanel">
            <Group justify="space-between" align="center" wrap="nowrap">
              <div className="folderCopy"><Text fw={650}>Downloaded model storage</Text><Text size="sm" className="mutedText">VidXP keeps the files needed by your selected search features here.</Text>{modelDirectory && <details className="technicalDetails"><summary>Storage location</summary><Text size="sm" className="pathText">{displayPath(modelDirectory)}</Text></details>}</div>
              <Button variant="default" leftSection={<IconFolderOpen aria-hidden="true" size={16} />} loading={operation === 'folder'} disabled={isBusy} onClick={() => void chooseFolder()}>Change location…</Button>
            </Group>
            <div className="cacheInventory" aria-live="polite">
              {operation === 'load' || operation === 'folder' || operation === 'reset' ? (
                <Text size="sm" mt="md"><Loader size="xs" /> Checking cached model files…</Text>
              ) : inventory && !inventory.readable ? (
                <Alert mt="md" color="red" title="Model folder cannot be read">{inventory.detail}</Alert>
              ) : inventory?.empty ? (
                <Text size="sm" mt="md">No cached models were found in this folder.</Text>
              ) : inventory ? (
                <>
                  {inventory.truncated ? (
                    <>
                      <Text fw={650} mt="md">At least {formatBytes(inventory.total_bytes)} across {inventory.file_count} cached files scanned <Text span fw={400} className="mutedText">(partial inventory)</Text></Text>
                      <Text size="sm" mt="xs">{inventory.detail}</Text>
                    </>
                  ) : (
                    <Text fw={650} mt="md">{formatBytes(inventory.total_bytes)} of cached model files found <Text span fw={400} className="mutedText">({inventory.file_count} files)</Text></Text>
                  )}
                  {inventory.recognized_models.length > 0 && (
                    <Group gap="xs" mt="xs">{inventory.recognized_models.map((model) => <Badge key={model.id} variant="light">{model.label}</Badge>)}</Group>
                  )}
                  <Text size="sm" mt="xs">VidXP will reuse files that are ready and download only what is missing.</Text>
                </>
              ) : null}
            </div>
            {!status?.ready && <Switch mt="lg" checked={prepareDuringInstall} disabled={isBusy} onChange={(event) => setPrepareDuringInstall(event.currentTarget.checked)} label="Verify cached models and download anything missing during setup" description={prepareDuringInstall ? 'Valid cached files in this folder will be reused.' : 'Preparation is deferred; missing models can be downloaded later.'} />}
          </div>
        </Stack>
      )}

      {status?.state === 'never_configured' && operation !== 'install' && (
        <div className="neutralSetupNote" role="status">{status.detail}</div>
      )}

      {status?.state === 'broken' && operation !== 'install' && (
        <Alert
          className="managedAttention"
          icon={<IconAlertCircle aria-hidden="true" />}
          color="yellow"
          title={corruptPointer ? 'VidXP could not read the saved setup' : attentionTitle}
          role="alert"
        >
          {corruptPointer
            ? 'Review the options above and rebuild VidXP. Your saved setup is not changed until the new one is ready.'
            : <><Text size="sm">Use the repair action below to check and restore this installation.</Text><details className="technicalDetails"><summary>Technical details</summary>{message}</details></>}
        </Alert>
      )}

      {status?.state === 'ready' && (
        <div className="runtimeSummary">
          <Text fw={700}>VidXP is installed</Text>
          <Text size="sm">Search: {status.capabilities.map((id) => manifest?.capabilities[id]?.label || id).join(', ') || 'none selected'}</Text>
          <Text size="sm">Processing and access: {status.surfaces.map((id) => manifest?.surfaces[id]?.label || id).join(', ') || 'command line only'}</Text>
          <details className="technicalDetails"><summary>Technical details</summary><Text size="xs">Version {status.package_version}</Text><Text size="xs" className="pathText">Models: {displayPath(status.model_directory)}</Text></details>
        </div>
      )}

      {recoverableConfiguration && (dirty || status?.state === 'broken') && <Alert color="yellow" title={dirty ? 'Your current setup stays available during the update' : 'Repair keeps your selected features'}>{dirty ? 'VidXP switches to the updated setup only after it has been installed and checked.' : 'VidXP first repairs the video tools, then restores this installation only if needed.'}</Alert>}
      {status?.ready && !displayedRuntimeSelected && <Alert color="yellow" title="This is not your active installation">Switch back to this installation before preparing models or opening VidXP.</Alert>}

      <div className="managedFooter">
        <div className="statusRegion" role="status" aria-live="polite" aria-atomic="true">{isBusy && <Loader size="xs" />}{(isBusy || status?.ready) && message}</div>
        {recoverableConfiguration ? (
          <Group>
            <Button variant="default" disabled={!dirty || isBusy} onClick={() => void resetDraft()}>Reset changes</Button>
            <Button loading={operation === 'install'} disabled={(!dirty && status?.state !== 'broken') || !manifest || isBusy} onClick={() => void install()}>{status?.state === 'broken' && !dirty ? 'Repair VidXP' : 'Apply update'}</Button>
            <Button variant="light" loading={operation === 'prepare'} disabled={!status?.ready || dirty || !displayedRuntimeSelected || isBusy} onClick={() => void prepareModels()}>Check downloaded models</Button>
            <Button leftSection={<IconExternalLink aria-hidden="true" size={17} />} loading={operation === 'launch'} disabled={!status?.ready || dirty || !displayedRuntimeSelected || isBusy} onClick={() => void launch()}>Open VidXP</Button>
          </Group>
        ) : (
          <Button loading={operation === 'install'} disabled={!manifest || isBusy} onClick={() => void install()}>{corruptPointer ? 'Rebuild VidXP' : 'Install VidXP'}</Button>
        )}
      </div>
      {failure && <Alert mt="md" icon={<IconAlertCircle aria-hidden="true" />} color="red" title="Could not continue" role="alert">{failure}</Alert>}
    </section>
  );
}
