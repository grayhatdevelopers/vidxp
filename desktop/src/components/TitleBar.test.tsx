import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const windowMocks = vi.hoisted(() => ({
  close: vi.fn(),
  isMaximized: vi.fn(),
  minimize: vi.fn(),
  onResized: vi.fn(),
  toggleMaximize: vi.fn(),
}));

vi.mock('@tauri-apps/api/window', () => ({ getCurrentWindow: () => windowMocks }));

import { DesktopViewport } from './TitleBar';

describe('desktop viewport platform layout contract', () => {
  beforeEach(() => {
    windowMocks.isMaximized.mockResolvedValue(false);
    windowMocks.onResized.mockResolvedValue(vi.fn());
  });

  it('renders the Windows custom title bar in the two-row grid contract', () => {
    render(
      <MantineProvider env="test">
        <DesktopViewport platform="Windows Win32"><main>Content</main></DesktopViewport>
      </MantineProvider>,
    );
    const viewport = screen.getByTestId('desktop-viewport');
    expect(viewport).toHaveClass('windowsCustomChrome');
    expect(viewport.children).toHaveLength(2);
    expect(viewport.lastElementChild).toBe(screen.getByText('Content'));
    expect(screen.getByRole('banner')).toBeVisible();
    expect(screen.getByText('Content')).toBeVisible();
  });

  it('renders native-platform content in the single full-height row contract', () => {
    render(
      <MantineProvider env="test">
        <DesktopViewport platform="MacIntel"><main>Content</main></DesktopViewport>
      </MantineProvider>,
    );
    const viewport = screen.getByTestId('desktop-viewport');
    expect(viewport).toHaveClass('nativePlatformChrome');
    expect(viewport.children).toHaveLength(1);
    expect(viewport.firstElementChild).toBe(screen.getByText('Content'));
    expect(screen.queryByRole('banner')).not.toBeInTheDocument();
    expect(screen.getByText('Content')).toBeVisible();
  });
});
