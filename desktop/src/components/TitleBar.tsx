import { ActionIcon } from '@mantine/core';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { IconCopy, IconMinus, IconSquare, IconX } from '@tabler/icons-react';
import { useEffect, useState, type MouseEvent } from 'react';

const appWindow = getCurrentWindow();

export function TitleBar() {
  const [maximized, setMaximized] = useState(false);

  async function refreshMaximizedState() {
    setMaximized(await appWindow.isMaximized());
  }

  async function toggleMaximized() {
    await appWindow.toggleMaximize();
    await refreshMaximizedState();
  }

  function handleDoubleClick(event: MouseEvent<HTMLElement>) {
    if ((event.target as HTMLElement).closest('button')) return;
    void toggleMaximized();
  }

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void refreshMaximizedState();
    void appWindow.onResized(() => {
      void refreshMaximizedState();
    }).then((stopListening) => {
      unlisten = stopListening;
    });
    return () => unlisten?.();
  }, []);

  return (
    <header
      className="titleBar"
      data-tauri-drag-region
      onDoubleClick={handleDoubleClick}
    >
      <div className="titleBarIdentity" data-tauri-drag-region>
        <img
          className="titleBarLogo"
          data-testid="vidxp-logo"
          data-tauri-drag-region
          src="/icon.png"
          alt=""
        />
        <span data-tauri-drag-region>VidXP</span>
      </div>
      <div className="windowControls">
        <ActionIcon
          className="windowControl"
          variant="subtle"
          color="gray"
          radius={0}
          aria-label="Minimize window"
          title="Minimize"
          onClick={() => void appWindow.minimize()}
        >
          <IconMinus aria-hidden="true" size={17} stroke={1.7} />
        </ActionIcon>
        <ActionIcon
          className="windowControl"
          variant="subtle"
          color="gray"
          radius={0}
          aria-label={maximized ? 'Restore window' : 'Maximize window'}
          title={maximized ? 'Restore' : 'Maximize'}
          onClick={() => void toggleMaximized()}
        >
          {maximized ? (
            <IconCopy aria-hidden="true" size={14} stroke={1.7} />
          ) : (
            <IconSquare aria-hidden="true" size={13} stroke={1.7} />
          )}
        </ActionIcon>
        <ActionIcon
          className="windowControl closeControl"
          variant="subtle"
          color="gray"
          radius={0}
          aria-label="Close window"
          title="Close"
          onClick={() => void appWindow.close()}
        >
          <IconX aria-hidden="true" size={17} stroke={1.7} />
        </ActionIcon>
      </div>
    </header>
  );
}
