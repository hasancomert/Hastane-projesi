/**
 * Harita ipucu: imlecin yanında küçük kart (Türkçe, biçimlenmiş sayılar). İçerik tooltipContent.ts'ten; konum
 * kapsayıcının kenarına taşarsa imlecin öbür yanına geçer. İşaretçi olaylarını almaz (haritayı engellemez).
 */
import { Paper, Text } from '@mantine/core';
import { useLayoutEffect, useMemo, useRef, useState } from 'react';

import { selectCanSolve, selectGrid, useApp } from '../store/appStore.ts';
import { classColor } from '../theme/palette.ts';
import { gridIndexOf } from './gridIndex.ts';
import { useMapUi } from './mapUiStore.ts';
import { tooltipContent } from './tooltipContent.ts';
import { useShownSolution } from './useShownSolution.ts';

const TONE: Record<string, string> = { critical: 'var(--mantine-color-red-7)', good: 'var(--mantine-color-green-8)', warning: 'var(--mantine-color-yellow-8)' };
const TONE_DARK: Record<string, string> = { critical: 'var(--mantine-color-red-4)', good: 'var(--mantine-color-green-4)', warning: 'var(--mantine-color-yellow-4)' };

export function MapTooltip({ containerRef }: { containerRef: React.RefObject<HTMLElement | null> }) {
  const hover = useMapUi((s) => s.hover);
  const tool = useMapUi((s) => s.tool);
  const scheme = useApp((s) => s.colorScheme);
  const grid = useApp(selectGrid);
  const gridKm = useApp((s) => s.problem.gridKm);
  const { solution } = useShownSolution();
  const meta = useApp((s) => s.meta);
  const visible = useApp((s) => s.layers);
  const locks = useApp((s) => s.problem.locks);
  const canLock = useApp(selectCanSolve);
  const ref = useRef<HTMLDivElement | null>(null);
  const [flip, setFlip] = useState<[boolean, boolean]>([false, false]);

  const content = useMemo(() => {
    if (!hover) return null;
    const gi = grid ? gridIndexOf(grid, gridKm) : null;
    try {
      return tooltipContent({ target: hover.target, grid, gridIndex: gi, solution, meta, visible, locks, tool, canLock });
    } catch {
      return null;
    }
  }, [hover, grid, gridKm, solution, meta, visible, locks, tool, canLock]);

  useLayoutEffect(() => {
    const el = ref.current;
    const box = containerRef.current;
    if (!el || !box || !hover) return;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const fx = hover.x + 16 + w > box.clientWidth - 8;
    const fy = hover.y + 16 + h > box.clientHeight - 8;
    setFlip((f) => (f[0] === fx && f[1] === fy ? f : [fx, fy]));
  }, [hover, content, containerRef]);

  if (!hover || !content) return null;
  const tones = scheme === 'dark' ? TONE_DARK : TONE;
  const left = flip[0] ? Math.max(4, hover.x - 16 - (ref.current?.offsetWidth ?? 0)) : hover.x + 16;
  const top = flip[1] ? Math.max(4, hover.y - 16 - (ref.current?.offsetHeight ?? 0)) : hover.y + 16;
  return (
    <Paper
      ref={ref}
      className="map-tooltip"
      shadow="md"
      radius="md"
      withBorder
      p="xs"
      data-export="skip"
      data-testid="map-tooltip"
      style={{ position: 'absolute', left, top, zIndex: 6, pointerEvents: 'none', maxWidth: '22rem', minWidth: '13rem' }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {content.classCode && (
          <span aria-hidden style={{ width: 12, height: 12, borderRadius: 6, flex: '0 0 auto', background: classColor(content.classCode, scheme), boxShadow: '0 0 0 1.5px var(--mantine-color-body)' }} />
        )}
        <Text fw={700} size="sm" lh={1.3}>
          {content.title}
        </Text>
      </div>
      {content.subtitle && (
        <Text size="xs" c="dimmed" mb={4}>
          {content.subtitle}
        </Text>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', columnGap: 12, rowGap: 2, marginTop: 2 }}>
        {content.rows.map((r) => (
          <div key={r.label} style={{ display: 'contents' }}>
            <Text size="xs" c="dimmed">
              {r.label}
            </Text>
            <Text size="xs" fw={600} ta="right" style={{ color: r.tone ? tones[r.tone] : undefined, fontVariantNumeric: 'tabular-nums' }}>
              {r.tone === 'critical' ? '⚠ ' : ''}
              {r.value}
            </Text>
          </div>
        ))}
      </div>
      {content.hint && (
        <Text size="xs" c="dimmed" mt={6} fs="italic">
          {content.hint}
        </Text>
      )}
    </Paper>
  );
}
