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

function candidatePath(candidate: LocalTargetCandidate): string {
  return candidate.canonical_executable || candidate.executable;
}

export function LocalSetup({ onBack, onActivated }: LocalSetupProps) {
  const [candidates, setCandidates] = useState<LocalTargetCandidate[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [displayName, setDisplayName] = useState('Local VidXP');
  const [validation, setValidation] = useState<LocalTargetValidation | null>(null);
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

  async function discover() {
    setBusy('discover');
    setFailure(null);
    try {
      setCandidates(await discoverLocalTargets());
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
    setValidation(null);
    setFailure(null);
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
    setValidation(null);
    setFailure(null);
    try {
      const result = await validateLocalTarget(selectedPath);
      setValidation(result);
      if (!isCompatible(result)) {
        setFailure({
          code: result.error?.code,
          message: result.error?.message ?? result.error?.detail ?? 'This VidXP target is not compatible.',
          action: result.error?.action,
        });
      }
    } catch (error) {
      setFailure({ message: errorMessage(error, 'VidXP validation failed.') });
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
                return (
                  <Radio.Card className="candidateCard" key={path} value={path}>
                    <Group wrap="nowrap" align="flex-start">
                      <Radio.Indicator aria-hidden="true" />
                      <div className="candidateCopy">
                        <Text fw={650}>{candidate.display_name || 'VidXP executable'}</Text>
                        <Code className="pathCode">{path}</Code>
                        {candidate.source && <Text size="xs" className="mutedText">{candidate.source}</Text>}
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
              <Code className="resolvedPath">{candidatePath(selected)}</Code>
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
        <Alert icon={<IconCheck aria-hidden="true" />} color="teal" title="Compatible VidXP installation">
          <div className="validationGrid">
            <span>VidXP</span><strong>{validation.vidxp_version || 'Verified'}</strong>
            <span>Protocol</span><strong>{validation.protocol_version ?? validation.probe_version ?? 'Compatible'}</strong>
            <span>Python</span><strong>{validation.python_version || validation.python_executable || 'Reported by target'}</strong>
            <span>Client</span><strong>{validation.can_launch_frontend === false ? 'Unavailable' : 'Ready to launch'}</strong>
          </div>
          {validation.warnings?.map((warning) => <Text size="sm" mt="xs" key={warning}>{warning}</Text>)}
          <Group justify="flex-end" mt="md">
            <Button color="teal" loading={busy === 'activate'} onClick={() => void activate()}>
              Use this installation
            </Button>
          </Group>
        </Alert>
      )}
    </section>
  );
}
