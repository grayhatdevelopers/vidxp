import { Alert, Badge, Button, Checkbox, Code, Group, Loader, Modal, Stack, Text, Title } from '@mantine/core';
import { IconActivityHeartbeat, IconCopy, IconExternalLink, IconPlugConnected, IconPlayerPlay, IconPlayerStop, IconRefresh, IconSettings, IconShare, IconTerminal2 } from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';

import {
  errorMessage,
  browserServiceStatus,
  configureExternalInstallation,
  installCodexPlugin,
  localServerStatus,
  localWorkerStatus,
  mcpClientConfig,
  runtimeManifest,
  startLocalServer,
  startSharedBrowser,
  startSharedServer,
  startLocalWorker,
  stopLocalServer,
  stopBrowserService,
  stopLocalWorker,
  targetDoctor,
  type DoctorReport,
  type BrowserServiceStatus,
  type CodexPluginInstallResult,
  type LocalServerStatus,
  type LocalWorkerStatus,
  type RuntimeManifest,
  type TargetError,
  type TargetProfile,
  type TargetSetupState,
} from '../tauri';

interface TargetSummaryProps {
  profile: TargetProfile;
  validationError?: TargetError | null;
  checking?: boolean;
  operationPending?: boolean;
  opening?: boolean;
  onRecheck: () => Promise<void>;
  onManageManaged: () => void;
  onSetupChanged: (setup: TargetSetupState) => void;
  onChooseAnother: () => void;
  onOpen: () => Promise<void>;
}

const CAPABILITY_LABELS: Record<string, string> = {
  actor: 'Actor recognition',
  dialogue: 'Dialogue search',
  media: 'Video tools',
  scene: 'Visual scene search',
  videoprism: 'Temporal video search',
};

interface WorkerFailure {
  title: string;
  detail: string;
}

