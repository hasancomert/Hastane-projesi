import { ActionIcon, Tooltip } from '@mantine/core';

import { useApp } from '../store/appStore.ts';

/** Açık / koyu tema düğmesi (tercih tarayıcıda saklanır). */
export function ThemeToggle() {
  const scheme = useApp((s) => s.colorScheme);
  const toggle = useApp((s) => s.toggleColorScheme);
  const label = scheme === 'dark' ? 'Açık temaya geç' : 'Koyu temaya geç';
  return (
    <Tooltip label={label}>
      <ActionIcon variant="default" size="lg" onClick={toggle} aria-label={label}>
        <span aria-hidden style={{ fontSize: '1.05rem', lineHeight: 1 }}>
          {scheme === 'dark' ? '☀' : '☾'}
        </span>
      </ActionIcon>
    </Tooltip>
  );
}
