/**
 * Bilgi düğmesi + açılır kutu: tıklama ve klavye ile açılır (Enter/Boşluk), Esc ile kapanır, odak kutuya taşınır
 * ve geri döner. İçeriği bağlantı içerebildiği için Tooltip değil Popover.
 */
import { ActionIcon, Popover, ScrollArea, type FloatingPosition } from '@mantine/core';
import { useState, type ReactNode } from 'react';

import { IconInfo } from './icons.tsx';

export interface InfoPopoverProps {
  /** düğmenin erişilebilir adı ("… açıklaması ve kaynakları") */
  label: string;
  children: ReactNode;
  position?: FloatingPosition;
  width?: number | string;
  size?: 'xs' | 'sm' | 'md';
  /** düğme yerine özel tetikleyici */
  target?: ReactNode;
  testId?: string;
}

export function InfoPopover({ label, children, position = 'right-start', width = 'min(24rem, 92vw)', size = 'sm', target, testId }: InfoPopoverProps) {
  const [opened, setOpened] = useState(false);
  return (
    <Popover
      opened={opened}
      onChange={setOpened}
      position={position}
      width={width}
      shadow="md"
      withArrow
      trapFocus
      returnFocus
      withinPortal
      zIndex={400}
      middlewares={{ flip: true, shift: { padding: 8 }, inline: false }}
    >
      <Popover.Target>
        {target ?? (
          <ActionIcon
            variant="subtle"
            color="gray"
            size={size}
            aria-label={label}
            aria-expanded={opened}
            onClick={() => setOpened((o) => !o)}
            data-testid={testId}
          >
            <IconInfo size={size === 'xs' ? 14 : 16} />
          </ActionIcon>
        )}
      </Popover.Target>
      <Popover.Dropdown p={0} role="dialog" aria-label={label}>
        <ScrollArea.Autosize mah="min(70vh, 34rem)" type="auto" offsetScrollbars>
          <div style={{ padding: 'var(--mantine-spacing-sm)' }}>{children}</div>
        </ScrollArea.Autosize>
      </Popover.Dropdown>
    </Popover>
  );
}
