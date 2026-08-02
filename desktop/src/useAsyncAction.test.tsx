import { Button, MantineProvider, Text } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StrictMode } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { useAsyncAction } from './useAsyncAction';

function Harness({ action }: { action: () => Promise<void> }) {
  const result = useAsyncAction('checking', action);
  return <>
    <Button onClick={() => void result.run()}>Run</Button>
    <Text role="status">{result.status}</Text>
  </>;
}

describe('useAsyncAction', () => {
  it('settles after the StrictMode effect replay', async () => {
    let resolve!: () => void;
    const action = vi.fn(() => new Promise<void>((done) => { resolve = done; }));
    const user = userEvent.setup();
    render(
      <StrictMode>
        <MantineProvider env="test"><Harness action={action} /></MantineProvider>
      </StrictMode>,
    );

    await user.click(screen.getByRole('button', { name: 'Run' }));
    expect(screen.getByRole('status')).toHaveTextContent('checking');
    resolve();
    expect(await screen.findByText('succeeded')).toBeVisible();
    expect(action).toHaveBeenCalledTimes(1);
  });
});
