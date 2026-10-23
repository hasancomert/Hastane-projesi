/**
 * R yarıçap halkaları (şartname 6.2): her açık hastanenin çevresinde R'nin KUŞ UÇUŞU karşılığı.
 *
 * R yol mesafesidir (30 km); dolambaç profili ters çevrilince haritada ≈25 km'lik daire (layers.rings.R_crowfly_km;
 * 30 km'lik daire ≈%20 büyük olurdu). Halkalar ince ve silik; odak hastanesinin halkası sınıf renginde, kalın ve
 * hafif dolgulu ("bu hastaneye R içinde ulaşılabilen alan").
 */
import { ScatterplotLayer } from '@deck.gl/layers';

import type { Hospital } from '../../api/types.ts';
import { ink } from '../mapColors.ts';
import { focusHospital, keepAlive, staleOpacity, writeColor } from './memo.ts';
import { hospitalColor } from './serviceAreas.ts';
import type { LayerBuilder } from './types.ts';

/** Haritada çizilecek yarıçap (km, kuş uçuşu) — çözümün halka bilgisinden, yoksa meta'dan. */
export function ringRadiusKm(ctx: { solution: { layers?: { rings?: { R_crowfly_km: number } } } | null; meta: { access_rings?: { R_crowfly_km: number } } | null }): number {
  return ctx.solution?.layers?.rings?.R_crowfly_km ?? ctx.meta?.access_rings?.R_crowfly_km ?? 25;
}

export const ringsLayer: LayerBuilder = (ctx) => {
  const hs = ctx.solution?.hospitals;
  if (!hs?.length) return null;
  if (!keepAlive('halkalar', ctx.visible.rings)) return null;
  const rM = ringRadiusKm(ctx) * 1000;
  const focus = focusHospital(ctx);
  const line = ink(ctx.scheme, 'secondary', ctx.scheme === 'dark' ? 120 : 105);
  const fh = focus !== null ? hs[focus] : undefined;
  return [
    new ScatterplotLayer<Hospital>({
      id: 'halkalar',
      data: hs,
      visible: ctx.visible.rings,
      opacity: staleOpacity(ctx.stale),
      getPosition: (h) => [h.lon, h.lat],
      getRadius: rM,
      radiusUnits: 'meters',
      stroked: true,
      filled: false,
      getLineColor: (h, { index, target }) => (index === focus ? writeColor(target, hospitalColor(h, ctx.scheme)) : writeColor(target, line)),
      lineWidthUnits: 'pixels',
      getLineWidth: (_, { index }) => (index === focus ? 2.5 : 1),
      pickable: false,
      updateTriggers: { getLineColor: [focus, ctx.scheme, hs], getLineWidth: [focus], getRadius: [rM] },
    }),
    // odak hastanesinin R alanı (hafif dolgu)
    new ScatterplotLayer<Hospital>({
      id: 'halka-odak',
      data: fh ? [fh] : [],
      visible: ctx.visible.rings && !!fh,
      opacity: staleOpacity(ctx.stale),
      getPosition: (h) => [h.lon, h.lat],
      getRadius: rM,
      radiusUnits: 'meters',
      stroked: false,
      filled: true,
      getFillColor: (h, { target }) => writeColor(target, hospitalColor(h, ctx.scheme), 0.12),
      pickable: false,
      updateTriggers: { getFillColor: [ctx.scheme], getRadius: [rM] },
    }),
  ];
};
