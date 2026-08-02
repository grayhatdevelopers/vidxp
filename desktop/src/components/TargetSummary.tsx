import { Alert, Badge, Button, Code, Group, Text, Title } from '@mantine/core';
import { IconExternalLink, IconRefresh, IconSettings } from '@tabler/icons-react';

import { type TargetError, type TargetProfile } from '../tauri';

interface TargetSummaryProps {
  profile: TargetProfile;
  validationError?: TargetError | null;
  checking?: boolean;
  operationPending?: boolean;
  opening?: boolean;
  onRecheck: () => Promise<void>;
  onManageManaged: () => void;
  onChooseAnother: () => void;
  onOpen: () => Promise<void>;
}

export function TargetSummary({ profile, validationError, checking, operationPending, opening, onRecheck, onManageManaged, onChooseAnother, onOpen }: TargetSummaryProps) {
  const executable = profile.display_executable;
  const desktopSurfaceUnavailable = !profile.frontend.launchable;
  const desktopAction = desktopSurfaceUnavailable
    ? profile.lifecycle_ownership === 'external'
      ? 'Unavailable · enable the browser surface in the externally managed installation'
      : 'Unavailable · return to managed setup to enable the browser surface'
    : 'Browser interface available';

  return (
    <section aria-labelledby="target-summary-title">
      <div className="sectionHeading compactHeading">
        <Text className="eyebrow">ACTIVE TARGET</Text>
        <Group justify="space-between" align="flex-end">
          <div>
            <Title id="target-summary-title" order={1} className="pageTitle">{profile.display_name}</Title>
            <Text className="lede">This target is restored and checked again whenever VidXP Desktop opens.</Text>
          </div>
          <Badge size="lg" variant="light" color={profile.kind === 'managed' ? 'violet' : 'teal'}>
            {profile.kind === 'managed' ? 'Desktop managed' : 'Externally managed'}
          </Badge>
        </Group>
      </div>

      {desktopSurfaceUnavailable && (
        <Alert color="yellow" mb="md" title="Action required · no usable desktop surface">
          <Text size="sm"><strong>Usable:</strong> {profile.lifecycle_ownership === 'external' ? 'This externally managed installation remains available through its own command-line and package-managed workflows.' : 'The managed runtime remains configured for its installed non-browser capabilities.'}</Text>
          <Text size="sm" mt="xs"><strong>Missing:</strong> {profile.frontend?.message || 'The supported browser interface cannot currently be launched.'}</Text>
          <Text size="sm" mt="xs"><strong>How to enable it:</strong> {profile.frontend?.remediation || (profile.lifecycle_ownership === 'external' ? "Use this installation's own package-management workflow to enable the VidXP browser interface, then return here and revalidate." : 'Return to managed setup and select the browser surface.')}</Text>
        </Alert>
      )}
      {validationError && (
        <Alert color="red" title={validationError.code} role="alert" mb="md">
          {validationError.message}
        </Alert>
      )}

      <div className="summaryPanel">
        <div className="summaryGrid">
          <Text>Ownership</Text><strong>{profile.lifecycle_ownership === 'external' ? 'You manage this installation' : 'VidXP Desktop manages this runtime'}</strong>
          {executable && <><Text>Executable</Text><Code className="pathCode">{executable}</Code></>}
          {profile.observed_vidxp_version && <><Text>VidXP version</Text><strong>{profile.observed_vidxp_version}</strong></>}
          <Text>Data root</Text><Code className="pathCode">{profile.display_data_root}</Code>
          {profile.last_validated_at && <><Text>Last checked</Text><strong>{new Date(profile.last_validated_at).toLocaleString()}</strong></>}
          <Text>Desktop action</Text><strong>{desktopAction}</strong>
        </div>
        <Group justify="space-between" mt="xl">
          <Group>
            <Button variant="default" leftSection={<IconRefresh aria-hidden="true" size={17} />} disabled={operationPending} onClick={onChooseAnother}>Manage targets</Button>
            <Button variant="subtle" leftSection={<IconRefresh aria-hidden="true" size={17} />} loading={checking} disabled={operationPending && !checking} onClick={() => void onRecheck()}>Recheck target</Button>
            {profile.kind === 'managed' && <Button variant="subtle" leftSection={<IconSettings aria-hidden="true" size={17} />} disabled={operationPending} onClick={onManageManaged}>Manage setup</Button>}
          </Group>
          <Button leftSection={<IconExternalLink aria-hidden="true" size={17} />} loading={opening} disabled={Boolean(validationError) || desktopSurfaceUnavailable || operationPending} onClick={() => void onOpen()}>Open VidXP</Button>
        </Group>
      </div>
    </section>
  );
}
