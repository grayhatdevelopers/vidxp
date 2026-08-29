import { Alert, Badge, Button, Code, Group, Loader, Stack, Text, ThemeIcon, Title } from '@mantine/core';
import { IconAlertCircle, IconArrowLeft, IconDownload, IconTrash } from '@tabler/icons-react';
import { useCallback, useEffect, useReducer, useRef } from 'react';

import { LocalSetup } from './components/LocalSetup';
import { ManagedSetup } from './components/ManagedSetup';
import { TargetChoice } from './components/TargetChoice';
import { TargetSummary } from './components/TargetSummary';
import { DesktopViewport } from './components/TitleBar';
import {
  beginManagedSetup,
  cancelManagedSetup,
  confirmForgetTarget,
  deleteTargetProfile,
  errorMessage,
  launchUi,
  recheckTargetState,
  selectTargetProfile,
  selectedProfile,
  targetSetupState,
  type ManagedSetupDraft,
  type TargetKind,
  type TargetSetupState,
} from './tauri';
import { useExclusiveOperation } from './useAsyncAction';

type Stage = 'loading' | 'choice' | 'local' | 'managed-confirm' | 'managed' | 'summary';
type AppOperation = 'startup-check' | 'recheck' | 'begin-managed' | 'cancel-managed' | 'select-profile' | 'forget-profile' | 'open-browser';

interface CompletionNotice {
  color: 'teal' | 'yellow';
  title: string;
  detail: string;
}

interface LifecycleState {
  stage: Stage;
  choice: TargetKind | null;
  setup: TargetSetupState | null;
  draft: ManagedSetupDraft | null;
  failure: string | null;
  completionNotice: CompletionNotice | null;
  premiereSetupRequested: boolean;
  operation: AppOperation | null;
  operationProfile: string | null;
}

type Action =
  | { type: 'navigate'; stage: Stage; choice?: TargetKind | null }
  | { type: 'choice'; choice: TargetKind | null }
  | { type: 'loaded'; setup: TargetSetupState }
  | { type: 'loadFailed'; failure: string }
  | { type: 'operationStarted'; operation: AppOperation; profileId?: string }
  | { type: 'operationFailed'; failure: string }
  | { type: 'operationSettled'; setup?: TargetSetupState; stage?: Stage; draft?: ManagedSetupDraft | null; completionNotice?: CompletionNotice | null; premiereSetupRequested?: boolean };

const initialState: LifecycleState = {
  stage: 'loading', choice: null, setup: null, draft: null, failure: null,
  completionNotice: null, premiereSetupRequested: false,
  operation: null, operationProfile: null,
};

function reducer(state: LifecycleState, action: Action): LifecycleState {
  switch (action.type) {
    case 'navigate':
      return { ...state, stage: action.stage, choice: action.choice === undefined ? state.choice : action.choice };
    case 'choice':
      return { ...state, choice: action.choice };
    case 'loaded':
      return { ...state, setup: action.setup, stage: selectedProfile(action.setup) ? 'summary' : 'choice', failure: null };
    case 'loadFailed':
      return { ...state, stage: 'choice', failure: action.failure };
    case 'operationStarted':
      return { ...state, operation: action.operation, operationProfile: action.profileId ?? null, failure: null };
    case 'operationFailed':
      return { ...state, operation: null, operationProfile: null, failure: action.failure };
    case 'operationSettled':
      return {
        ...state,
        setup: action.setup ?? state.setup,
        stage: action.stage ?? state.stage,
        draft: action.draft === undefined ? state.draft : action.draft,
        completionNotice: action.completionNotice === undefined ? state.completionNotice : action.completionNotice,
        premiereSetupRequested: action.premiereSetupRequested ?? state.premiereSetupRequested,
        operation: null,
        operationProfile: null,
        failure: null,
      };
  }
}

