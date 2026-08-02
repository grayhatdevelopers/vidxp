import { Alert, Badge, Button, Group, Loader, Stack, Text, ThemeIcon, Title } from '@mantine/core';
import { IconAlertCircle, IconArrowLeft, IconDownload, IconTrash } from '@tabler/icons-react';
import { useEffect, useReducer, useRef } from 'react';

import { LocalSetup } from './components/LocalSetup';
import { ManagedSetup } from './components/ManagedSetup';
import { TargetChoice } from './components/TargetChoice';
import { TargetSummary } from './components/TargetSummary';
import { TitleBar } from './components/TitleBar';
import {
  beginManagedSetup,
  cancelManagedSetup,
  deleteTargetProfile,
  errorMessage,
  recheckTargetState,
  selectTargetProfile,
  selectedProfile,
  targetSetupState,
  type ManagedSetupDraft,
  type TargetKind,
  type TargetSetupState,
} from './tauri';

type Stage = 'loading' | 'choice' | 'local' | 'managed-confirm' | 'managed' | 'summary';
interface LifecycleState {
  stage: Stage;
  choice: TargetKind | null;
  setup: TargetSetupState | null;
  draft: ManagedSetupDraft | null;
  failure: string | null;
  checking: boolean;
  busyProfile: string | null;
}
type Action =
  | { type: 'navigate'; stage: Stage }
  | { type: 'choice'; choice: TargetKind | null }
  | { type: 'loaded'; setup: TargetSetupState }
  | { type: 'state'; setup: TargetSetupState }
  | { type: 'draft'; draft: ManagedSetupDraft | null }
  | { type: 'failure'; failure: string | null }
  | { type: 'checking'; checking: boolean }
  | { type: 'busyProfile'; id: string | null };

const initialState: LifecycleState = {
  stage: 'loading', choice: null, setup: null, draft: null, failure: null, checking: false, busyProfile: null,
};

function reducer(state: LifecycleState, action: Action): LifecycleState {
  switch (action.type) {
    case 'navigate': return { ...state, stage: action.stage, failure: null };
    case 'choice': return { ...state, choice: action.choice };
    case 'loaded': return { ...state, setup: action.setup, stage: selectedProfile(action.setup) ? 'summary' : 'choice' };
    case 'state': return { ...state, setup: action.setup };
    case 'draft': return { ...state, draft: action.draft };
    case 'failure': return { ...state, failure: action.failure };
    case 'checking': return { ...state, checking: action.checking };
    case 'busyProfile': return { ...state, busyProfile: action.id };
  }
}

