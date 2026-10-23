/**
 * Seçim ve imleç geri bildirimi (en üstte): seçili hücrenin çift çizgili çerçevesi (yüzey + mürekkep — her
 * dolgunun üstünde okunur), seçili hastanenin halesi, kilit aracındayken imlecin altındaki hücrenin önizlemesi
 * (zorunlu = good, yasak = critical), seçim aracında bir hücre katmanı açıkken ince imleç çerçevesi.
 */
import type { Layer } from '@deck.gl/core';
import { PolygonLayer, ScatterplotLayer } from '@deck.gl/layers';

import type { Hospital } from '../../api/types.ts';
import { hexToRgba } from '../../theme/palette.ts';
import { LOCK_CLOSED_HEX, LOCK_OPEN_HEX, ink, surfaceRing } from '../mapColors.ts';
import { PX_PER_SQRT_BED, symbolScale } from './hospitals.ts';
import type { LayerBuilder } from './types.ts';

export const selectionLayer: LayerBuilder = (ctx) => {
  const gi = ctx.gridIndex;
  const out: Layer[] = [];
  const inkC = ink(ctx.scheme, 'primary', 255);
  const halo = surfaceRing(ctx.scheme, 255);

  // imleç altındaki hücre
  const hc = ctx.hoveredCell ?? -1;
  const tool = ctx.tool ?? 'select';
  const anyCellLayer = ctx.visible.population || ctx.visible.access || ctx.visible.serviceAreas || ctx.visible.shadowPrices || ctx.visible.candidates;
  if (gi && hc >= 0 && hc < gi.n && (tool !== 'select' || anyCellLayer)) {
    const c = tool === 'lockOpen' ? hexToRgba(LOCK_OPEN_HEX) : tool === 'lockClosed' ? hexToRgba(LOCK_CLOSED_HEX) : ink(ctx.scheme, 'primary', 170);
    out.push(
      new PolygonLayer<number>({
        id: 'imlec-hucre',
        data: [hc],
        positionFormat: 'XY',
        getPolygon: (i) => gi.polygon(i),
        filled: tool !== 'select',
        getFillColor: [c[0], c[1], c[2], 60],
        stroked: true,
        getLineColor: c,
        lineWidthUnits: 'pixels',
        getLineWidth: tool === 'select' ? 1.5 : 2.5,
        pickable: false,
        updateTriggers: { getFillColor: [tool, ctx.scheme], getLineColor: [tool, ctx.scheme], getLineWidth: [tool] },
      }),
    );
  }

  // seçili hücre
  const sel = ctx.selection;
  if (gi && sel?.kind === 'cell' && sel.gridIndex >= 0 && sel.gridIndex < gi.n) {
    for (const [id, color, w] of [
      ['secim-hucre-alt', halo, 5.5],
      ['secim-hucre', inkC, 2.5],
    ] as const) {
      out.push(
        new PolygonLayer<number>({
          id,
          data: [sel.gridIndex],
          positionFormat: 'XY',
          getPolygon: (i) => gi.polygon(i),
          filled: false,
          stroked: true,
          getLineColor: color,
          lineWidthUnits: 'pixels',
          getLineWidth: w,
          pickable: false,
          updateTriggers: { getLineColor: [ctx.scheme] },
        }),
      );
    }
  }

  // seçili hastanenin halesi
  const hs = ctx.solution?.hospitals;
  if (hs && sel?.kind === 'hospital' && hs[sel.index] && ctx.visible.hospitals) {
    const h = hs[sel.index];
    const scale = symbolScale(ctx.zoom);
    for (const [id, color, w] of [
      ['secim-hastane-alt', halo, 6],
      ['secim-hastane', inkC, 2.5],
    ] as const) {
      out.push(
        new ScatterplotLayer<Hospital>({
          id,
          data: [h],
          getPosition: (x) => [x.lon, x.lat],
          getRadius: (x) => PX_PER_SQRT_BED * Math.sqrt(Math.max(x.beds, 1)) + 5 / scale,
          radiusUnits: 'pixels',
          radiusScale: scale,
          filled: false,
          stroked: true,
          getLineColor: color,
          lineWidthUnits: 'pixels',
          getLineWidth: w,
          pickable: false,
          updateTriggers: { getRadius: [scale], getLineColor: [ctx.scheme] },
        }),
      );
    }
  }
  return out;
};
