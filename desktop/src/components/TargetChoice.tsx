import { Button, Group, Radio, Stack, Text, ThemeIcon, Title } from '@mantine/core';
import { IconDeviceDesktop, IconDownload } from '@tabler/icons-react';

import type { TargetKind } from '../tauri';

interface TargetChoiceProps {
  value: TargetKind | null;
  disabled?: boolean;
  onChange: (value: TargetKind) => void;
  onContinue: () => void;
}

const targets = [
  {
    value: 'existing_local' as const,
    title: 'Use an existing installation',
    description: 'Connect to VidXP already installed on this computer. Nothing is downloaded.',
    detail: 'You stay in control of updates and removal.',
    icon: IconDeviceDesktop,
  },
  {
    value: 'managed' as const,
    title: 'Set up VidXP for me',
    description: 'Create a private runtime managed by this desktop app.',
    detail: 'Downloads Python, VidXP, and only the capabilities you choose.',
    icon: IconDownload,
  },
];

export function TargetChoice({ value, disabled, onChange, onContinue }: TargetChoiceProps) {
  return (
    <section aria-labelledby="target-choice-title">
      <div className="sectionHeading">
        <Title id="target-choice-title" order={1} className="displayTitle">
          Where should VidXP run?
        </Title>
        <Text className="lede">
          Choose before anything is installed. You can change the selected target later.
        </Text>
      </div>

      <Radio.Group
        aria-label="VidXP target"
        value={value}
        onChange={(next) => onChange(next as TargetKind)}
        readOnly={disabled}
      >
        <div className="targetGrid">
          {targets.map((target) => {
            const Icon = target.icon;
            return (
              <Radio.Card className="targetCard" value={target.value} key={target.value}>
                <Stack gap="lg" h="100%">
                  <Group justify="space-between" align="flex-start" wrap="nowrap">
                    <ThemeIcon className="targetIcon" size={48} radius="md" variant="light">
                      <Icon aria-hidden="true" size={25} stroke={1.7} />
                    </ThemeIcon>
                    <Radio.Indicator aria-hidden="true" />
                  </Group>
                  <div>
                    <Title order={2} className="cardTitle">
                      {target.title}
                    </Title>
                    <Text className="cardDescription">{target.description}</Text>
                  </div>
                  <Text className="cardDetail" mt="auto">
                    {target.detail}
                  </Text>
                </Stack>
              </Radio.Card>
            );
          })}
        </div>
      </Radio.Group>

      <Group justify="flex-end" mt="xl">
        <Button size="md" disabled={!value || disabled} onClick={onContinue}>
          Continue
        </Button>
      </Group>
    </section>
  );
}
