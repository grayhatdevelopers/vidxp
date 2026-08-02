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
  const [message, setMessage] = useState('Loading managed runtime options…');
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
      setMessage(nextStatus.ready ? 'The managed runtime is ready.' : nextStatus.detail);
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
      setFailure('Select at least one capability.');
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
          setMessage('The managed runtime is ready.');
          return;
        }
      }
      setMessage(
        captured.prepare_models
          ? 'Creating the managed runtime, verifying cached models, and downloading anything missing…'
          : 'Creating the managed runtime…',
      );
      const result = await installRuntime(captured);
      setMessage(result.install.prepared ? 'Runtime and selected models are ready.' : 'Runtime ready. Model downloads were deferred.');
      onCommitted(result.setup);
    } catch (error) {
      setFailure(errorMessage(error, 'Managed setup failed. The previous target remains authoritative; any completed replacement runtime is retained for recovery.'));
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
      setFailure(errorMessage(error, 'Model preparation failed. The installed runtime remains active.'));
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
      <Button variant="subtle" leftSection={<IconArrowLeft aria-hidden="true" size={17} />} onClick={() => void onBack()} disabled={isBusy}>Back</Button>
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
                  disabled={isBusy}
                  onClick={() => { if (!isBusy) toggleValue(id, !capabilities.includes(id), setCapabilities, capabilities); }}
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
                <Checkbox key={id} checked={surfaces.includes(id)} disabled={isBusy} onChange={(event) => toggleValue(id, event.currentTarget.checked, setSurfaces, surfaces)} label={surface.label} description={surface.description} />
              ))}
            </Stack>
          </div>

          <div className="setupPanel">
            <Group justify="space-between" align="center" wrap="nowrap">
              <div className="folderCopy"><Text fw={650}>Model storage</Text><Text size="sm" className="pathText">{modelDirectory ? displayPath(modelDirectory) : 'Using the default location'}</Text></div>
              <Button variant="default" leftSection={<IconFolderOpen aria-hidden="true" size={16} />} loading={operation === 'folder'} disabled={isBusy} onClick={() => void chooseFolder()}>Choose folder…</Button>
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
                  <Text size="sm" mt="xs">Cached files detected; verification required. VidXP will reuse valid cached files and download only missing material.</Text>
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
          title={corruptPointer ? 'Managed runtime configuration is unreadable' : attentionTitle}
          role="alert"
        >
          {corruptPointer
            ? `${message} Choose a new managed configuration below. The unreadable pointer remains unchanged until the replacement is fully installed, validated, and activated.`
            : message}
        </Alert>
      )}

      {status?.state === 'ready' && (
        <div className="runtimeSummary">
          <Text fw={700}>Installed managed runtime</Text>
          <Text size="sm">VidXP {status.package_version} · Capabilities: {status.capabilities.join(', ') || 'none'} · Interfaces: {status.surfaces.join(', ') || 'processing only'}</Text>
          <Text size="sm" className="pathText">Models: {displayPath(status.model_directory)}</Text>
        </div>
      )}

      {recoverableConfiguration && (dirty || status?.state === 'broken') && <Alert color="yellow" title={dirty ? 'Update creates a replacement runtime' : 'Repair keeps the current configuration'}>{dirty ? 'The installed runtime remains active while Desktop creates and validates this draft. It is replaced only after activation succeeds.' : 'Desktop will repair media tools first. It replaces the managed runtime only if the installed runtime is still damaged, preserving its capabilities, browser surface, and model folder.'}</Alert>}
      {corruptPointer && <Alert color="yellow" title="Replacement becomes authoritative only after activation">Desktop cannot recover settings from the unreadable pointer. Review the default capabilities, browser surface, and model folder above before configuring a replacement.</Alert>}
      {status?.ready && !displayedRuntimeSelected && <Alert color="yellow" title="Select this managed target to use it">Prepare and Open are unavailable because another target is currently selected. Return to Manage targets and select this managed runtime first.</Alert>}

      <div className="managedFooter">
        <div className="statusRegion" role="status" aria-live="polite" aria-atomic="true">{isBusy && <Loader size="xs" />}{(isBusy || status?.ready) && message}</div>
        {recoverableConfiguration ? (
          <Group>
            <Button variant="default" disabled={!dirty || isBusy} onClick={() => void resetDraft()}>Reset changes</Button>
            <Button loading={operation === 'install'} disabled={(!dirty && status?.state !== 'broken') || !manifest || isBusy} onClick={() => void install()}>{status?.state === 'broken' && !dirty ? 'Repair VidXP' : 'Apply update'}</Button>
            <Button variant="light" loading={operation === 'prepare'} disabled={!status?.ready || dirty || !displayedRuntimeSelected || isBusy} onClick={() => void prepareModels()}>Prepare / verify models</Button>
            <Button leftSection={<IconExternalLink aria-hidden="true" size={17} />} loading={operation === 'launch'} disabled={!status?.ready || dirty || !displayedRuntimeSelected || isBusy} onClick={() => void launch()}>Open VidXP</Button>
          </Group>
        ) : (
          <Button loading={operation === 'install'} disabled={!manifest || isBusy} onClick={() => void install()}>{corruptPointer ? 'Configure replacement' : 'Configure VidXP'}</Button>
        )}
      </div>
      {failure && <Alert mt="md" icon={<IconAlertCircle aria-hidden="true" />} color="red" title="Could not continue" role="alert">{failure}</Alert>}
    </section>
  );
}
