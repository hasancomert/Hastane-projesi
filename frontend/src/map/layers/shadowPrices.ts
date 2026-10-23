/**
 * Gölge fiyat haritası (şartname 7.6): açık hastaneler sabitken atama LP'sinin dual değeri — hücrede bir kişi daha
 * olsaydı toplam maliyetin ne kadar artacağı (TL/kişi; bugünkü değer, ufuk boyunca). Yüksek değer: bu hücreye
 * hizmet pahalı (uzak ya da kapasitesi dolu hastaneler) → yeni hastane için aday bölge.
 *
 * Girdi: duals.demand_cell / marginal_cost_per_person_tl (sunucu dual istendiğinde döndürür; varsayılan çözümlerde
 * var). Renk alanı değerlerin %2–%98 yüzdeliği (uç değerler rampayı ezmesin). Dual yoksa katman çizilmez; lejant
 * düğmesi devre dışı ve açıklamalı (bkz. MapLegend).
 */
import { SolidPolygonLayer } from '@deck.gl/layers';

import type { Duals } from '../../api/types.ts';
import type { Scheme } from '../../theme/palette.ts';
import type { GridIndex } from '../gridIndex.ts';
import { robustDomain, shadowScale, type SequentialScale } from '../mapColors.ts';
import { keepAlive, memoLast, staleOpacity, writeColor, underLabels } from './memo.ts';
import type { LayerBuilder } from './types.ts';

interface ShadowData {
  /** dual konumları (duals sırası) — hücresi ızgarada olanlar */
  positions: number[];
  gridIndex: Int32Array;
  domain: [number, number];
}

const shadowData = memoLast((gi: GridIndex, duals: Duals): ShadowData => {
  const m = duals.demand_cell.length;
  const gridIndex = new Int32Array(m).fill(-1);
  const positions: number[] = [];
  for (let k = 0; k < m; k++) {
    const i = gi.byId.get(duals.demand_cell[k]);
    if (i === undefined || !Number.isFinite(duals.marginal_cost_per_person_tl[k])) continue;
    gridIndex[k] = i;
    positions.push(k);
  }
  return { positions, gridIndex, domain: robustDomain(duals.marginal_cost_per_person_tl) };
});

const scaleFor = memoLast((scheme: Scheme, lo: number, hi: number): SequentialScale => shadowScale(scheme, [lo, hi]));

/** Gösterilen çözümün gölge fiyat ölçeği (lejant da aynısını kullanır); dual yoksa null. */
export function shadowScaleFor(gi: GridIndex | null, duals: Duals | null | undefined, scheme: Scheme): SequentialScale | null {
  if (!gi || !duals?.marginal_cost_per_person_tl?.length) return null;
  const d = shadowData(gi, duals);
  return scaleFor(scheme, d.domain[0], d.domain[1]);
}

export const shadowPricesLayer: LayerBuilder = (ctx) => {
  const gi = ctx.gridIndex;
  const duals = ctx.solution?.duals;
  if (!gi || !duals?.marginal_cost_per_person_tl?.length) return null;
  if (!keepAlive('golge', ctx.visible.shadowPrices)) return null;
  const data = shadowData(gi, duals);
  const scale = scaleFor(ctx.scheme, data.domain[0], data.domain[1]);
  return new SolidPolygonLayer<number>({
    id: 'golge',
    data: data.positions,
    visible: ctx.visible.shadowPrices,
    ...underLabels(ctx),
    opacity: staleOpacity(ctx.stale),
    positionFormat: 'XY',
    getPolygon: (k) => gi.polygon(data.gridIndex[k]),
    getFillColor: (k, { target }) => writeColor(target, scale.color(duals.marginal_cost_per_person_tl[k])),
    pickable: false,
    updateTriggers: { getFillColor: [ctx.scheme, duals] },
  });
};
