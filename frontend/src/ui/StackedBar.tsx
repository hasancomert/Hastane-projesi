/**
 * Yatay yığılmış çubuk (parça–bütün): segmentler arasında 2 px yüzey boşluğu, taban (sol) köşeli, uç (sağ) 4 px
 * yuvarlak; her segmentin üzerine gelince değer ipucu. Renk tek başına anlam taşımaz: yanında `SegmentLegend`
 * (renk karesi + etiket + değer + pay) her zaman gösterilir — tablo görünümü yerine geçer.
 */
import { Group, Text, Tooltip } from '@mantine/core';
import type { ReactNode } from 'react';

import { fmtPercent } from '../format/number.ts';

export interface BarSegment {
  key: string;
  label: string;
  value: number;
  color: string;
  /** ipucunda değerin biçimli hali (yoksa yalnız pay) */
  valueText?: string;
  description?: string;
}

export interface StackedBarProps {
  segments: BarSegment[];
  /** toplam (yoksa segmentlerin toplamı); segment payları buna göre */
  total?: number;
  height?: number;
  /** ekran okuyucu için çubuğun özeti */
  ariaLabel: string;
  /** sıfır/negatif segmentleri gizle (varsayılan true) */
  hideEmpty?: boolean;
  /** dolmayan kısım (ör. talep/kapasite göstergesi) için iz rengi */
  track?: boolean;
  /** ipucunda değerden sonra yazılacak birim (valueText birimsizse) */
  unit?: string;
}

export function StackedBar({ segments, total, height = 12, ariaLabel, hideEmpty = true, track = false, unit }: StackedBarProps) {
  const shown = hideEmpty ? segments.filter((s) => s.value > 0) : segments;
  const sum = shown.reduce((a, s) => a + Math.max(0, s.value), 0);
  const denom = total && total > 0 ? Math.max(total, sum) : sum;
  return (
    <div
      role="img"
      aria-label={ariaLabel}
      style={{
        display: 'flex',
        gap: 2,
        height,
        width: '100%',
        borderRadius: '0 4px 4px 0',
        overflow: 'hidden',
        background: track ? 'var(--mantine-color-default-border)' : undefined,
      }}
    >
      {denom > 0 &&
        shown.map((s, i) => {
          const share = Math.max(0, s.value) / denom;
          const last = i === shown.length - 1 && !track;
          return (
            <Tooltip
              key={s.key}
              label={
                <span>
                  <b>{s.label}</b>: {s.valueText ? `${s.valueText}${unit ? ` ${unit}` : ''} · ` : ''}
                  {fmtPercent(share)}
                  {s.description ? <><br />{s.description}</> : null}
                </span>
              }
              openDelay={80}
            >
              <span
                style={{
                  flex: `0 0 calc(${(share * 100).toFixed(4)}% - ${i === 0 ? 0 : 2}px)`,
                  minWidth: share > 0 ? 2 : 0,
                  background: s.color,
                  borderRadius: last ? '0 4px 4px 0' : 0,
                }}
              />
            </Tooltip>
          );
        })}
    </div>
  );
}

/** Renk karesi (lejant ve kartlarda kimlik işareti). */
export function Swatch({ color, size = 10, round = false }: { color: string; size?: number; round?: boolean }) {
  return (
    <span
      aria-hidden
      style={{
        display: 'inline-block',
        width: size,
        height: size,
        borderRadius: round ? size : 2,
        background: color,
        flexShrink: 0,
        boxShadow: '0 0 0 1.5px var(--mantine-color-body)',
      }}
    />
  );
}

export interface SegmentLegendProps {
  segments: BarSegment[];
  total?: number;
  /** sıfır segmentleri ayrı bir dipnotta topla */
  collapseZero?: boolean;
  /** iki sütun (dar kartlar için tek sütun) */
  columns?: 1 | 2;
  footer?: ReactNode;
  size?: 'xs' | 'sm';
  /** değer sütununun birimi (değerler birimsiz yazıldığında başlıkta bir kez: "milyar TL") */
  unitCaption?: string;
}

/** Lejant tablosu: renk · etiket · değer · pay (sayılar tabular-nums ile hizalı). */
export function SegmentLegend({ segments, total, collapseZero = true, columns = 1, footer, size = 'xs', unitCaption }: SegmentLegendProps) {
  const sum = total ?? segments.reduce((a, s) => a + Math.max(0, s.value), 0);
  const nonZero = collapseZero ? segments.filter((s) => s.value > 0) : segments;
  const zero = collapseZero ? segments.filter((s) => !(s.value > 0)) : [];
  return (
    <div>
      {unitCaption && (
        <Text size="xs" c="dimmed" ta="right" mb={2}>
          {unitCaption} · pay
        </Text>
      )}
      <div
        role="table"
        aria-label="Kırılım tablosu"
        style={{ display: 'grid', gridTemplateColumns: columns === 2 ? '1fr 1fr' : '1fr', columnGap: '0.75rem', rowGap: 2 }}
      >
        {nonZero.map((s) => (
          <div role="row" key={s.key} style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
            <Swatch color={s.color} />
            <Text role="cell" size={size} style={{ flex: 1, minWidth: 0 }} truncate="end" title={s.description}>
              {s.label}
            </Text>
            {s.valueText && (
              <Text role="cell" size={size} fw={600} style={{ fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>
                {s.valueText}
              </Text>
            )}
            <Text role="cell" size={size} c="dimmed" style={{ fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap', minWidth: '3.4em', textAlign: 'right' }}>
              {sum > 0 ? fmtPercent(Math.max(0, s.value) / sum) : '—'}
            </Text>
          </div>
        ))}
      </div>
      {(zero.length > 0 || footer) && (
        <Group gap={4} mt={4}>
          {zero.length > 0 && (
            <Text size="xs" c="dimmed">
              Sıfır: {zero.map((s) => s.label.toLocaleLowerCase('tr-TR')).join(', ')} (bu ayarlarda modele girmiyor).
            </Text>
          )}
          {footer}
        </Group>
      )}
    </div>
  );
}
