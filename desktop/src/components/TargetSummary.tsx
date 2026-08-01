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
  const executable = profile.canonical_executable || profile.executable;

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
      {profile.can_launch_frontend === false && (
        <Alert color="yellow" mb="md" title="Browser interface not installed">
          This target is compatible, but it does not currently provide the supported browser interface.
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
          {profile.data_root && <><Text>Data root</Text><Code className="pathCode">{profile.data_root}</Code></>}
          {profile.last_validated_at && <><Text>Last checked</Text><strong>{new Date(profile.last_validated_at).toLocaleString()}</strong></>}
        </div>
        <Group justify="space-between" mt="xl">
          <Button variant="default" leftSection={<IconRefresh aria-hidden="true" size={17} />} onClick={onChooseAnother}>Choose another target</Button>
          <Button leftSection={<IconExternalLink aria-hidden="true" size={17} />} loading={opening} disabled={Boolean(validationError) || profile.can_launch_frontend === false} onClick={() => void open()}>Open VidXP</Button>
        </Group>
      </div>
    </section>
  );
}
