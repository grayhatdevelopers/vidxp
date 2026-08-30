import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  Loader,
  Modal,
  Progress,
  Stack,
  Switch,
  Text,
  Title,
} from '@mantine/core';
import { IconAlertCircle, IconArrowLeft, IconExternalLink, IconFolderOpen } from '@tabler/icons-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import {
  chooseModelDirectory,
  cancelManagedSetupOperation,
  displayPath,
  errorMessage,
  installPremiereExtensions,
  installMediaRuntime,
  installRuntime,
  launchUi,
  localServerStatus,
  modelDirectoryInventory,
  onManagedSetupProgress,
  premiereIntegrationState,
  prepareManagedModels,
  runtimeManifest,
  runtimeStatus,
  startLocalServer,
  type PremiereIntegrationState,
  type RuntimeManifest,
  type RuntimeStatus,
  type ModelDirectoryInventory,
  type ManagedSetupProgress,
  type TargetSetupState,
} from '../tauri';
import { useExclusiveOperation } from '../useAsyncAction';

interface ManagedSetupProps {
  draftId: string;
  selectedManagedRuntimeProfile: string | null;
  premiereRequested: boolean;
  onBack: () => Promise<void>;
  onCommitted: (setup: TargetSetupState, notice?: { color: 'teal' | 'yellow'; title: string; detail: string }) => void;
}

type ManagedOperation = 'load' | 'folder' | 'reset' | 'install' | 'prepare' | 'launch';

