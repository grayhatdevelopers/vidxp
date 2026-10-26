import { Alert, Button, Code, Group, Loader, PasswordInput, Stack, Text, TextInput, Title } from '@mantine/core';
import { IconArrowLeft, IconCheck, IconCloud } from '@tabler/icons-react';
import { useState } from 'react';

import { activateRemoteTarget, errorMessage, inspectRemoteTarget, type RemoteTargetInspection, type TargetSetupState } from '../tauri';

interface RemoteSetupProps {
  onBack: () => void;
  onActivated: (setup: TargetSetupState) => void;
}

export function RemoteSetup({ onBack, onActivated }: RemoteSetupProps) {
  const [url, setUrl] = useState('http://');
  const [name, setName] = useState('Remote VidXP');
  const [authorization, setAuthorization] = useState('');
  const [inspection, setInspection] = useState<RemoteTargetInspection | null>(null);
  const [busy, setBusy] = useState<'check' | 'connect' | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  async function check() {
    setBusy('check');
    setFailure(null);
    try {
      setInspection(await inspectRemoteTarget(url.trim(), authorization.trim() || undefined));
    } catch (error) {
      setFailure(errorMessage(error, 'The remote VidXP server could not be checked.'));
    } finally {
      setBusy(null);
    }
  }

  async function connect() {
    if (!inspection?.compatible) return;
    setBusy('connect');
    setFailure(null);
    try {
      onActivated(await activateRemoteTarget(url.trim(), name.trim() || 'Remote VidXP', authorization.trim() || undefined));
    } catch (error) {
      setFailure(errorMessage(error, 'The remote VidXP server could not be connected.'));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section aria-labelledby="remote-setup-title">
      <Button variant="subtle" leftSection={<IconArrowLeft size={17} />} onClick={onBack} disabled={busy !== null}>Back</Button>
      <div className="sectionHeading compactHeading">
        <Text className="eyebrow">REMOTE SERVER</Text>
        <Title id="remote-setup-title" order={1} className="pageTitle">Connect to a VidXP server</Title>
        <Text className="lede">Desktop checks the server before saving it. Remote installation and service management stay on that server.</Text>
      </div>
      {failure && <Alert color="red" title="Could not continue" role="alert" mb="md">{failure}</Alert>}
      <div className="setupPanel">
        <Stack gap="md">
          <TextInput label="Server URL" placeholder="https://vidxp.example.com" value={url} onChange={(event) => setUrl(event.currentTarget.value)} disabled={busy !== null} />
          <TextInput label="Connection name" description="Used only to identify this server in Desktop." value={name} onChange={(event) => setName(event.currentTarget.value)} disabled={busy !== null} />
          <PasswordInput label="Authorization header value" description="Optional. Enter the value advertised by the server challenge, for example Bearer <token>." value={authorization} onChange={(event) => setAuthorization(event.currentTarget.value)} disabled={busy !== null} />
          <Group justify="flex-end"><Button leftSection={<IconCloud size={17} />} loading={busy === 'check'} disabled={busy !== null || !url.trim()} onClick={() => void check()}>Check server</Button></Group>
        </Stack>
      </div>
      {inspection && <div className="setupPanel">
        <Group justify="space-between"><Title order={2} className="panelTitle">Server check</Title><IconCheck aria-label={inspection.compatible ? 'Compatible' : 'Not compatible'} color={inspection.compatible ? 'teal' : 'red'} /></Group>
        <Text mt="sm">{inspection.message}</Text>
        {inspection.requires_authentication && <Alert mt="md" color="yellow" title="Authentication required">The server advertises {inspection.authentication_scheme ?? 'an authentication'} through its HTTP challenge. Enter the complete authorization header value, then check again.</Alert>}
        {inspection.compatible && <div className="summaryGrid" style={{ marginTop: '1rem' }}>
          <Text>Capabilities</Text><strong>{inspection.capabilities.join(', ') || 'None reported'}</strong>
          <Text>Repository</Text><Code>{inspection.repository ?? 'Not reported'}</Code>
          <Text>Model/config</Text><strong>{inspection.model_config ?? 'Not reported'}</strong>
          <Text>Job readiness</Text><strong>{inspection.job_ready ? 'Ready' : 'Needs attention'}</strong>
        </div>}
        <Group justify="flex-end" mt="lg"><Button color="teal" loading={busy === 'connect'} disabled={busy !== null || !inspection.compatible} onClick={() => void connect()}>Use this server</Button></Group>
      </div>}
      {busy === 'connect' && <div className="statusRegion" role="status"><Loader size="xs" /> Saving remote target…</div>}
    </section>
  );
}
