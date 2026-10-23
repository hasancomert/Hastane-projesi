/**
 * Erişim ısı haritası (şartname 6.2): her hücrenin en yakın AÇIK hastaneye yol mesafesi — ızgaranın tüm
 * hücreleri (nüfussuz hücreler de: "burada biri yaşasaydı"). 0 … R doğrusal sıralı rampa; R'nin ötesi durum
 * rengi (critical). Nüfuslu olup R dışında kalan hücreler (riskli bölge) ayrıca kalın çerçeveyle vurgulanır.
 *
 * Girdi: layers.access.nearest_km (grid_index sırası) + layers.rings.R_km. Not: assignment.km atanan hastaneye
 * mesafedir (kapasite yüzünden en yakın olmayabilir); bu katman en yakın açık hastaneyi gösterir.
 */
import { PolygonLayer, SolidPolygonLayer } from '@deck.gl/layers';

import type { GridCells, LayerAccess } from '../../api/types.ts';
import type { GridIndex } from '../gridIndex.ts';
import { accessScale, beyondRColor } from '../mapColors.ts';
import { popByCell } from './cellData.ts';
import { keepAlive, memoLast, staleOpacity, writeColor, underLabels } from './memo.ts';
import type { LayerBuilder } from './types.ts';

interface AccessData {
  cells: number[];
  /** R dışında kalan NÜFUSLU hücreler */
  risky: number[];
}

const accessData = memoLast((gi: GridIndex, access: LayerAccess, pop: Float64Array, R: number): AccessData => {
  const cells: number[] = [];
  const risky: number[] = [];
  const n = Math.min(gi.n, access.nearest_km.length);
  for (let i = 0; i < n; i++) {
    const d = access.nearest_km[i];
    if (d === null || d === undefined || !Number.isFinite(d)) continue;
    cells.push(i);
    if (d > R + 1e-9 && pop[i] > 0) risky.push(i);
  }
  return { cells, risky };
});

/** R (yol km) — çözümün halka bilgisinden, yoksa meta'dan. */
export function currentR(ctx: { solution: { layers?: { rings?: { R_km: number } } } | null; meta: { access_rings?: { R_km: number } } | null }): number {
  return ctx.solution?.layers?.rings?.R_km ?? ctx.meta?.access_rings?.R_km ?? 30;
}

export const accessLayer: LayerBuilder = (ctx) => {
  const gi = ctx.gridIndex;
  const access = ctx.solution?.layers?.access;
  if (!gi || !ctx.grid || !access) return null;
  if (!keepAlive('erisim', ctx.visible.access)) return null;
  const R = currentR(ctx);
  const pop = popByCell(ctx.grid as GridCells, gi, ctx.solution?.layers.demand ?? null);
  const data = accessData(gi, access, pop, R);
  const scale = accessScale(ctx.scheme, R);
  const beyond = beyondRColor(ctx.scheme === 'dark' ? 225 : 205);
  const opacity = staleOpacity(ctx.stale);
  return [
    new SolidPolygonLayer<number>({
      id: 'erisim',
      data: data.cells,
      visible: ctx.visible.access,
      ...underLabels(ctx),
      opacity,
      positionFormat: 'XY',
      getPolygon: (i) => gi.polygon(i),
      getFillColor: (i, { target }) => {
        const d = access.nearest_km[i];
        return writeColor(target, d > R + 1e-9 ? beyond : scale.color(d));
      },
      pickable: false,
      updateTriggers: { getFillColor: [ctx.scheme, access, R] },
    }),
    new PolygonLayer<number>({
      id: 'erisim-riskli',
      data: data.risky,
      visible: ctx.visible.access && data.risky.length > 0,
      ...underLabels(ctx),
      opacity,
      positionFormat: 'XY',
      getPolygon: (i) => gi.polygon(i),
      filled: false,
      stroked: true,
      getLineColor: beyondRColor(255),
      lineWidthUnits: 'pixels',
      getLineWidth: 3,
      pickable: false,
    }),
  ];
};
