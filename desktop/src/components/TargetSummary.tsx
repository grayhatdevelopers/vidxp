import { Alert, Badge, Button, Code, Group, Text, Title } from '@mantine/core';
import { IconAlertCircle, IconExternalLink, IconRefresh } from '@tabler/icons-react';
import { useState } from 'react';

import { errorMessage, launchUi, type TargetError, type TargetProfile } from '../tauri';

interface TargetSummaryProps {
  profile: TargetProfile;
  validationError?: TargetError | null;
  notice?: string | null;
  onChooseAnother: () => void;
}

export function TargetSummary({ profile, validationError, notice, onChooseAnother }: TargetSummaryProps) {
  const [opening, setOpening] = useState(false);
  const [openError, setOpenError] = useState<string | null>(null);
  const executable = profile.display_executable || profile.canonical_executable || profile.executable;
  const desktopSurfaceUnavailable = profile.can_launch_frontend === false;
  const desktopAction = desktopSurfaceUnavailable
    ? profile.lifecycle_ownership === 'external'
      ? 'Unavailable · enable the browser surface in the externally managed installation'
      : 'Unavailable · return to managed setup to enable the browser surface'
    : 'Browser interface available';

  async function open() {
    setOpening(true);
    setOpenError(null);
    try {
      await launchUi();
    } catch (error) {
      setOpenError(errorMessage(error, 'VidXP could not be opened.'));
      setOpening(false);
    }
  }

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

      {notice && <Alert color="blue" mb="md">{notice}</Alert>}
      {desktopSurfaceUnavailable && (
        <Alert color="yellow" mb="md" title="Action required · no usable desktop surface">
          <Text size="sm"><strong>Usable:</strong> {profile.lifecycle_ownership === 'external' ? 'This externally managed installation remains available through its own command-line and package-managed workflows.' : 'The managed runtime remains configured for its installed non-browser capabilities.'}</Text>
          <Text size="sm" mt="xs"><strong>Missing:</strong> {profile.frontend?.message || 'The supported browser interface cannot currently be launched.'}</Text>
          <Text size="sm" mt="xs"><strong>How to enable it:</strong> {profile.frontend?.remediation || (profile.lifecycle_ownership === 'external' ? "Use this installation's own package-management workflow to enable the VidXP browser interface, then return here and revalidate." : 'Return to managed setup and select the browser surface.')}</Text>
        </Alert>
      )}
      {(validationError || openError) && (
        <Alert icon={<IconAlertCircle aria-hidden="true" />} color="red" title={validationError?.code || 'Target unavailable'} role="alert" mb="md">
          {validationError?.message || validationError?.detail || openError}
          {validationError?.action && <Text fw={600} mt="xs">{validationError.action}</Text>}
        </Alert>
      )}

      <div className="summaryPanel">
        <div className="summaryGrid">
          <Text>Ownership</Text><strong>{profile.lifecycle_ownership === 'external' ? 'You manage this installation' : 'VidXP Desktop manages this runtime'}</strong>
          {executable && <><Text>Executable</Text><Code className="pathCode">{executable}</Code></>}
          {profile.vidxp_version && <><Text>VidXP version</Text><strong>{profile.vidxp_version}</strong></>}
          {profile.data_root && <><Text>Data root</Text><Code className="pathCode">{profile.display_data_root || profile.data_root}</Code></>}
          {profile.last_validated_at && <><Text>Last checked</Text><strong>{new Date(profile.last_validated_at).toLocaleString()}</strong></>}
          <Text>Desktop action</Text><strong>{desktopAction}</strong>
        </div>
        <Group justify="space-between" mt="xl">
          <Button variant="default" leftSection={<IconRefresh aria-hidden="true" size={17} />} onClick={onChooseAnother}>Choose another target</Button>
          <Button leftSection={<IconExternalLink aria-hidden="true" size={17} />} loading={opening} disabled={Boolean(validationError) || desktopSurfaceUnavailable} onClick={() => void open()}>Open VidXP</Button>
        </Group>
      </div>
    </section>
  );
}
