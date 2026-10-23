/**
 * Kullanıcının kilitleri (şartname 6.5 "kilitle / yasakla"): 'aç' kilidi (bu hücrede hastane zorunlu) ve 'yasak'
 * (bu hücrede hastane açılamaz). Girdi depodaki `problem.locks` — henüz çözülmemiş kilit de hemen görünür.
 * Kilit bir DURUMDUR: durum renkleri (good / critical) + simge (+ / ×) + lejant etiketi; renk tek başına anlam
 * taşımaz. Hücre çerçevesi coğrafi, simge ekran boyutunda (uzaktan da görünür). Aday katmanı kapalıyken de çizilir.
 */
import type { Layer } from '@deck.gl/core';
import { PolygonLayer, TextLayer } from '@deck.gl/layers';

import { hexToRgba } from '../../theme/palette.ts';
import { FONT_STACK } from '../../theme/theme.ts';
import type { GridIndex } from '../gridIndex.ts';
import { LOCK_CLOSED_HEX, LOCK_OPEN_HEX, surfaceRing } from '../mapColors.ts';
import { memoLast } from './memo.ts';
import type { LayerBuilder } from './types.ts';

interface LockCell {
  i: number;
  kind: 'open' | 'closed';
}

const lockCells = memoLast((gi: GridIndex, open: string[], closed: string[]): LockCell[] => {
  const out: LockCell[] = [];
  for (const id of open) {
    const i = gi.byId.get(id);
    if (i !== undefined) out.push({ i, kind: 'open' });
  }
  for (const id of closed) {
    const i = gi.byId.get(id);
    if (i !== undefined) out.push({ i, kind: 'closed' });
  }
  return out;
});

export const locksLayer: LayerBuilder = (ctx) => {
  const gi = ctx.gridIndex;
  if (!gi) return null;
  const cells = lockCells(gi, ctx.locks.open, ctx.locks.closed);
  if (!cells.length) return null;
  const openC = hexToRgba(LOCK_OPEN_HEX);
  const closedC = hexToRgba(LOCK_CLOSED_HEX);
  const halo = surfaceRing(ctx.scheme, 235);
  const layers: Layer[] = [
    new PolygonLayer<LockCell>({
      id: 'kilit-cerceve',
      data: cells,
      positionFormat: 'XY',
      getPolygon: (c) => gi.polygon(c.i),
      filled: true,
      getFillColor: (c) => (c.kind === 'open' ? [openC[0], openC[1], openC[2], 50] : [closedC[0], closedC[1], closedC[2], 50]),
      stroked: true,
      getLineColor: (c) => (c.kind === 'open' ? openC : closedC),
      lineWidthUnits: 'pixels',
      getLineWidth: 2.5,
      pickable: false,
      updateTriggers: { getFillColor: [cells], getLineColor: [cells] },
    }),
    new TextLayer<LockCell>({
      id: 'kilit-simge',
      data: cells,
      getPosition: (c) => gi.centroid(c.i),
      getText: (c) => (c.kind === 'open' ? '+' : '×'),
      getSize: 20,
      sizeUnits: 'pixels',
      getColor: (c) => (c.kind === 'open' ? openC : closedC),
      fontFamily: FONT_STACK,
      fontWeight: 800,
      characterSet: ['+', '×'],
      fontSettings: { sdf: true, fontSize: 64, buffer: 10 },
      outlineWidth: 0.3,
      outlineColor: halo,
      getTextAnchor: 'middle',
      getAlignmentBaseline: 'center',
      // hücredeki hastane işaretini örtmesin: sağ üste kaydır
      getPixelOffset: [13, -13],
      pickable: false,
      updateTriggers: { getColor: [cells], getText: [cells] },
    }),
  ];
  return layers;
};
