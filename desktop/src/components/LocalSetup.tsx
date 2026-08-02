import {
  Alert,
  Badge,
  Button,
  Code,
  Group,
  Loader,
  Radio,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import {
  IconAlertCircle,
  IconArrowLeft,
  IconCheck,
  IconFileSearch,
  IconFolderOpen,
  IconRefresh,
} from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';

import {
  activateLocalTarget,
  chooseLocalExecutable,
  discoverLocalTargets,
  errorMessage,
  inspectLocalTarget,
  type LocalTargetCandidate,
  type LocalTargetInspection,
  type TargetSetupState,
} from '../tauri';

interface LocalSetupProps {
  onBack: () => void;
  onActivated: (setup: TargetSetupState) => void;
}

interface CandidateState extends LocalTargetCandidate {
  checking?: boolean;
  inspection?: LocalTargetInspection;
  inspectionError?: string;
}

function candidatePath(candidate: LocalTargetCandidate): string {
  return candidate.executable;
}

function candidateDisplayPath(candidate: LocalTargetCandidate): string {
  return candidate.display_path || candidatePath(candidate);
}

const stateLabel = {
  ready_to_use: 'Ready to use',
  update_required: 'Update required',
  cannot_start: 'Cannot start',
} as const;

export function LocalSetup({ onBack, onActivated }: LocalSetupProps) {
  const [candidates, setCandidates] = useState<CandidateState[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [displayName, setDisplayName] = useState('Local VidXP');
  const [busy, setBusy] = useState<'discover' | 'browse' | 'activate' | null>('discover');
  const [failure, setFailure] = useState<string | null>(null);
  const candidateGeneration = useRef(new Map<string, number>());

  async function discover() {
    setBusy('discover');
    setFailure(null);
    try {
      const discovered = await discoverLocalTargets();
      setCandidates((current) => discovered.map((candidate) => {
        const prior = current.find((item) => candidatePath(item) === candidatePath(candidate));
        return {
          ...candidate,
          checking: prior?.checking,
          inspection: prior?.inspection,
          inspectionError: prior?.inspectionError,
        };
      }));
    } catch (error) {
      setFailure(errorMessage(error, 'VidXP discovery could not be completed.'));
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    void discover();
  }, []);

  async function checkCandidate(path: string) {
    const generation = (candidateGeneration.current.get(path) ?? 0) + 1;
    candidateGeneration.current.set(path, generation);
    setCandidates((current) => current.map((candidate) => (
      candidatePath(candidate) === path
        ? { ...candidate, checking: true, inspection: undefined, inspectionError: undefined }
        : candidate
    )));
    try {
      const inspection = await inspectLocalTarget(path);
      if (candidateGeneration.current.get(path) !== generation) return;
      setCandidates((current) => current.map((candidate) => (
        candidatePath(candidate) === path
          ? { ...candidate, checking: false, inspection, inspectionError: undefined }
          : candidate
      )));
    } catch (error) {
      if (candidateGeneration.current.get(path) !== generation) return;
      const message = errorMessage(error, 'This executable could not be inspected.');
      setCandidates((current) => current.map((candidate) => (
        candidatePath(candidate) === path
          ? { ...candidate, checking: false, inspection: undefined, inspectionError: message }
          : candidate
      )));
    }
  }

  function selectCandidate(path: string) {
    setSelectedPath(path);
    const candidate = candidates.find((item) => candidatePath(item) === path);
    if (!candidate?.checking && !candidate?.inspection) void checkCandidate(path);
  }

  async function browse() {
    setBusy('browse');
    setFailure(null);
    try {
      const candidate = await chooseLocalExecutable();
      if (!candidate) return;
      const path = candidatePath(candidate);
      setCandidates((current) => [candidate, ...current.filter((item) => candidatePath(item) !== path)]);
      setSelectedPath(path);
      void checkCandidate(path);
    } catch (error) {
      setFailure(errorMessage(error, 'The selected executable could not be opened.'));
    } finally {
      setBusy(null);
    }
  }

  async function activate(inspection: LocalTargetInspection) {
    if (!inspection.adoptable || !inspection.validation) return;
    setBusy('activate');
    setFailure(null);
    try {
      const setup = await activateLocalTarget(
        inspection.validation.canonical_executable,
        displayName.trim() || undefined,
      );
      onActivated(setup);
    } catch (error) {
      setFailure(errorMessage(error, 'The inspected target could not be activated.'));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section aria-labelledby="local-setup-title">
      <Button variant="subtle" leftSection={<IconArrowLeft aria-hidden="true" size={17} />} onClick={onBack} disabled={busy !== null}>Back</Button>
      <div className="sectionHeading compactHeading">
        <Text className="eyebrow">EXISTING INSTALLATION</Text>
        <Title id="local-setup-title" order={1} className="pageTitle">Connect this desktop to VidXP</Title>
        <Text className="lede">Choose an installation to check whether it supports this Desktop. Checking and connecting it will not modify it.</Text>
      </div>

      <div className="setupPanel">
        <Group justify="space-between" align="flex-start" mb="md">
          <div>
            <Title order={2} className="panelTitle">Found on this computer</Title>
            <Text className="mutedText" size="sm">Select one candidate to check it, or browse to another executable.</Text>
          </div>
          <Button variant="default" leftSection={<IconRefresh aria-hidden="true" size={16} />} loading={busy === 'discover'} disabled={busy !== null} onClick={() => void discover()}>Scan again</Button>
        </Group>

        {busy === 'discover' && candidates.length === 0 ? (
          <div className="emptyState" role="status" aria-live="polite"><Loader size="sm" /> Looking for VidXP executables…</div>
        ) : candidates.length > 0 ? (
          <Radio.Group value={selectedPath} onChange={selectCandidate} aria-label="Discovered VidXP executables" readOnly={busy !== null}>
            <Stack gap="xs">
              {candidates.map((candidate) => {
                const path = candidatePath(candidate);
                const selected = selectedPath === path;
                const inspection = candidate.inspection;
                const validation = inspection?.validation;
                const title = inspection?.reported_version ? `VidXP ${inspection.reported_version}` : 'VidXP executable';
                const color = inspection?.state === 'ready_to_use' ? 'teal' : inspection?.state === 'update_required' ? 'yellow' : inspection?.state === 'cannot_start' || candidate.inspectionError ? 'red' : 'gray';
                return (
                  <div className="candidateWrapper" key={path}>
                    <Radio.Card className="candidateCard" value={path}>
                      <Group wrap="nowrap" align="flex-start">
                        <Radio.Indicator aria-hidden="true" />
                        <div className="candidateCopy">
                          <Group justify="space-between" align="flex-start" gap="xs">
                            <Text fw={650}>{title}</Text>
                            <Badge color={color} variant="light">
                              {candidate.checking ? 'Checking…' : inspection ? stateLabel[inspection.state] : candidate.inspectionError ? 'Cannot start' : 'Found'}
                            </Badge>
                          </Group>
                          <Code className="pathCode">{candidateDisplayPath(candidate)}</Code>
                          <Text size="xs" className="mutedText">{candidate.checking ? 'Checking compatibility…' : inspection || candidate.inspectionError ? candidate.source && `Discovered via ${candidate.source}` : 'Not checked'}</Text>
                        </div>
                      </Group>
                    </Radio.Card>

                    {selected && candidate.checking && (
                      <div className="inlineInspection" role="status" aria-live="polite"><Loader size="xs" /> Checking identity, probe, and launch compatibility…</div>
                    )}
                    {selected && candidate.inspectionError && (
                      <Alert m="sm" color="red" icon={<IconAlertCircle aria-hidden="true" />} title="Cannot start">{candidate.inspectionError}</Alert>
                    )}
                    {selected && inspection && (
                      <div className="inlineInspection">
                        <Text size="sm">{inspection.message}</Text>
                        <div className="validationGrid">
                          <span>Desktop probe</span><strong>{inspection.probe_compatible ? `Compatible · protocol ${validation?.protocol_version ?? 'supported'}` : 'Unavailable or incompatible'}</strong>
                          <span>Launch contract</span><strong>{inspection.launch_compatible ? `Compatible · protocol ${validation?.launch_protocol_version ?? 'supported'}` : 'Not accepted'}</strong>
                          {validation?.python_version && <><span>Python</span><strong>{validation.python_version}</strong></>}
                          {validation?.display_data_root && <><span>Data root</span><Code className="pathCode">{validation.display_data_root}</Code></>}
                          {validation?.frontend && <><span>Browser interface</span><strong>{validation.frontend.launchable ? 'Available' : 'Unavailable'}</strong></>}
                        </div>
                        {inspection.remediation && <Text size="sm" mt="sm"><strong>What to do:</strong> {inspection.remediation}</Text>}
                        {validation?.can_launch_frontend === false && (
                          <Alert mt="sm" color="yellow" title="Desktop action required">
                            <Text size="sm"><strong>Usable:</strong> This installation remains available through its own command-line workflows.</Text>
                            <Text size="sm" mt="xs"><strong>Missing:</strong> {validation.frontend?.message}</Text>
                            <Text size="sm" mt="xs"><strong>Enable it:</strong> {validation.frontend?.remediation}</Text>
                          </Alert>
                        )}
                        {inspection.technical_details && <details className="technicalDetails"><summary>Technical details</summary><Code block>{inspection.technical_details}</Code></details>}
                        {inspection.adoptable && validation && (
                          <Stack gap="sm" mt="md">
                            <TextInput label="Target name" description="Used only to identify this connection in VidXP Desktop." value={displayName} onChange={(event) => setDisplayName(event.currentTarget.value)} />
                            <Group justify="flex-end">
                              <Button color={validation.can_launch_frontend === false ? 'yellow' : 'teal'} leftSection={validation.can_launch_frontend === false ? <IconAlertCircle size={16} /> : <IconCheck size={16} />} loading={busy === 'activate'} onClick={() => void activate(inspection)}>
                                {validation.can_launch_frontend === false ? 'Save external target' : 'Use this installation'}
                              </Button>
                            </Group>
                          </Stack>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </Stack>
          </Radio.Group>
        ) : (
          <div className="emptyState"><IconFileSearch aria-hidden="true" size={22} /><span>No candidates were found automatically. Your installation may still be usable.</span></div>
        )}

        <Button mt="md" variant="light" leftSection={<IconFolderOpen aria-hidden="true" size={17} />} loading={busy === 'browse'} disabled={busy !== null} onClick={() => void browse()}>Browse for an executable…</Button>
      </div>

      {busy === 'activate' && <div className="statusRegion" role="status" aria-live="polite"><Loader size="xs" /> Saving and activating this target…</div>}
      {failure && <Alert icon={<IconAlertCircle aria-hidden="true" />} color="red" title="Could not continue" role="alert">{failure}</Alert>}
    </section>
  );
}
