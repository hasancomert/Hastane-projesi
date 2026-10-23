/**
 * Aday hücreler (şartname 6.2; hocanın 5. maddesi "ızgaraya kaç hastane sığar?" = aday hücre sayısı).
 *
 * Girdi: çözümün `layers.candidates` alanı (parametreye bağlı aday kümesi; origin 0 aday kuralı · 1 kapsama
 * onarımı · 2 kullanıcı 'aç' kilidi; open 0/1). Çözüm yoksa GeoJSON `aday` işareti (varsayılan parametrelerin
 * adayları). Hücre çerçevesi + çok hafif dolgu; hastane açılan adaylar biraz koyu. Kullanıcının kilitleri ayrı
 * katmanda (locks.ts) — aday katmanı kapalıyken de görünür.
 */
import { PolygonLayer } from '@deck.gl/layers';

import type { GridCells, LayerCandidates } from '../../api/types.ts';
import type { GridIndex } from '../gridIndex.ts';
import { ink } from '../mapColors.ts';
import { keepAlive, memoLast, staleOpacity, writeColor, underLabels } from './memo.ts';
import type { LayerBuilder } from './types.ts';

interface CandCells {
  /** aday hücrelerin grid_index'leri */
  cells: number[];
  /** grid_index → origin (−1 aday değil) */
  origin: Int8Array;
  /** grid_index → 1 açık */
  open: Uint8Array;
}

const candCells = memoLast((grid: GridCells, gi: GridIndex, cand: LayerCandidates | null): CandCells => {
  const origin = new Int8Array(gi.n).fill(-1);
  const open = new Uint8Array(gi.n);
  const cells: number[] = [];
  if (cand) {
    for (let k = 0; k < cand.grid_index.length; k++) {
      const i = cand.grid_index[k];
      if (i < 0 || i >= gi.n) continue;
      cells.push(i);
      origin[i] = cand.origin[k];
      open[i] = cand.open[k];
    }
  } else {
    grid.features.forEach((f, i) => {
      if (f.properties.aday) {
        cells.push(i);
        origin[i] = 0;
      }
    });
  }
  return { cells, origin, open };
});

export const candidatesLayer: LayerBuilder = (ctx) => {
  const gi = ctx.gridIndex;
  if (!gi || !ctx.grid) return null;
  if (!keepAlive('adaylar', ctx.visible.candidates)) return null;
  const data = candCells(ctx.grid, gi, ctx.solution?.layers.candidates ?? null);
  const line = ink(ctx.scheme, 'primary', ctx.scheme === 'dark' ? 150 : 120);
  const repair = ink(ctx.scheme, 'primary', 235);
  const fillOpen = ink(ctx.scheme, 'primary', 70);
  const fill = ink(ctx.scheme, 'primary', ctx.scheme === 'dark' ? 26 : 18);
  return new PolygonLayer<number>({
    id: 'adaylar',
    data: data.cells,
    visible: ctx.visible.candidates,
    ...underLabels(ctx),
    opacity: staleOpacity(ctx.stale),
    positionFormat: 'XY',
    getPolygon: (i) => gi.polygon(i),
    filled: true,
    stroked: true,
    getFillColor: (i, { target }) => writeColor(target, data.open[i] ? fillOpen : fill),
    getLineColor: (i, { target }) => writeColor(target, data.origin[i] === 1 ? repair : line),
    lineWidthUnits: 'pixels',
    getLineWidth: (i) => (data.origin[i] === 1 ? 2 : 1),
    lineJointRounded: false,
    pickable: false,
    updateTriggers: {
      getFillColor: [ctx.scheme, data],
      getLineColor: [ctx.scheme, data],
      getLineWidth: [data],
    },
  });
};
