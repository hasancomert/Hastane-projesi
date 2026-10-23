/**
 * KPI kutucuğu (stat tile): etiket · değer (+ birim) · ipucu · isteğe bağlı küçük grafik. Değer büyük ve orantılı
 * rakamlarla; birim değerin yanında daha küçük. Metin her zaman mürekkep tonlarında (veri rengi yalnız işaretlerde).
 */
import { Paper, Text } from '@mantine/core';
import type { ReactNode } from 'react';

export interface KpiTileProps {
  label: ReactNode;
  value?: ReactNode;
  unit?: ReactNode;
  hint?: ReactNode;
  children?: ReactNode;
  /** iki sütunu kaplasın (ızgarada) */
  wide?: boolean;
  /** sağ üst köşe (ör. bilgi düğmesi) */
  aside?: ReactNode;
  testId?: string;
}

export function KpiTile({ label, value, unit, hint, children, wide, aside, testId }: KpiTileProps) {
  return (
    <Paper
      withBorder
      radius="md"
      p="xs"
      data-testid={testId}
      style={{ gridColumn: wide ? '1 / -1' : undefined, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 4 }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 4 }}>
        <Text size="xs" c="dimmed" style={{ lineHeight: 1.3 }}>
          {label}
        </Text>
        {aside}
      </div>
      {value !== undefined && (
        <Text component="div" fw={650} style={{ lineHeight: 1.15, fontSize: '1.25rem', overflowWrap: 'anywhere' }}>
          {value}
          {unit && (
            <Text component="span" size="sm" fw={500} c="dimmed" ml={4}>
              {unit}
            </Text>
          )}
        </Text>
      )}
      {hint && (
        <Text size="xs" c="dimmed" style={{ lineHeight: 1.35 }}>
          {hint}
        </Text>
      )}
      {children}
    </Paper>
  );
}