export function TargetSummary({ profile, validationError, checking, operationPending, opening, onRecheck, onManageManaged, onSetupChanged, onChooseAnother, onOpen }: TargetSummaryProps) {
  const executable = profile.display_executable;
  const [doctor, setDoctor] = useState<DoctorReport | null>(null);
  const [server, setServer] = useState<LocalServerStatus | null>(null);
  const [browser, setBrowser] = useState<BrowserServiceStatus | null>(null);
  const [worker, setWorker] = useState<LocalWorkerStatus | null>(null);
  const [mcpConfig, setMcpConfig] = useState<string | null>(null);
  const [codexSetup, setCodexSetup] = useState<CodexPluginInstallResult | null>(null);
  const [busy, setBusy] = useState<'doctor' | 'config' | 'codex' | 'features' | 'worker-start' | 'worker-stop' | 'browser-share' | 'browser-stop' | 'server-start' | 'server-share' | 'server-stop' | null>(null);
  const [runtimeFailure, setRuntimeFailure] = useState<string | null>(null);
  const [workerFailure, setWorkerFailure] = useState<WorkerFailure | null>(null);
  const workerStatusRequest = useRef(0);
  const workerActionActive = useRef(false);
  const [copied, setCopied] = useState(false);
  const [shareCopied, setShareCopied] = useState(false);
  const [externalSetupOpened, setExternalSetupOpened] = useState(false);
  const [externalManifest, setExternalManifest] = useState<RuntimeManifest | null>(null);
  const [externalCapabilities, setExternalCapabilities] = useState<string[]>([]);
  const [externalSurfaces, setExternalSurfaces] = useState<string[]>([]);
  const [externalFailure, setExternalFailure] = useState<string | null>(null);
  const [externalTechnical, setExternalTechnical] = useState<string | null>(null);
  const [readinessOpened, setReadinessOpened] = useState(false);
  const [readinessElapsed, setReadinessElapsed] = useState(0);
  const needsRuntimeUpdate = validationError?.code === 'runtime_update_required';
  const runtimeCompatible = !validationError;
  const desktopSurfaceUnavailable = !runtimeCompatible || !profile.frontend.launchable;
  const browserAvailable = runtimeCompatible && profile.surfaces.includes('browser');
  const workerAvailable = runtimeCompatible && profile.surfaces.includes('worker');
  const mcpAvailable = runtimeCompatible && (profile.surfaces.includes('mcp') || profile.surfaces.includes('server'));
  const serverAvailable = runtimeCompatible && profile.surfaces.includes('server');
  const failedChecks = doctor?.checks.filter((check) => !check.ok) ?? [];

  const capabilityLabel = (capability: string) => CAPABILITY_LABELS[capability] ?? capability;

  useEffect(() => {
    setDoctor(null);
    setMcpConfig(null);
    setCodexSetup(null);
    setRuntimeFailure(null);
    if (!serverAvailable) {
      setServer(null);
      return;
    }
    let active = true;
    let timer: ReturnType<typeof setInterval> | null = null;
    const poll = async () => {
      try {
        const status = await localServerStatus();
        if (!active) return;
        setServer(status);
      } catch (error) {
        if (active) setRuntimeFailure(errorMessage(error, 'The local service status could not be checked.'));
      }
    };
    void poll();
    timer = setInterval(() => void poll(), 5000);
    return () => {
      active = false;
      if (timer) clearInterval(timer);
    };
  }, [profile.id, serverAvailable]);

  useEffect(() => {
    if (!browserAvailable) {
      setBrowser(null);
      return;
    }
    let active = true;
    const poll = async () => {
      try {
        const status = await browserServiceStatus();
        if (active) setBrowser(status);
      } catch (error) {
        if (active) setRuntimeFailure(errorMessage(error, 'The browser sharing status could not be checked.'));
      }
    };
    void poll();
    const timer = setInterval(() => void poll(), 5000);
    return () => { active = false; clearInterval(timer); };
  }, [profile.id, browserAvailable]);

  useEffect(() => {
    if (!workerAvailable) {
      setWorker(null);
      setWorkerFailure(null);
      return;
    }
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      if (workerActionActive.current) {
        if (active) timer = setTimeout(() => void poll(), 5000);
        return;
      }
      const request = ++workerStatusRequest.current;
      try {
        const status = await localWorkerStatus();
        if (active && request === workerStatusRequest.current) {
          setWorker(status);
          setWorkerFailure(null);
        }
      } catch (error) {
        if (active && request === workerStatusRequest.current) {
          setWorkerFailure({
            title: 'Local processing status could not be checked',
            detail: errorMessage(error, 'VidXP could not check local video processing.'),
          });
        }
      } finally {
        if (active) timer = setTimeout(() => void poll(), 5000);
      }
    };
    void poll();
    return () => {
      active = false;
      workerStatusRequest.current += 1;
      if (timer) clearTimeout(timer);
    };
  }, [profile.id, workerAvailable]);

  useEffect(() => {
    if (busy !== 'doctor') return;
    setReadinessElapsed(0);
    const started = Date.now();
    const timer = setInterval(() => setReadinessElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [busy]);

  async function runDoctor() {
    setBusy('doctor');
    setRuntimeFailure(null);
    setDoctor(null);
    setReadinessOpened(true);
    try {
      setDoctor(await targetDoctor());
    } catch (error) {
      setRuntimeFailure(errorMessage(error, 'VidXP could not check whether this setup is ready.'));
    } finally {
      setBusy(null);
    }
  }

  async function loadMcpConfig() {
    setBusy('config');
    setRuntimeFailure(null);
    setCopied(false);
    try {
      setMcpConfig(await mcpClientConfig());
    } catch (error) {
      setRuntimeFailure(errorMessage(error, 'VidXP could not create the AI client setup.'));
    } finally {
      setBusy(null);
    }
  }

  async function setupCodex() {
    setBusy('codex');
    setRuntimeFailure(null);
    setCodexSetup(null);
    try {
      setCodexSetup(await installCodexPlugin());
    } catch (error) {
      setRuntimeFailure(errorMessage(error, 'VidXP could not install the Codex plugin.'));
    } finally {
      setBusy(null);
    }
  }

  async function openExternalSetup() {
    setBusy('features');
    setRuntimeFailure(null);
    setExternalFailure(null);
    setExternalTechnical(null);
    try {
      const manifest = await runtimeManifest();
      setExternalManifest(manifest);
      if (needsRuntimeUpdate) {
        const defaultSurfaces = Object.entries(manifest.surfaces).filter(([, surface]) => surface.default).map(([id]) => id);
        setExternalSurfaces(defaultSurfaces);
        setExternalCapabilities(defaultSurfaces.includes('worker') ? Object.keys(manifest.capabilities) : []);
      } else {
        setExternalCapabilities(profile.capabilities.filter((capability) => capability in manifest.capabilities));
        setExternalSurfaces([...profile.surfaces]);
      }
      setExternalSetupOpened(true);
    } catch (error) {
      setRuntimeFailure(errorMessage(error, 'VidXP could not load the available setup options.'));
    } finally {
      setBusy(null);
    }
  }

  async function applyExternalFeatures() {
    setBusy('features');
    setExternalFailure(null);
    setExternalTechnical(null);
    try {
      const setup = await configureExternalInstallation(externalCapabilities, externalSurfaces);
      onSetupChanged(setup);
      setExternalSetupOpened(false);
    } catch (error) {
      const detail = errorMessage(error, 'VidXP could not update the selected features for this installation.');
      setExternalFailure(detail.includes('not an isolated uv tool installation')
        ? 'This installation uses a different package manager. Change its features there, then check the connection again.'
        : 'VidXP could not reinstall this app with the selected features. Your current installation may need attention.');
      setExternalTechnical(detail);
    } finally {
      setBusy(null);
    }
  }

  async function setServerMode(mode: 'local' | 'shared' | 'stopped') {
    setBusy(mode === 'local' ? 'server-start' : mode === 'shared' ? 'server-share' : 'server-stop');
    setRuntimeFailure(null);
    setShareCopied(false);
    try {
      setServer(await (mode === 'local' ? startLocalServer() : mode === 'shared' ? startSharedServer() : stopLocalServer()));
    } catch (error) {
      setRuntimeFailure(errorMessage(error, 'VidXP could not change the sharing service.'));
    } finally {
      setBusy(null);
    }
  }

  async function setBrowserShared(running: boolean) {
    setBusy(running ? 'browser-share' : 'browser-stop');
    setRuntimeFailure(null);
    try {
      setBrowser(await (running ? startSharedBrowser() : stopBrowserService()));
    } catch (error) {
      setRuntimeFailure(errorMessage(error, 'VidXP could not change browser sharing.'));
    } finally {
      setBusy(null);
    }
  }

  async function copyShareToken() {
    if (!server?.bearer_token) return;
    await navigator.clipboard.writeText(server.bearer_token);
    setShareCopied(true);
  }

  async function setWorkerRunning(running: boolean) {
    setBusy(running ? 'worker-start' : 'worker-stop');
    setWorkerFailure(null);
    workerActionActive.current = true;
    workerStatusRequest.current += 1;
    try {
      setWorker(await (running ? startLocalWorker() : stopLocalWorker()));
    } catch (error) {
      setWorkerFailure({
        title: running ? 'Local processing could not be started' : 'Local processing could not be stopped',
        detail: errorMessage(error, 'VidXP could not change local video processing.'),
      });
    } finally {
      workerActionActive.current = false;
      setBusy(null);
    }
  }

  async function copyMcpConfig() {
    if (!mcpConfig) return;
    try {
      await navigator.clipboard.writeText(mcpConfig);
      setCopied(true);
    } catch {
      setRuntimeFailure('VidXP could not copy this automatically. Select the setup text and copy it manually.');
    }
  }

  return (
    <section aria-labelledby="target-summary-title">
      <div className="sectionHeading compactHeading">
        <Text className="eyebrow">YOUR VIDXP</Text>
        <Group justify="space-between" align="flex-end">
          <div>
            <Title id="target-summary-title" order={1} className="pageTitle">{profile.display_name}</Title>
            <Text className="lede">Choose how you want to use VidXP. Your setup is remembered the next time you open the app.</Text>
          </div>
          <Badge size="lg" variant="light" color={profile.kind === 'managed' ? 'violet' : 'teal'}>
            {profile.kind === 'managed' ? 'Managed by VidXP' : 'Your installation'}
          </Badge>
        </Group>
      </div>

      {needsRuntimeUpdate && (
        <Alert color="yellow" mb="md" title="Update this VidXP installation to continue">
          <Text size="sm">This Desktop version cannot safely read or manage the features in the selected Python installation.</Text>
          <Button mt="md" variant="light" loading={busy === 'features'} disabled={operationPending || busy !== null} onClick={() => void openExternalSetup()}>Update this installation</Button>
        </Alert>
      )}
      {!needsRuntimeUpdate && desktopSurfaceUnavailable && (
        <Alert color="yellow" mb="md" title="The browser interface is not enabled">
          <Text size="sm">Other installed features are still available. Open Setup options to add the browser interface.</Text>
        </Alert>
      )}
      {validationError && !needsRuntimeUpdate && (
        <Alert color="red" title="This setup needs attention" role="alert" mb="md">
          {validationError.message}
          <details className="technicalDetails"><summary>Technical details</summary><Code>{validationError.code}</Code></details>
          {profile.kind === 'managed' && (
            <Button mt="md" variant="light" loading={operationPending} onClick={onManageManaged}>Repair VidXP</Button>
          )}
        </Alert>
      )}
      {runtimeFailure && <Alert color="red" title="That did not work" role="alert" mb="md">{runtimeFailure}</Alert>}

      <div className="summaryPanel">
        <div className="summaryGrid">
          <Text>Status</Text><strong>{validationError ? 'Needs attention' : 'Connected'}</strong>
          <Text>Available</Text><strong>{runtimeCompatible ? [
            workerAvailable && 'local video processing',
            !desktopSurfaceUnavailable && 'browser interface',
            mcpAvailable && 'AI assistant integration',
            serverAvailable && 'app integration service',
          ].filter(Boolean).join(', ') || 'Command-line tools' : 'Available after the installation is updated'}</strong>
          <Text>Search features</Text><strong>{runtimeCompatible ? profile.capabilities.map(capabilityLabel).join(', ') || 'None installed' : 'Unknown until the installation is updated'}</strong>
          {profile.last_validated_at && <><Text>Last checked</Text><strong>{new Date(profile.last_validated_at).toLocaleString()}</strong></>}
        </div>
        <details className="technicalDetails">
          <summary>Technical details</summary>
          <div className="summaryGrid technicalSummary">
            {profile.observed_vidxp_version && <><Text>Version</Text><strong>{profile.observed_vidxp_version}</strong></>}
            {executable && <><Text>Program</Text><Code className="pathCode">{executable}</Code></>}
            <Text>Data location</Text><Code className="pathCode">{profile.display_data_root}</Code>
          </div>
        </details>
        <Group justify="space-between" mt="xl">
          <Group>
            <Button variant="default" leftSection={<IconRefresh aria-hidden="true" size={17} />} disabled={operationPending} onClick={onChooseAnother}>Switch installation</Button>
            <Button variant="subtle" leftSection={<IconRefresh aria-hidden="true" size={17} />} loading={checking} disabled={operationPending && !checking} onClick={() => void onRecheck()}>Check connection</Button>
            {profile.kind === 'managed'
              ? <Button variant="subtle" leftSection={<IconSettings aria-hidden="true" size={17} />} disabled={operationPending} onClick={onManageManaged}>Setup options</Button>
              : <Button variant="subtle" leftSection={<IconSettings aria-hidden="true" size={17} />} loading={busy === 'features'} disabled={operationPending || busy !== null} onClick={() => void openExternalSetup()}>Setup options</Button>}
          </Group>
          <Button leftSection={<IconExternalLink aria-hidden="true" size={17} />} loading={opening} disabled={Boolean(validationError) || desktopSurfaceUnavailable || operationPending} onClick={() => void onOpen()}>Open VidXP</Button>
        </Group>
      </div>

      <div className="setupPanel runtimeControlPanel">
        <Title order={2} className="panelTitle">Health and background services</Title>
        <Text size="sm" className="mutedText">Check whether VidXP is usable and control only the services you enabled.</Text>

        <Stack gap="sm" mt="lg">
          <Group justify="space-between" className="runtimeControlRow">
            <div><Text fw={650}>Readiness check</Text><Text size="sm" className="mutedText">Checks FFmpeg plus the packages and downloaded models required by each installed search feature.</Text></div>
            <Button variant="light" leftSection={<IconActivityHeartbeat size={17} />} loading={busy === 'doctor'} disabled={!runtimeCompatible || operationPending || busy !== null} onClick={() => void runDoctor()}>Check readiness</Button>
          </Group>
          {doctor && (
            <Alert color={doctor.ok && doctor.modalities.length > 0 ? 'teal' : 'yellow'} title={doctor.ok ? doctor.modalities.length > 0 ? 'VidXP is ready' : 'Video tools are ready' : `${failedChecks.length} item${failedChecks.length === 1 ? '' : 's'} need attention`}>
              {doctor.ok
                ? doctor.modalities.length > 0 ? `Ready search features: ${doctor.modalities.map(capabilityLabel).join(', ')}.` : 'No search features were reported, so this result covers only the shared video tools.'
                : <><Text size="sm">Open setup to repair a managed installation, or use your installer to repair an external one.</Text><details className="technicalDetails"><summary>See check details</summary>{failedChecks.map((check) => <Text size="xs" key={`${check.capability}-${check.name}`}>{check.error || `${check.name} is unavailable.`}</Text>)}</details></>}
            </Alert>
          )}

          {workerAvailable && <div>
            <Group justify="space-between" className="runtimeControlRow">
              <div>
                <Text fw={650}>Local video processing</Text>
                <Text size="sm" className="mutedText">{worker?.running ? 'Ready to process indexing, search, and model jobs on this computer.' : 'Starts automatically when VidXP needs to process a video. You can also start it now.'}</Text>
              </div>
              {worker?.running
                ? <Button color="red" variant="light" leftSection={<IconPlayerStop size={17} />} loading={busy === 'worker-stop'} disabled={operationPending || busy !== null} onClick={() => void setWorkerRunning(false)}>Stop processing</Button>
                : <Button variant="light" leftSection={<IconPlayerPlay size={17} />} loading={busy === 'worker-start'} disabled={operationPending || busy !== null} onClick={() => void setWorkerRunning(true)}>Start processing</Button>}
            </Group>
            {workerFailure && <Alert mt="sm" color="red" title={workerFailure.title} role="alert">{workerFailure.detail}</Alert>}
          </div>}

          {browserAvailable && <Group justify="space-between" className="runtimeControlRow" align="flex-start">
            <div>
              <Text fw={650}>Browser interface</Text>
              <Text size="sm" className="mutedText">{browser?.shared ? 'Shared without authentication on your current local network.' : 'Private to this computer unless you explicitly share it.'}</Text>
              {browser?.shared && browser.network_url && <div className="connectionDetails"><Text size="xs">Network address</Text><Code className="pathCode">{browser.network_url}</Code></div>}
            </div>
            {browser?.shared
              ? <Button color="red" variant="light" leftSection={<IconPlayerStop size={17} />} loading={busy === 'browser-stop'} disabled={operationPending || busy !== null} onClick={() => void setBrowserShared(false)}>Stop sharing</Button>
              : <Button color="yellow" variant="light" leftSection={<IconShare size={17} />} loading={busy === 'browser-share'} disabled={operationPending || busy !== null} onClick={() => void setBrowserShared(true)}>Share browser</Button>}
          </Group>}

          {mcpAvailable && <Group justify="space-between" className="runtimeControlRow">
            <div><Text fw={650}>AI assistant integration</Text><Text size="sm" className="mutedText">Install VidXP's MCP server and skills in Codex, or copy the MCP setup for another compatible assistant.</Text></div>
            <Group gap="xs" justify="flex-end">
              <Button leftSection={<IconPlugConnected size={17} />} loading={busy === 'codex'} disabled={operationPending || busy !== null} onClick={() => void setupCodex()}>Set up in Codex</Button>
              <Button variant="light" leftSection={<IconTerminal2 size={17} />} loading={busy === 'config'} disabled={operationPending || busy !== null} onClick={() => void loadMcpConfig()}>Copy MCP setup</Button>
            </Group>
          </Group>}

          {codexSetup && <Alert color="teal" title="VidXP is installed in Codex">
            <Text size="sm">{codexSetup.detail}</Text>
            <details className="technicalDetails"><summary>Installation details</summary><Text size="xs">Plugin {codexSetup.plugin_version} from {codexSetup.marketplace_name}</Text>{codexSetup.installed_path && <Code className="pathCode">{codexSetup.installed_path}</Code>}</details>
          </Alert>}

          {serverAvailable && <Group justify="space-between" className="runtimeControlRow">
            <div>
              <Text fw={650}>App integration service</Text>
              <Text size="sm" className="mutedText">{server?.shared ? 'Authenticated API and MCP access is available on your current local network.' : server?.running ? 'The API and MCP connection are private to this computer.' : 'Start this only when another app needs to connect to VidXP.'}</Text>
              {server?.running && <div className="connectionDetails">
                {server.origin && <><Text size="xs">API address</Text><Code className="pathCode">{server.origin}</Code></>}
                {server.mcp_url && <><Text size="xs" mt="xs">MCP address</Text><Code className="pathCode">{server.mcp_url}</Code></>}
                {server.shared && server.bearer_token && <details className="technicalDetails"><summary>Bearer token</summary><Code className="pathCode">{server.bearer_token}</Code><Button mt="xs" size="compact-sm" variant="subtle" leftSection={<IconCopy size={15} />} onClick={() => void copyShareToken()}>{shareCopied ? 'Copied' : 'Copy token'}</Button></details>}
              </div>}
            </div>
            <Group gap="xs" justify="flex-end">
              {!server?.running && <Button variant="light" leftSection={<IconPlayerPlay size={17} />} loading={busy === 'server-start'} disabled={operationPending || busy !== null} onClick={() => void setServerMode('local')}>Start locally</Button>}
              {!server?.shared && <Button color="yellow" variant="light" leftSection={<IconShare size={17} />} loading={busy === 'server-share'} disabled={operationPending || busy !== null} onClick={() => void setServerMode('shared')}>Share service</Button>}
              {server?.running && <Button color="red" variant="light" leftSection={<IconPlayerStop size={17} />} loading={busy === 'server-stop'} disabled={operationPending || busy !== null} onClick={() => void setServerMode('stopped')}>Stop service</Button>}
            </Group>
          </Group>}
        </Stack>
      </div>

      <Modal opened={mcpConfig !== null} onClose={() => setMcpConfig(null)} title="Connect an AI assistant" size="lg">
        <Text size="sm" mb="sm">Copy this MCP setup into a compatible AI assistant. It already points to this VidXP installation and video library.</Text>
        <Code block className="mcpConfigCode">{mcpConfig}</Code>
        <Group justify="flex-end" mt="md"><Button leftSection={<IconCopy size={17} />} onClick={() => void copyMcpConfig()}>{copied ? 'Copied' : 'Copy setup'}</Button></Group>
      </Modal>

      <Modal opened={readinessOpened} onClose={() => { if (busy !== 'doctor') setReadinessOpened(false); }} title={busy === 'doctor' ? 'Checking VidXP readiness' : 'VidXP readiness'} size="lg" closeOnClickOutside={busy !== 'doctor'} closeOnEscape={busy !== 'doctor'}>
        {busy === 'doctor' ? <Stack gap="md">
          <Group><Loader size="sm" /><Text>Checking the selected installation… {readinessElapsed}s</Text></Group>
          <Text size="sm" className="mutedText">VidXP is inspecting video tools, installed search packages, and model files. It does not download or change anything, and stops after three minutes if it cannot finish.</Text>
          <div><Text size="sm" fw={650}>Search features expected from this installation</Text><Text size="sm">{profile.capabilities.map(capabilityLabel).join(', ') || 'The installed runtime will report these as part of the check.'}</Text></div>
        </Stack> : doctor ? <Stack gap="sm">
          {doctor.modalities.length > 0
            ? <Alert color={doctor.ok ? 'teal' : 'yellow'} title={doctor.ok ? 'Ready to use' : 'Some items need attention'}>Search features checked: {doctor.modalities.map(capabilityLabel).join(', ')}.</Alert>
            : <Alert color="yellow" title="No search features were checked">Only the shared video tools were reported by this installation.</Alert>}
          {doctor.checks.map((check) => <Group key={`${check.capability}-${check.kind}-${check.name}`} justify="space-between" className="runtimeControlRow" wrap="nowrap">
            <div><Text fw={650}>{check.name}</Text><Text size="xs" className="mutedText">{capabilityLabel(check.capability)} · {check.kind === 'model' ? 'Downloaded model' : 'Installed package or video tool'}</Text>{check.error && <Text size="xs" c="red">{check.error}</Text>}</div>
            <Badge color={check.ok ? 'teal' : 'yellow'} variant="light">{check.ok ? 'Ready' : 'Needs attention'}</Badge>
          </Group>)}
          <Group justify="flex-end"><Button onClick={() => setReadinessOpened(false)}>Done</Button></Group>
        </Stack> : null}
      </Modal>

      <Modal opened={externalSetupOpened} onClose={() => { if (busy === null) setExternalSetupOpened(false); }} title="Change features for this installation" size="lg" closeOnClickOutside={busy === null} closeOnEscape={busy === null}>
        <Text size="sm" mb="md">Choose what VidXP can search, where video work runs, and how other apps can connect.</Text>
        {needsRuntimeUpdate && externalManifest && <Alert color="yellow" title="This update is required" mb="md">VidXP will update this same installation from {profile.observed_vidxp_version} to {externalManifest.package_version}, then apply the selected features. It will not create a Desktop-managed copy.</Alert>}
        {externalFailure && <Alert color="red" title="Features could not be updated" mb="md">{externalFailure}{externalTechnical && <details className="technicalDetails"><summary>Technical details</summary><Code block>{externalTechnical}</Code></details>}</Alert>}
        {!externalManifest ? <Loader size="sm" /> : <Stack gap="sm">
          <Text fw={650}>Search features</Text>
          {Object.entries(externalManifest.capabilities).map(([id, capability]) => (
            <Checkbox key={id} checked={externalCapabilities.includes(id)} disabled={busy !== null} onChange={(event) => { const checked = event.currentTarget.checked; setExternalCapabilities((current) => checked ? [...current, id] : current.filter((value) => value !== id)); if (!checked) setExternalSurfaces((current) => current.filter((surface) => surface !== 'worker')); }} label={capability.label} />
          ))}
          <Text fw={650} mt="sm">Video processing</Text>
          {Object.entries(externalManifest.surfaces).filter(([id]) => id === 'worker').map(([id, surface]) => {
            return <Checkbox key={id} checked={externalSurfaces.includes(id)} disabled={busy !== null} onChange={(event) => { const checked = event.currentTarget.checked; setExternalSurfaces((current) => checked ? [...current, id] : current.filter((value) => value !== id)); if (checked && externalManifest) setExternalCapabilities(Object.keys(externalManifest.capabilities)); }} label={surface.label} description={surface.description} />;
          })}
          <Text fw={650} mt="sm">Interfaces and integrations</Text>
          {Object.entries(externalManifest.surfaces).filter(([id]) => id !== 'worker').map(([id, surface]) => {
            return <Checkbox key={id} checked={externalSurfaces.includes(id)} disabled={busy !== null} onChange={(event) => { const checked = event.currentTarget.checked; setExternalSurfaces((current) => checked ? [...current, id] : current.filter((value) => value !== id)); }} label={surface.label} description={surface.description} />;
          })}
        </Stack>}
        {!needsRuntimeUpdate && <Alert color="blue" mt="md" title="Your existing installation stays selected">VidXP reinstalls this isolated app environment at its current compatible version with the selected features. It does not replace it with a Desktop-managed copy.</Alert>}
        <Group justify="flex-end" mt="md">
          <Button variant="default" disabled={busy !== null} onClick={() => setExternalSetupOpened(false)}>Cancel</Button>
          <Button loading={busy === 'features'} disabled={busy !== null || (!needsRuntimeUpdate && [...externalCapabilities].sort().join(',') === [...profile.capabilities].sort().join(',') && [...externalSurfaces].sort().join(',') === [...profile.surfaces].sort().join(','))} onClick={() => void applyExternalFeatures()}>{needsRuntimeUpdate ? 'Update and apply' : 'Apply changes'}</Button>
        </Group>
      </Modal>
    </section>
  );
}
