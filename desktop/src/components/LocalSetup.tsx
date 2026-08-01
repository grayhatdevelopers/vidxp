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
import { useEffect, useMemo, useState } from 'react';

import {
  activateLocalTarget,
  chooseLocalExecutable,
  discoverLocalTargets,
  errorMessage,
  isCompatible,
  validateLocalTarget,
  type LocalTargetCandidate,
  type LocalTargetValidation,
} from '../tauri';

interface LocalSetupProps {
  onBack: () => void;
  onActivated: () => Promise<void>;
}

interface CandidateState extends LocalTargetCandidate {
  validation?: LocalTargetValidation;
  validationError?: string;
}

function candidatePath(candidate: LocalTargetCandidate): string {
  return candidate.canonical_executable || candidate.executable;
}

function candidateDisplayPath(candidate: LocalTargetCandidate): string {
  return candidate.display_path || candidatePath(candidate);
}

export function LocalSetup({ onBack, onActivated }: LocalSetupProps) {
  const [candidates, setCandidates] = useState<CandidateState[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [displayName, setDisplayName] = useState('Local VidXP');
  const [busy, setBusy] = useState<'discover' | 'browse' | 'validate' | 'activate' | null>(
    'discover',
  );
  const [failure, setFailure] = useState<{ code?: string; message: string; action?: string } | null>(
    null,
  );

  const selected = useMemo(
    () => candidates.find((candidate) => candidatePath(candidate) === selectedPath) ?? null,
    [candidates, selectedPath],
  );
  const validation = selected?.validation ?? null;

  async function discover() {
    setBusy('discover');
    setFailure(null);
    try {
      const discovered = await discoverLocalTargets();
      setCandidates((current) => discovered.map((candidate) => {
        const previous = current.find((item) => candidatePath(item) === candidatePath(candidate));
        return {
          ...candidate,
          validation: previous?.validation,
          validationError: previous?.validationError,
        };
      }));
    } catch (error) {
      setFailure({ message: errorMessage(error, 'VidXP discovery could not be completed.') });
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    void discover();
  }, []);

  function selectCandidate(path: string) {
    setSelectedPath(path);
    const candidate = candidates.find((item) => candidatePath(item) === path);
    setFailure(candidate?.validationError ? { message: candidate.validationError } : null);
  }

  async function browse() {
    setBusy('browse');
    setFailure(null);
    try {
      const candidate = await chooseLocalExecutable();
      if (!candidate) return;
      const path = candidatePath(candidate);
      setCandidates((current) => [candidate, ...current.filter((item) => candidatePath(item) !== path)]);
      selectCandidate(path);
    } catch (error) {
      setFailure({ message: errorMessage(error, 'The selected executable could not be opened.') });
    } finally {
      setBusy(null);
    }
  }

  async function validate() {
    if (!selectedPath) return;
    setBusy('validate');
    setFailure(null);
    try {
      const result = await validateLocalTarget(selectedPath);
      setCandidates((current) => current.map((candidate) => (
        candidatePath(candidate) === selectedPath
          ? { ...candidate, validation: result, validationError: undefined }
          : candidate
      )));
      if (!isCompatible(result)) {
        setFailure({
          code: result.error?.code,
          message: result.error?.message ?? result.error?.detail ?? 'This VidXP target is not compatible.',
          action: result.error?.action,
        });
      }
    } catch (error) {
      const message = errorMessage(error, 'VidXP validation failed.');
      setCandidates((current) => current.map((candidate) => (
        candidatePath(candidate) === selectedPath
          ? { ...candidate, validation: undefined, validationError: message }
          : candidate
      )));
      setFailure({ message });
    } finally {
      setBusy(null);
    }
  }

  async function activate() {
    if (!validation || !isCompatible(validation)) return;
    setBusy('activate');
    setFailure(null);
    try {
      await activateLocalTarget({
        executable: validation.canonical_executable || validation.executable || selectedPath || '',
        displayName: displayName.trim() || undefined,
      });
      await onActivated();
    } catch (error) {
      setFailure({ message: errorMessage(error, 'The validated target could not be activated.') });
      setBusy(null);
    }
  }

  const compatible = validation && isCompatible(validation);
  const desktopSurfaceUnavailable = compatible && validation.can_launch_frontend === false;

  return (
    <section aria-labelledby="local-setup-title">
      <Button variant="subtle" leftSection={<IconArrowLeft aria-hidden="true" size={17} />} onClick={onBack}>
        Back
      </Button>
      <div className="sectionHeading compactHeading">
        <Text className="eyebrow">EXISTING INSTALLATION</Text>
        <Title id="local-setup-title" order={1} className="pageTitle">
          Connect this desktop to VidXP
        </Title>
        <Text className="lede">
          Select a candidate, review its resolved path, then validate it. Discovery never selects or
          changes an installation for you.
        </Text>
      </div>

      <div className="setupPanel">
        <Group justify="space-between" align="flex-start" mb="md">
          <div>
            <Title order={2} className="panelTitle">Found on this computer</Title>
            <Text className="mutedText" size="sm">Choose one candidate or browse to another executable.</Text>
          </div>
          <Button
            variant="default"
            leftSection={<IconRefresh aria-hidden="true" size={16} />}
            loading={busy === 'discover'}
            onClick={() => void discover()}
          >
            Scan again
          </Button>
        </Group>

        {busy === 'discover' && candidates.length === 0 ? (
          <div className="emptyState" role="status" aria-live="polite">
            <Loader size="sm" /> Looking for VidXP executables…
          </div>
        ) : candidates.length > 0 ? (
          <Radio.Group
            value={selectedPath}
            onChange={selectCandidate}
            aria-label="Discovered VidXP executables"
          >
            <Stack gap="xs">
              {candidates.map((candidate) => {
                const path = candidatePath(candidate);
                const candidateValidation = candidate.validation;
                const validated = candidateValidation && isCompatible(candidateValidation);
                return (
                  <Radio.Card className="candidateCard" key={path} value={path}>
                    <Group wrap="nowrap" align="flex-start">
                      <Radio.Indicator aria-hidden="true" />
                      <div className="candidateCopy">
                        <Group justify="space-between" align="flex-start" gap="xs">
                          <Text fw={650}>{candidate.display_name || 'Candidate executable'}</Text>
                          <Badge color={validated ? 'teal' : candidate.validationError ? 'red' : 'gray'} variant="light">
                            {validated ? 'Compatible' : candidate.validationError ? 'Validation failed' : 'Not validated'}
                          </Badge>
                        </Group>
                        <Code className="pathCode">{candidateDisplayPath(candidate)}</Code>
                        {candidate.source && <Text size="xs" className="mutedText">Discovered via {candidate.source}</Text>}
                        {candidateValidation && (
                          <div className="candidateMetadata">
                            <Text size="xs">VidXP {candidateValidation.vidxp_version || 'reported'} · Python {candidateValidation.python_version || 'reported'}</Text>
                            <Text size="xs">Probe {candidateValidation.protocol_version ?? candidateValidation.probe_version ?? 'compatible'} · Browser surface {candidateValidation.can_launch_frontend === false ? 'unavailable' : 'available'}</Text>
                          </div>
                        )}
                        {candidate.validationError && <Text size="xs" c="red.3" mt="xs">{candidate.validationError}</Text>}
                      </div>
                    </Group>
                  </Radio.Card>
                );
              })}
            </Stack>
          </Radio.Group>
        ) : (
          <div className="emptyState">
            <IconFileSearch aria-hidden="true" size={22} />
            <span>No candidates were found automatically. Your installation may still be usable.</span>
          </div>
        )}

        <Button
          mt="md"
          variant="light"
          leftSection={<IconFolderOpen aria-hidden="true" size={17} />}
          loading={busy === 'browse'}
          onClick={() => void browse()}
        >
          Browse for an executable…
        </Button>
      </div>

      {selected && (
        <div className="setupPanel" aria-labelledby="review-candidate-title">
          <Group justify="space-between" mb="lg">
            <Title id="review-candidate-title" order={2} className="panelTitle">Review and validate</Title>
            <Badge variant="light">No downloads</Badge>
          </Group>
          <Stack gap="md">
            <div>
              <Text className="fieldLabel">Resolved executable</Text>
              <Code className="resolvedPath">{candidateDisplayPath(selected)}</Code>
            </div>
            <TextInput
              label="Target name"
              description="Used only to identify this connection in VidXP Desktop."
              value={displayName}
              onChange={(event) => setDisplayName(event.currentTarget.value)}
            />
            <Text size="sm" className="mutedText">
              Validation will report the installation&apos;s configured data root; VidXP Desktop does not rewrite it.
            </Text>
            <Group justify="flex-end">
              <Button loading={busy === 'validate'} disabled={Boolean(busy) || !selectedPath} onClick={() => void validate()}>
                Validate installation
              </Button>
            </Group>
          </Stack>
        </div>
      )}

      <div className="statusRegion" role="status" aria-live="polite" aria-atomic="true">
        {busy === 'validate' && <><Loader size="xs" /> Checking identity, compatibility, and client support…</>}
        {busy === 'activate' && <><Loader size="xs" /> Saving and activating this target…</>}
      </div>

      {failure && (
        <Alert icon={<IconAlertCircle aria-hidden="true" />} color="red" title={failure.code ? `Could not validate · ${failure.code}` : 'Could not continue'} role="alert">
          <Text size="sm">{failure.message}</Text>
          {failure.action && <Text size="sm" mt="xs" fw={600}>{failure.action}</Text>}
        </Alert>
      )}

      {compatible && (
        <Alert
          icon={desktopSurfaceUnavailable ? <IconAlertCircle aria-hidden="true" /> : <IconCheck aria-hidden="true" />}
          color={desktopSurfaceUnavailable ? 'yellow' : 'teal'}
          title={desktopSurfaceUnavailable ? 'Compatible installation · desktop action required' : 'Compatible VidXP installation'}
        >
          <div className="validationGrid">
            <span>VidXP</span><strong>{validation.vidxp_version || 'Verified'}</strong>
            <span>Protocol</span><strong>{validation.protocol_version ?? validation.probe_version ?? 'Compatible'}</strong>
            <span>Python</span><strong>{validation.python_version || validation.display_python_executable || validation.python_executable || 'Reported by target'}</strong>
            {validation.display_data_root && <><span>Data root</span><Code className="pathCode">{validation.display_data_root}</Code></>}
            <span>Desktop action</span><strong>{desktopSurfaceUnavailable ? 'Unavailable' : 'Browser interface available'}</strong>
          </div>
          {desktopSurfaceUnavailable && (
            <>
              <Text size="sm" mt="md"><strong>Usable:</strong> The installation remains available through its own command-line and package-managed workflows.</Text>
              <Text size="sm" mt="xs"><strong>Missing:</strong> {validation.frontend?.message || validation.warnings?.[0] || 'The supported browser interface cannot currently be launched.'}</Text>
              <Text size="sm" mt="xs"><strong>How to enable it:</strong> {validation.frontend?.remediation || "Use this installation's own package-management workflow to enable the VidXP browser interface, then revalidate."}</Text>
            </>
          )}
          <Group justify="flex-end" mt="md">
            <Button color={desktopSurfaceUnavailable ? 'yellow' : 'teal'} loading={busy === 'activate'} onClick={() => void activate()}>
              {desktopSurfaceUnavailable ? 'Save external target' : 'Use this installation'}
            </Button>
          </Group>
        </Alert>
      )}
    </section>
  );
}
