import { Alert, Button, Group, Loader, Text, ThemeIcon, Title } from '@mantine/core';
import { IconAlertCircle, IconArrowLeft, IconDownload } from '@tabler/icons-react';
import { useEffect, useState } from 'react';

import { LocalSetup } from './components/LocalSetup';
import { ManagedSetup } from './components/ManagedSetup';
import { TargetChoice } from './components/TargetChoice';
import { TargetSummary } from './components/TargetSummary';
import { TitleBar } from './components/TitleBar';
import {
  chooseManagedTarget,
  errorMessage,
  selectedProfile,
  targetSetupState,
  type TargetKind,
  type TargetSetupState,
} from './tauri';

type Stage = 'loading' | 'choice' | 'local' | 'managed-confirm' | 'managed' | 'summary';

export function App() {
  const [stage, setStage] = useState<Stage>('loading');
  const [choice, setChoice] = useState<TargetKind | null>(null);
  const [setupState, setSetupState] = useState<TargetSetupState | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [choosingManaged, setChoosingManaged] = useState(false);

  useEffect(() => {
    let current = true;
    void targetSetupState()
      .then((state) => {
        if (!current) return;
        setSetupState(state);
        setStage(selectedProfile(state) ? 'summary' : 'choice');
      })
      .catch((error: unknown) => {
        if (!current) return;
        setFailure(errorMessage(error, 'VidXP Desktop could not load its target profiles.'));
        setStage('choice');
      });
    return () => {
      current = false;
    };
  }, []);

  async function refreshTargetState() {
    const state = await targetSetupState();
    setSetupState(state);
  }

  function continueFromChoice() {
    if (choice === 'existing_local') setStage('local');
    if (choice === 'managed') setStage('managed-confirm');
  }

  async function confirmManaged() {
    setChoosingManaged(true);
    setFailure(null);
    try {
      await chooseManagedTarget();
      await refreshTargetState();
      setStage('managed');
    } catch (error) {
      setFailure(errorMessage(error, 'The managed target could not be selected.'));
    } finally {
      setChoosingManaged(false);
    }
  }

  async function activated() {
    await refreshTargetState();
    setStage('summary');
  }

  const profile = setupState ? selectedProfile(setupState) : null;

  return (
    <div className="appViewport">
      <TitleBar />
      <div className="appBackdrop">
        <div className="aurora auroraOne" aria-hidden="true" />
        <div className="aurora auroraTwo" aria-hidden="true" />
        <div className="mainScroller">
          <main className="appShell">
            <div className="contentFrame">
          {failure && (
            <Alert icon={<IconAlertCircle aria-hidden="true" />} color="red" title="Desktop initialization issue" role="alert" mb="lg">
              {failure}
            </Alert>
          )}
          {setupState?.issues?.map((issue) => (
            <Alert key={`${issue.code}-${issue.message}`} color="yellow" title={issue.code || 'Target profile notice'} mb="md">
              {issue.message || issue.detail}
            </Alert>
          ))}

          {stage === 'loading' && (
            <div className="loadingState" role="status" aria-live="polite">
              <Loader size="sm" /> Restoring your VidXP target…
            </div>
          )}
          {stage === 'choice' && (
            <TargetChoice value={choice} onChange={setChoice} onContinue={continueFromChoice} />
          )}
          {stage === 'local' && (
            <LocalSetup onBack={() => setStage('choice')} onActivated={activated} />
          )}
          {stage === 'managed-confirm' && (
            <section aria-labelledby="managed-confirm-title">
              <Button variant="subtle" leftSection={<IconArrowLeft aria-hidden="true" size={17} />} onClick={() => setStage('choice')}>Back</Button>
              <div className="confirmationPanel">
                <ThemeIcon size={54} radius="xl" variant="light"><IconDownload aria-hidden="true" size={28} /></ThemeIcon>
                <Text className="eyebrow" mt="xl">CONFIRM MANAGED SETUP</Text>
                <Title id="managed-confirm-title" order={1} className="pageTitle">Let VidXP Desktop manage the runtime?</Title>
                <Text className="lede centeredCopy">The next screen lets you choose capabilities and models. Nothing is downloaded until you select Configure VidXP.</Text>
                <div className="ownershipNote">
                  <strong>Desktop owned</strong>
                  <span>The app may install, validate, update, stop, or replace only this private runtime.</span>
                </div>
                <Group justify="center" mt="xl">
                  <Button variant="default" onClick={() => setStage('choice')}>Choose a different target</Button>
                  <Button loading={choosingManaged} onClick={() => void confirmManaged()}>Continue to setup</Button>
                </Group>
              </div>
            </section>
          )}
          {stage === 'managed' && (
            <ManagedSetup onBack={() => setStage('choice')} onReady={refreshTargetState} />
          )}
          {stage === 'summary' && profile && (
            <TargetSummary
              profile={profile}
              validationError={setupState?.selected_profile_error}
              notice={setupState?.notice}
              onChooseAnother={() => {
                setChoice(null);
                setStage('choice');
              }}
            />
          )}
          {stage === 'summary' && !profile && (
            <Alert color="yellow" title="The selected target is missing">
              Choose another target to continue.
              <Button mt="md" variant="light" onClick={() => setStage('choice')}>Choose a target</Button>
            </Alert>
          )}
            </div>

            <footer className="appFooter">
              <span>Target metadata stays private to VidXP Desktop.</span>
              <span>Credentials are never stored in this setup profile.</span>
            </footer>
          </main>
        </div>
      </div>
    </div>
  );
}