export function ManagedSetup({ draftId, selectedManagedRuntimeProfile, premiereRequested, onBack, onCommitted }: ManagedSetupProps) {
  const [manifest, setManifest] = useState<RuntimeManifest | null>(null);
  const [status, setStatus] = useState<RuntimeStatus | null>(null);
  const [capabilities, setCapabilities] = useState<string[]>([]);
  const [surfaces, setSurfaces] = useState<string[]>([]);
  const [premiere, setPremiere] = useState<PremiereIntegrationState | null>(null);
  const [premiereEnabled, setPremiereEnabled] = useState(premiereRequested);
  const [prepareDuringInstall, setPrepareDuringInstall] = useState(true);
  const [localAnswers, setLocalAnswers] = useState(false);
  const [modelDirectory, setModelDirectory] = useState('');
  const [inventory, setInventory] = useState<ModelDirectoryInventory | null>(null);
  const [operation, setOperation] = useState<ManagedOperation | null>('load');
  const [message, setMessage] = useState('Loading VidXP options…');
  const [failure, setFailure] = useState<string | null>(null);
  const [installFailure, setInstallFailure] = useState<string | null>(null);
  const [cancelFailure, setCancelFailure] = useState<string | null>(null);
  const [cancelRequested, setCancelRequested] = useState(false);
  const [setupProgress, setSetupProgress] = useState<ManagedSetupProgress | null>(null);
  const [setupElapsed, setSetupElapsed] = useState(0);
  const operations = useExclusiveOperation<ManagedOperation>();
  const failureAlert = useRef<HTMLDivElement | null>(null);
  const cancelRequestedRef = useRef(false);
  const initialLoad = useRef<Promise<{
    manifest: RuntimeManifest;
    status: RuntimeStatus;
    inventory: ModelDirectoryInventory;
    premiere: PremiereIntegrationState;
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
      const request = initialLoad.current ?? Promise.all([runtimeManifest(), runtimeStatus(), premiereIntegrationState()])
        .then(async ([nextManifest, nextStatus, nextPremiere]) => ({
          manifest: nextManifest,
          status: nextStatus,
          inventory: await modelDirectoryInventory(nextStatus.model_directory),
          premiere: nextPremiere,
        }));
      initialLoad.current = request;
      const { manifest: nextManifest, status: nextStatus, inventory: nextInventory, premiere: nextPremiere } = await request;
      if (!operations.current(operationId)) return;
      setManifest(nextManifest);
      setStatus(nextStatus);
      setPremiere(nextPremiere);
      const recoverable = Boolean(nextStatus.runtime_profile);
      const shouldEnablePremiere = premiereRequested || Boolean(nextPremiere.cep_installed || nextPremiere.uxp_installed);
      setCapabilities(recoverable ? nextStatus.capabilities : Object.keys(nextManifest.capabilities));
      const nextSurfaces = (
        recoverable
          ? nextStatus.surfaces
          : Object.entries(nextManifest.surfaces)
              .filter(([, surface]) => surface.default)
              .map(([id]) => id)
      );
      setSurfaces(shouldEnablePremiere ? [...new Set([...nextSurfaces, 'worker', 'server'])] : nextSurfaces);
      setPremiereEnabled(shouldEnablePremiere);
      setModelDirectory(nextStatus.model_directory);
      setLocalAnswers(recoverable ? Boolean(nextStatus.local_answers) : false);
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
  }, [beginOperation, operations, premiereRequested, settleOperation]);

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

  useEffect(() => {
    let active = true;
    let stop: (() => void) | undefined;
    void onManagedSetupProgress((progress) => {
      if (active && progress.draft_id === draftId) setSetupProgress(progress);
    }).then((unlisten) => {
      if (active) stop = unlisten;
      else unlisten();
    });
    return () => {
      active = false;
      stop?.();
    };
  }, [draftId]);

  useEffect(() => {
    if (operation !== 'install') {
      setSetupElapsed(0);
      return undefined;
    }
    const started = Date.now();
    const timer = window.setInterval(() => setSetupElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [operation]);

  useEffect(() => {
    if (!failure || installFailure) return;
    failureAlert.current?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
    failureAlert.current?.focus({ preventScroll: true });
  }, [failure, installFailure]);

  function toggleValue(value: string, checked: boolean, setter: (next: string[]) => void, current: string[]) {
    setter(checked ? [...current, value] : current.filter((item) => item !== value));
  }

  function toggleSurface(id: string, checked: boolean) {
    toggleValue(id, checked, setSurfaces, surfaces);
    if (id === 'worker' && checked && manifest) {
      setCapabilities(Object.keys(manifest.capabilities));
    }
    if (!checked && (id === 'worker' || id === 'server')) setPremiereEnabled(false);
  }

  function togglePremiere(checked: boolean) {
    setPremiereEnabled(checked);
    if (!checked) return;
    setSurfaces([...new Set([...surfaces, 'worker', 'server'])]);
    if (manifest) setCapabilities(Object.keys(manifest.capabilities));
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
      surfaces: premiereEnabled ? [...new Set([...surfaces, 'worker', 'server'])] : [...surfaces],
      premiere: premiereEnabled,
      prepare_models: prepareDuringInstall,
      local_answers: localAnswers,
      model_directory: modelDirectory || undefined,
      draft_id: draftId,
    };
    setFailure(null);
    setInstallFailure(null);
    setCancelFailure(null);
    setCancelRequested(false);
    cancelRequestedRef.current = false;
    setSetupProgress({
      draft_id: draftId,
      current: 1,
      total: (captured.prepare_models ? 8 : 7) + (captured.local_answers ? 1 : 0),
      stage: 'video-tools',
      message: 'Checking FFmpeg and required video codecs',
    });
    try {
      setMessage('Checking FFmpeg and required codecs…');
      await installMediaRuntime(draftId, (captured.prepare_models ? 8 : 7) + (captured.local_answers ? 1 : 0));
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
      let premiereNotice: { color: 'teal' | 'yellow'; title: string; detail: string } | undefined;
      if (captured.premiere) {
        setMessage('Starting VidXP privately and installing the Premiere extension…');
        try {
          const server = await localServerStatus();
          if (!server.running) await startLocalServer();
          const extension = await installPremiereExtensions();
          premiereNotice = {
            color: extension.opened_packages.length > 0 ? 'yellow' : 'teal',
            title: extension.opened_packages.length > 0 ? 'Finish Premiere setup in Creative Cloud' : 'VidXP and Premiere are ready',
            detail: extension.detail,
          };
        } catch (error) {
          premiereNotice = {
            color: 'yellow',
            title: 'VidXP is ready; Premiere setup needs attention',
            detail: errorMessage(error, 'The Premiere extension could not be installed. Use Install for Premiere from the VidXP summary screen to retry.'),
          };
        }
      }
      setMessage(result.install.prepared ? 'VidXP and the selected search features are ready.' : 'VidXP is installed. Search files can be downloaded later.');
      onCommitted(result.setup, premiereNotice);
    } catch (error) {
      const detail = cancelRequestedRef.current
        ? 'Setup was cancelled. Your previous VidXP installation is unchanged.'
        : errorMessage(error, 'Setup did not finish. Your previous VidXP installation is unchanged.');
      setFailure(detail);
      setInstallFailure(detail);
    } finally {
      cancelRequestedRef.current = false;
      setCancelRequested(false);
      settleOperation(operationId);
      setSetupProgress(null);
    }
  }

  async function cancelInstall() {
    if (cancelRequestedRef.current) return;
    cancelRequestedRef.current = true;
    setCancelRequested(true);
    setCancelFailure(null);
    setSetupProgress((current) => ({
      draft_id: draftId,
      current: current?.current ?? 1,
      total: current?.total ?? (prepareDuringInstall ? 8 : 7) + (localAnswers ? 1 : 0),
      stage: current?.stage ?? 'cancelling',
      message: 'Stopping setup safely',
      model_message: current?.model_message,
      model_current: current?.model_current,
      model_total: current?.model_total,
    }));
    try {
      await cancelManagedSetupOperation(draftId);
    } catch (error) {
      cancelRequestedRef.current = false;
      setCancelRequested(false);
      setCancelFailure(errorMessage(error, 'Setup could not be stopped.'));
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
    || localAnswers !== Boolean(status?.local_answers)
  );
  const premiereNeedsInstall = premiereEnabled && !premiere?.cep_installed && !premiere?.uxp_installed;

  async function resetDraft() {
    if (!recoverableConfiguration || !status) return;
    const operationId = beginOperation('reset');
    if (operationId === null) return;
    setCapabilities(status.capabilities);
    const keepPremiere = premiereRequested || Boolean(premiere?.cep_installed || premiere?.uxp_installed);
    setPremiereEnabled(keepPremiere);
    setSurfaces(keepPremiere ? [...new Set([...status.surfaces, 'worker', 'server'])] : status.surfaces);
    setModelDirectory(status.model_directory);
    setLocalAnswers(Boolean(status.local_answers));
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
  const progressCurrent = setupProgress?.current ?? 1;
  const progressTotal = setupProgress?.total ?? (prepareDuringInstall ? 8 : 7) + (localAnswers ? 1 : 0);
  const selectedModelDownloads = new Map<string, number>();
  for (const capability of capabilities) {
    for (const model of manifest?.capabilities[capability]?.models ?? []) {
      selectedModelDownloads.set(
        model.cache_key,
        Math.max(selectedModelDownloads.get(model.cache_key) ?? 0, model.download_size_bytes),
      );
    }
  }
  const selectedModelBytes = [...selectedModelDownloads.values()].reduce((total, bytes) => total + bytes, 0);
  const managedRuntimeBytes = manifest?.managed_runtime_estimated_size_bytes ?? 0;
  const localAnswerSpec = manifest?.local_answers ?? {
    engine: 'ollama',
    model: 'qwen3.5:4b-q4_K_M',
    download_size_bytes: 3650722202,
    label: 'Local grounded answers',
    description: 'Turn VidXP search evidence into cited answers on this computer.',
  };
  const localAnswerModelBytes = localAnswers ? localAnswerSpec.download_size_bytes : 0;
  const plannedSetupBytes = managedRuntimeBytes + selectedModelBytes + localAnswerModelBytes;
  const capabilityModelSummary = capabilities
    .map((id) => {
      const capability = manifest?.capabilities[id];
      if (!capability) return null;
      const models = new Map<string, number>();
      for (const model of capability.models ?? []) models.set(model.cache_key, model.download_size_bytes);
      const bytes = [...models.values()].reduce((total, size) => total + size, 0);
      return `${capability.label}: ${formatBytes(bytes)}`;
    })
    .filter((summary): summary is string => summary !== null)
    .join(' · ');

  function dismissInstallFailure() {
    setInstallFailure(null);
    setFailure(null);
  }

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

      {failure && !installFailure && <div ref={failureAlert} tabIndex={-1}><Alert mb="md" icon={<IconAlertCircle aria-hidden="true" />} color="red" title="Could not continue" role="alert">{failure}</Alert></div>}

      {status?.state === 'broken' && operation !== 'install' && (
        <>
          <Alert
            className="managedAttention"
            icon={<IconAlertCircle aria-hidden="true" />}
            color="yellow"
            title={corruptPointer ? 'VidXP could not read the saved setup' : attentionTitle}
            role="alert"
          >
            {corruptPointer
              ? <Text size="sm">Review the options below and rebuild VidXP. Your saved setup is not changed until the new one is ready.</Text>
              : <><Text size="sm">Repair this Desktop-managed installation now, or review its saved features below first.</Text><details className="technicalDetails"><summary>Technical details</summary>{message}</details></>}
            <Button mt="md" disabled={!manifest || isBusy} onClick={() => void install()}>
              {corruptPointer ? 'Rebuild now' : dirty ? 'Apply update now' : 'Repair now'}
            </Button>
          </Alert>
          <Alert color="yellow" title={dirty ? 'Your current setup stays available during the update' : 'Repair keeps your selected features'}>
            {dirty ? 'VidXP switches to the updated setup only after it has been installed and checked.' : 'VidXP first repairs the video tools, then restores this installation only if needed.'}
          </Alert>
        </>
      )}

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
            <Title order={2} className="panelTitle" mb="md">Grounded answers</Title>
            <Checkbox
              checked={localAnswers}
              disabled={isBusy}
              onChange={(event) => setLocalAnswers(event.currentTarget.checked)}
              label={localAnswerSpec.label}
              description={`${localAnswerSpec.description} Model download: ${formatBytes(localAnswerSpec.download_size_bytes)}.`}
            />
            {localAnswers && (
              <Alert mt="md" color="blue" title="VidXP manages the connection">
                VidXP checks for Ollama, asks before installing it, starts only a VidXP-owned service when needed, and configures the browser, API, worker, Premiere, and MCP surfaces automatically. There is no URL to enter.
              </Alert>
            )}
          </div>

          <div className="setupPanel">
            <Title order={2} className="panelTitle" mb="md">Interfaces and integrations</Title>
            <Stack gap="xs">
              {premiere?.platform_supported && (
                <Checkbox
                  checked={premiereEnabled}
                  disabled={isBusy || !premiere.cep_package_available || !premiere.uxp_package_available}
                  onChange={(event) => togglePremiere(event.currentTarget.checked)}
                  label="Premiere Pro extension"
                  description="Install the matching Adobe extension. VidXP includes local processing and its private connection automatically."
                />
              )}
              {Object.entries(manifest.surfaces).filter(([id]) => id !== 'worker').map(([id, surface]) => (
                <Checkbox key={id} checked={surfaces.includes(id)} disabled={isBusy} onChange={(event) => toggleSurface(id, event.currentTarget.checked)} label={surface.label} description={surface.description} />
              ))}
            </Stack>
          </div>

          <div className="setupPanel">
            <Group justify="space-between" align="center" wrap="nowrap">
              <div className="folderCopy"><Text fw={650}>Downloaded model storage</Text><Text size="sm" className="mutedText">VidXP keeps search models here. A reused external Ollama service continues to own its existing model store.</Text>{modelDirectory && <details className="technicalDetails"><summary>Storage location</summary><Text size="sm" className="pathText">{displayPath(modelDirectory)}</Text></details>}</div>
              <Button variant="default" leftSection={<IconFolderOpen aria-hidden="true" size={16} />} loading={operation === 'folder'} disabled={isBusy} onClick={() => void chooseFolder()}>Change location…</Button>
            </Group>
            <Alert mt="md" color="blue" title="Plan for local storage">
              <Text size="sm">The managed runtime can use approximately {formatBytes(managedRuntimeBytes)}. Selected model downloads total up to {formatBytes(selectedModelBytes)}.{localAnswers ? ` The grounded-answer model adds ${formatBytes(localAnswerModelBytes)}.` : ''}</Text>
              <Text size="sm" mt="xs">{capabilityModelSummary}</Text>
              <Text size="sm" mt="xs">Plan for approximately {formatBytes(plannedSetupBytes)} locally, plus temporary installation space, indexes, and videos. Valid cached model files are reused.</Text>
            </Alert>
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

      {status?.state === 'ready' && (
        <div className="runtimeSummary">
          <Text fw={700}>VidXP is installed</Text>
          <Text size="sm">Search: {status.capabilities.map((id) => manifest?.capabilities[id]?.label || id).join(', ') || 'none selected'}</Text>
          <Text size="sm">Processing and access: {status.surfaces.map((id) => manifest?.surfaces[id]?.label || id).join(', ') || 'command line only'}</Text>
          <Text size="sm">Grounded answers: {status.local_answers ? localAnswerSpec.model : 'not installed'}</Text>
          <details className="technicalDetails"><summary>Technical details</summary><Text size="xs">Version {status.package_version}</Text><Text size="xs" className="pathText">Models: {displayPath(status.model_directory)}</Text></details>
        </div>
      )}

      {recoverableConfiguration && dirty && status?.state !== 'broken' && <Alert color="yellow" title="Your current setup stays available during the update">VidXP switches to the updated setup only after it has been installed and checked.</Alert>}
      {status?.ready && !displayedRuntimeSelected && <Alert color="yellow" title="This is not your active installation">Switch back to this installation before preparing models or opening VidXP.</Alert>}

      <div className="managedFooter">
        <div className="statusRegion" role="status" aria-live="polite" aria-atomic="true">{isBusy && operation !== 'install' && <Loader size="xs" />}{(isBusy || status?.ready) && message}</div>
        {recoverableConfiguration ? (
          <Group>
            <Button variant="default" disabled={!dirty || isBusy} onClick={() => void resetDraft()}>Reset changes</Button>
            <Button disabled={(!dirty && status?.state !== 'broken' && !premiereRequested && !premiereNeedsInstall) || !manifest || isBusy} onClick={() => void install()}>{premiereEnabled ? 'Apply and install Premiere' : status?.state === 'broken' && !dirty ? 'Repair VidXP' : 'Apply update'}</Button>
            <Button variant="light" loading={operation === 'prepare'} disabled={!status?.ready || dirty || !displayedRuntimeSelected || isBusy} onClick={() => void prepareModels()}>Check downloaded models</Button>
            <Button leftSection={<IconExternalLink aria-hidden="true" size={17} />} loading={operation === 'launch'} disabled={!status?.ready || dirty || !displayedRuntimeSelected || isBusy} onClick={() => void launch()}>Open VidXP</Button>
          </Group>
        ) : (
          <Button disabled={!manifest || isBusy} onClick={() => void install()}>{premiereEnabled ? 'Install VidXP and Premiere' : corruptPointer ? 'Rebuild VidXP' : 'Install VidXP'}</Button>
        )}
      </div>
      <Modal
        opened={operation === 'install' || installFailure !== null}
        onClose={() => { if (operation !== 'install') dismissInstallFailure(); }}
        title={installFailure ? 'Setup could not finish' : 'Setting up VidXP'}
        size="md"
        closeOnClickOutside={operation !== 'install'}
        closeOnEscape={operation !== 'install'}
        withCloseButton={operation !== 'install'}
      >
        {installFailure ? (
          <Stack gap="md">
            <Alert icon={<IconAlertCircle aria-hidden="true" />} color="red" title="VidXP was not installed" role="alert">{installFailure}</Alert>
            <Text size="sm">Any model files already downloaded remain cached and will be reused when you retry.</Text>
            <Group justify="flex-end"><Button onClick={dismissInstallFailure}>Review setup</Button></Group>
          </Stack>
        ) : (
          <Stack gap="md" role="status" aria-live="polite" aria-atomic="true">
            <Group justify="space-between" align="baseline">
              <Text fw={700}>Step {progressCurrent} of {progressTotal}</Text>
              <Text size="sm" className="mutedText">{setupElapsed}s elapsed</Text>
            </Group>
            <Progress value={(progressCurrent / progressTotal) * 100} size="lg" animated />
            {(setupProgress?.stage === 'models' || setupProgress?.stage === 'local-answers')
              && setupProgress.model_message && (
                <Stack gap={6}>
                  <Group justify="space-between" align="baseline" wrap="nowrap">
                    <Text size="sm" fw={650}>{setupProgress.model_message}</Text>
                    {setupProgress.model_current != null && setupProgress.model_total != null
                      ? <Text size="xs" className="mutedText" style={{ whiteSpace: 'nowrap' }}>
                          {formatBytes(setupProgress.model_current)} of {formatBytes(setupProgress.model_total)}
                        </Text>
                      : <Loader size="xs" />}
                  </Group>
                  {setupProgress.model_current != null && setupProgress.model_total != null && (
                    <Progress
                      aria-label="Current model download progress"
                      value={(setupProgress.model_current / setupProgress.model_total) * 100}
                      size="md"
                      animated
                    />
                  )}
                </Stack>
              )}
            <div>
              <Text fw={650}>{setupProgress?.message ?? 'Starting managed setup'}</Text>
              <Text size="sm" className="mutedText" mt="xs">The existing installation remains active until every step has completed and the replacement passes validation.</Text>
            </div>
            {cancelFailure && <Alert color="red" title="Could not stop setup" role="alert">{cancelFailure}</Alert>}
            <Group justify="flex-end">
              <Button variant="default" loading={cancelRequested} disabled={cancelRequested} onClick={() => void cancelInstall()}>
                {cancelRequested ? 'Stopping…' : 'Cancel setup'}
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>
    </section>
  );
}