export function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const operations = useExclusiveOperation<AppOperation>();
  const startupLoad = useRef<Promise<TargetSetupState> | null>(null);

  const startOperation = useCallback((operation: AppOperation, profileId?: string): number | null => {
    const current = operations.begin(operation);
    if (current === null) return null;
    dispatch({ type: 'operationStarted', operation, profileId });
    return current;
  }, [operations]);

  const settleOperation = useCallback((current: number, action: Action) => {
    if (!operations.settle(current)) return;
    dispatch(action);
  }, [operations]);

  const recheck = useCallback(async (operation: 'startup-check' | 'recheck' = 'recheck') => {
    const current = startOperation(operation);
    if (current === null) return;
    try {
      const setup = await recheckTargetState();
      settleOperation(current, { type: 'operationSettled', setup });
    } catch (error) {
      settleOperation(current, {
        type: 'operationFailed',
        failure: errorMessage(error, 'VidXP could not check the active installation.'),
      });
    }
  }, [settleOperation, startOperation]);

  useEffect(() => {
    let mounted = true;
    const request = startupLoad.current ?? targetSetupState();
    startupLoad.current = request;
    void request
      .then((setup) => {
        if (!mounted) return;
        dispatch({ type: 'loaded', setup });
        if (selectedProfile(setup)) void recheck('startup-check');
      })
      .catch((error: unknown) => {
        if (!mounted) return;
        dispatch({
          type: 'loadFailed',
          failure: errorMessage(error, 'VidXP Desktop could not load your installations.'),
        });
      });
    return () => {
      mounted = false;
    };
  }, [recheck]);

  async function beginManaged(premiereSetupRequested = false) {
    const current = startOperation('begin-managed');
    if (current === null) return;
    try {
      const draft = await beginManagedSetup();
      settleOperation(current, { type: 'operationSettled', draft, stage: 'managed', premiereSetupRequested });
    } catch (error) {
      settleOperation(current, {
        type: 'operationFailed',
        failure: errorMessage(error, 'Managed setup could not be started.'),
      });
    }
  }

  async function cancelManaged() {
    if (!state.draft) return;
    const current = startOperation('cancel-managed');
    if (current === null) return;
    try {
      const setup = await cancelManagedSetup(state.draft.id);
      settleOperation(current, {
        type: 'operationSettled', setup, draft: null,
        stage: selectedProfile(setup) ? 'summary' : 'choice',
        premiereSetupRequested: false,
      });
    } catch (error) {
      settleOperation(current, {
        type: 'operationFailed',
        failure: errorMessage(error, 'Managed setup could not be cancelled.'),
      });
    }
  }

  async function selectSaved(id: string) {
    const current = startOperation('select-profile', id);
    if (current === null) return;
    try {
      const setup = await selectTargetProfile(id);
      settleOperation(current, { type: 'operationSettled', setup, stage: 'summary' });
    } catch (error) {
      settleOperation(current, {
        type: 'operationFailed',
        failure: errorMessage(error, 'The saved installation could not be selected.'),
      });
    }
  }

  async function forgetSaved(id: string, name: string) {
    if (!await confirmForgetTarget(name)) return;
    const current = startOperation('forget-profile', id);
    if (current === null) return;
    try {
      const setup = await deleteTargetProfile(id);
      settleOperation(current, { type: 'operationSettled', setup });
    } catch (error) {
      settleOperation(current, {
        type: 'operationFailed',
        failure: errorMessage(error, 'The saved installation could not be removed.'),
      });
    }
  }

  async function openBrowser() {
    const current = startOperation('open-browser');
    if (current === null) return;
    try {
      await launchUi();
      settleOperation(current, { type: 'operationSettled' });
    } catch (error) {
      settleOperation(current, {
        type: 'operationFailed',
        failure: errorMessage(error, 'VidXP could not be opened.'),
      });
    }
  }

  const profile = state.setup ? selectedProfile(state.setup) : null;
  const operationPending = state.operation !== null;
  return (
    <DesktopViewport>
      <div className="appBackdrop"><div className="aurora auroraOne" aria-hidden="true" /><div className="aurora auroraTwo" aria-hidden="true" />
        <div className="mainScroller"><main className="appShell"><div className="contentFrame">
          {state.failure && <Alert icon={<IconAlertCircle />} color="red" title="Desktop issue" role="alert" mb="lg">{state.failure}</Alert>}
          {state.completionNotice && <Alert color={state.completionNotice.color} title={state.completionNotice.title} mb="lg" withCloseButton onClose={() => dispatch({ type: 'operationSettled', completionNotice: null })}>{state.completionNotice.detail}</Alert>}
          {state.setup?.issues.map((issue) => <Alert key={`${issue.code}-${issue.message}`} color="yellow" title={issue.code} mb="md">{issue.message}</Alert>)}
          {state.stage === 'loading' && <div className="loadingState" role="status"><Loader size="sm" /> Loading your VidXP setup…</div>}
          {state.stage === 'choice' && <>
            {profile && <Button variant="subtle" leftSection={<IconArrowLeft size={17} />} disabled={operationPending} onClick={() => dispatch({ type: 'navigate', stage: 'summary' })}>Back to VidXP</Button>}
            {state.setup && state.setup.profiles.length > 0 && <section className="setupPanel" aria-labelledby="saved-targets-title">
              <Group justify="space-between"><Title id="saved-targets-title" order={2} className="panelTitle">Saved installations</Title><Badge variant="light">{state.setup.profiles.length}</Badge></Group>
              <Stack gap="xs" mt="md">{state.setup.profiles.map((saved) => <Group key={saved.id} justify="space-between" className="savedTargetRow">
                <div><Text fw={650}>{saved.display_name}{saved.id === state.setup?.selected_profile_id ? ' · Active' : ''}</Text><Text size="xs" className="mutedText">{saved.kind === 'managed' ? 'Managed by VidXP' : 'Managed by you'}</Text><details className="technicalDetails"><summary>Location</summary><Code className="pathCode">{saved.display_executable}</Code></details></div>
                <Group><Button size="xs" variant="light" loading={state.operationProfile === saved.id && state.operation === 'select-profile'} disabled={operationPending || saved.id === state.setup?.selected_profile_id} onClick={() => void selectSaved(saved.id)}>Select</Button>{saved.kind !== 'managed' && <Button size="xs" color="red" variant="subtle" leftSection={<IconTrash size={14} />} loading={state.operationProfile === saved.id && state.operation === 'forget-profile'} disabled={operationPending} onClick={() => void forgetSaved(saved.id, saved.display_name)}>Forget</Button>}</Group>
              </Group>)}</Stack>
            </section>}
            <TargetChoice value={state.choice} disabled={operationPending} onChange={(choice) => dispatch({ type: 'choice', choice })} onContinue={() => dispatch({ type: 'navigate', stage: state.choice === 'existing_local' ? 'local' : 'managed-confirm' })} />
          </>}
          {state.stage === 'local' && <LocalSetup onBack={() => dispatch({ type: 'navigate', stage: 'choice' })} onActivated={(setup) => dispatch({ type: 'operationSettled', setup, stage: 'summary' })} />}
          {state.stage === 'managed-confirm' && <section aria-labelledby="managed-confirm-title"><Button variant="subtle" leftSection={<IconArrowLeft size={17} />} disabled={operationPending} onClick={() => dispatch({ type: 'navigate', stage: 'choice' })}>Back</Button><div className="confirmationPanel"><ThemeIcon size={54} radius="xl" variant="light"><IconDownload size={28} /></ThemeIcon><Text className="eyebrow" mt="xl">SET UP VIDXP</Text><Title id="managed-confirm-title" order={1} className="pageTitle">Install and manage VidXP on this computer?</Title><Text className="lede centeredCopy">You choose the features. VidXP checks the new setup before switching to it, so your current installation stays available.</Text><Group justify="center" mt="xl"><Button variant="default" disabled={operationPending} onClick={() => dispatch({ type: 'navigate', stage: 'choice' })}>Cancel</Button><Button loading={state.operation === 'begin-managed'} disabled={operationPending} onClick={() => void beginManaged()}>Choose features</Button></Group></div></section>}
          {state.stage === 'managed' && state.draft && <ManagedSetup draftId={state.draft.id} selectedManagedRuntimeProfile={profile?.kind === 'managed' ? profile.managed_runtime_profile ?? null : null} premiereRequested={state.premiereSetupRequested} onBack={cancelManaged} onCommitted={(setup, completionNotice) => dispatch({ type: 'operationSettled', setup, draft: null, stage: 'summary', completionNotice, premiereSetupRequested: false })} />}
          {state.stage === 'summary' && profile && <TargetSummary profile={profile} validationError={profile.validation_error} checking={state.operation === 'startup-check' || state.operation === 'recheck'} opening={state.operation === 'open-browser'} operationPending={operationPending} onRecheck={() => recheck()} onManageManaged={() => void beginManaged()} onSetUpPremiere={() => void beginManaged(true)} onSetupChanged={(setup) => dispatch({ type: 'operationSettled', setup, stage: 'summary' })} onChooseAnother={() => dispatch({ type: 'navigate', stage: 'choice', choice: null })} onOpen={openBrowser} />}
        </div><footer className="appFooter"><span>Your VidXP settings stay on this computer.</span><span>Desktop only stops services that it starts.</span></footer></main></div>
      </div>
    </DesktopViewport>
  );
}