export function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const requestGeneration = useRef(0);

  async function recheck() {
    const generation = ++requestGeneration.current;
    dispatch({ type: 'checking', checking: true });
    try {
      const setup = await recheckTargetState();
      if (requestGeneration.current === generation) dispatch({ type: 'state', setup });
    } catch (error) {
      if (requestGeneration.current === generation) {
        dispatch({ type: 'failure', failure: errorMessage(error, 'The active target could not be checked.') });
      }
    } finally {
      if (requestGeneration.current === generation) dispatch({ type: 'checking', checking: false });
    }
  }

  useEffect(() => {
    let current = true;
    void targetSetupState()
      .then((setup) => {
        if (!current) return;
        dispatch({ type: 'loaded', setup });
        if (selectedProfile(setup)) void recheck();
      })
      .catch((error: unknown) => {
        if (!current) return;
        dispatch({ type: 'failure', failure: errorMessage(error, 'VidXP Desktop could not load its target profiles.') });
        dispatch({ type: 'navigate', stage: 'choice' });
      });
    return () => {
      current = false;
      requestGeneration.current += 1;
    };
  }, []);

  async function retrieveState() {
    const setup = await targetSetupState();
    dispatch({ type: 'state', setup });
    return setup;
  }

  async function beginManaged() {
    try {
      const draft = await beginManagedSetup();
      dispatch({ type: 'draft', draft });
      dispatch({ type: 'navigate', stage: 'managed' });
    } catch (error) {
      dispatch({ type: 'failure', failure: errorMessage(error, 'Managed setup could not be started.') });
    }
  }

  async function cancelManaged() {
    await cancelManagedSetup();
    dispatch({ type: 'draft', draft: null });
    dispatch({ type: 'navigate', stage: state.setup && selectedProfile(state.setup) ? 'summary' : 'choice' });
  }

  async function selectSaved(id: string) {
    dispatch({ type: 'busyProfile', id });
    try {
      await selectTargetProfile(id);
      await retrieveState();
      dispatch({ type: 'navigate', stage: 'summary' });
    } catch (error) {
      dispatch({ type: 'failure', failure: errorMessage(error, 'The saved target could not be selected.') });
    } finally {
      dispatch({ type: 'busyProfile', id: null });
    }
  }

  async function forgetSaved(id: string, name: string) {
    if (!window.confirm(`Forget “${name}” from VidXP Desktop? The installation itself will not be changed.`)) return;
    dispatch({ type: 'busyProfile', id });
    try {
      const setup = await deleteTargetProfile(id);
      dispatch({ type: 'state', setup });
    } catch (error) {
      dispatch({ type: 'failure', failure: errorMessage(error, 'The saved target could not be forgotten.') });
    } finally {
      dispatch({ type: 'busyProfile', id: null });
    }
  }

  const profile = state.setup ? selectedProfile(state.setup) : null;
  return (
    <div className="appViewport">
      <TitleBar />
      <div className="appBackdrop"><div className="aurora auroraOne" aria-hidden="true" /><div className="aurora auroraTwo" aria-hidden="true" />
        <div className="mainScroller"><main className="appShell"><div className="contentFrame">
          {state.failure && <Alert icon={<IconAlertCircle />} color="red" title="Desktop issue" role="alert" mb="lg">{state.failure}</Alert>}
          {state.setup?.issues.map((issue) => <Alert key={`${issue.code}-${issue.message}`} color="yellow" title={issue.code} mb="md">{issue.message}</Alert>)}
          {state.stage === 'loading' && <div className="loadingState" role="status"><Loader size="sm" /> Restoring your VidXP target…</div>}
          {state.stage === 'choice' && <>
            {profile && <Button variant="subtle" leftSection={<IconArrowLeft size={17} />} onClick={() => dispatch({ type: 'navigate', stage: 'summary' })}>Back to active target</Button>}
            {state.setup && state.setup.profiles.length > 0 && <section className="setupPanel" aria-labelledby="saved-targets-title">
              <Group justify="space-between"><Title id="saved-targets-title" order={2} className="panelTitle">Saved targets</Title><Badge variant="light">{state.setup.profiles.length}</Badge></Group>
              <Stack gap="xs" mt="md">{state.setup.profiles.map((saved) => <Group key={saved.id} justify="space-between" className="savedTargetRow">
                <div><Text fw={650}>{saved.display_name}{saved.id === state.setup?.selected_profile_id ? ' · Active' : ''}</Text><Text size="xs" className="mutedText">{saved.kind === 'managed' ? 'Desktop managed' : 'Externally managed'} · {saved.display_executable}</Text></div>
                <Group><Button size="xs" variant="light" loading={state.busyProfile === saved.id} disabled={saved.id === state.setup?.selected_profile_id} onClick={() => void selectSaved(saved.id)}>Select</Button><Button size="xs" color="red" variant="subtle" leftSection={<IconTrash size={14} />} disabled={state.busyProfile !== null} onClick={() => void forgetSaved(saved.id, saved.display_name)}>Forget</Button></Group>
              </Group>)}</Stack>
            </section>}
            <TargetChoice value={state.choice} onChange={(choice) => dispatch({ type: 'choice', choice })} onContinue={() => dispatch({ type: 'navigate', stage: state.choice === 'existing_local' ? 'local' : 'managed-confirm' })} />
          </>}
          {state.stage === 'local' && <LocalSetup onBack={() => dispatch({ type: 'navigate', stage: 'choice' })} onActivated={async () => { await retrieveState(); dispatch({ type: 'navigate', stage: 'summary' }); }} />}
          {state.stage === 'managed-confirm' && <section aria-labelledby="managed-confirm-title"><Button variant="subtle" leftSection={<IconArrowLeft size={17} />} onClick={() => dispatch({ type: 'navigate', stage: 'choice' })}>Back</Button><div className="confirmationPanel"><ThemeIcon size={54} radius="xl" variant="light"><IconDownload size={28} /></ThemeIcon><Text className="eyebrow" mt="xl">CONFIRM MANAGED SETUP</Text><Title id="managed-confirm-title" order={1} className="pageTitle">Let VidXP Desktop manage a private runtime?</Title><Text className="lede centeredCopy">Your active target stays available until the replacement is installed, validated, and activated.</Text><Group justify="center" mt="xl"><Button variant="default" onClick={() => dispatch({ type: 'navigate', stage: 'choice' })}>Cancel</Button><Button onClick={() => void beginManaged()}>Continue to setup</Button></Group></div></section>}
          {state.stage === 'managed' && state.draft && <ManagedSetup draftId={state.draft.id} onBack={cancelManaged} onReady={async () => { await retrieveState(); dispatch({ type: 'draft', draft: null }); dispatch({ type: 'navigate', stage: 'summary' }); }} />}
          {state.stage === 'summary' && profile && <TargetSummary profile={profile} validationError={profile.validation_error} checking={state.checking} onRecheck={recheck} onManageManaged={() => dispatch({ type: 'navigate', stage: 'managed-confirm' })} onChooseAnother={() => { dispatch({ type: 'choice', choice: null }); dispatch({ type: 'navigate', stage: 'choice' }); }} />}
        </div><footer className="appFooter"><span>Target metadata stays private to VidXP Desktop.</span><span>Credentials are never stored in this setup profile.</span></footer></main></div>
      </div>
    </div>
  );
}
