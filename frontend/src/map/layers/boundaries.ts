/**
 * İl ve ilçe sınırları (şartname 6.2 ilk katman; hocanın 2. maddesi "Ankara haritasını çiz"). Altlık harita
 * yüklenemese de haritanın anlamlı kalmasını sağlar. Çizgiler altlık yazılarının altında (beforeId); ilçe ADLARI
 * MapLibre sembol katmanıdır (basemap.ts: yerel Noto Sans glifleri, çakışma önleme, hale) — görünürlüğünü MapView
 * bu katmanla birlikte açar/kapatır.
 */
import { GeoJsonLayer } from '@deck.gl/layers';
import type { FeatureCollection } from 'geojson';

import { ink } from '../mapColors.ts';
import { underLabels } from './memo.ts';
import type { LayerBuilder } from './types.ts';

export const boundariesLayer: LayerBuilder = (ctx) => {
  if (!ctx.boundaries) return null;
  const visible = ctx.visible.boundaries;
  const dark = ctx.scheme === 'dark';
  return [
    new GeoJsonLayer({
      id: 'ilceler',
      data: ctx.boundaries.ilceler as unknown as FeatureCollection,
      visible,
      ...underLabels(ctx),
      stroked: true,
      filled: false,
      getLineColor: ink(ctx.scheme, 'secondary', dark ? 150 : 140),
      lineWidthUnits: 'pixels',
      getLineWidth: 1,
      lineWidthMinPixels: 1,
      pickable: false,
      updateTriggers: { getLineColor: ctx.scheme },
    }),
    new GeoJsonLayer({
      id: 'il',
      data: ctx.boundaries.il as unknown as FeatureCollection,
      visible,
      ...underLabels(ctx),
      stroked: true,
      filled: false,
      getLineColor: ink(ctx.scheme, 'primary', dark ? 210 : 190),
      lineWidthUnits: 'pixels',
      getLineWidth: 2.5,
      pickable: false,
      updateTriggers: { getLineColor: ctx.scheme },
    }),
  ];
};
