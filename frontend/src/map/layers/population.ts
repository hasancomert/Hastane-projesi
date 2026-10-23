/**
 * Nüfus yoğunluğu ızgarası (log renk ölçeği, kişi/km²) ve 3B nüfus sütunları (şartname 6.2, hocanın 7. maddesi).
 *
 * Girdi: senaryonun hücre nüfusu (cellData.popByCell — çözüm varsa layers.demand.population, yoksa GeoJSON pop).
 * Yalnız nüfuslu hücreler çizilir (Ankara'nın ≈%16'sı boş: su, dağ). 3B sütun yüksekliği yoğunlukla doğrusal
 * (eşit alanlı hücrelerde nüfusla da orantılı); sütunlar komşularından ayrılsın diye hücrenin %80'i.
 */
import { SolidPolygonLayer } from '@deck.gl/layers';

import type { GridCells } from '../../api/types.ts';
import type { GridIndex } from '../gridIndex.ts';
import { densityScale } from '../mapColors.ts';
import { popByCell } from './cellData.ts';
import { keepAlive, memoLast, writeColor, underLabels } from './memo.ts';
import type { LayerBuilder } from './types.ts';

/** Sütun yüksekliği: 1 kişi/km² → 1,2 m (en yoğun hücre ≈ 30.000 kişi/km² → ≈36 km; Ankara ≈270 km genişlikte). */
export const COLUMN_M_PER_DENSITY = 1.2;

interface PopCells {
  /** nüfuslu hücrelerin grid_index'leri (katman verisi) */
  cells: number[];
  /** grid_index → yoğunluk (kişi/km²) */
  dens: Float64Array;
}

const popCells = memoLast((grid: GridCells, gi: GridIndex, pop: Float64Array): PopCells => {
  void grid;
  const cells: number[] = [];
  const dens = new Float64Array(gi.n);
  for (let i = 0; i < gi.n; i++) {
    if (pop[i] > 0) {
      cells.push(i);
      dens[i] = pop[i] / gi.cellAreaKm2;
    }
  }
  return { cells, dens };
});

export const populationLayer: LayerBuilder = (ctx) => {
  const gi = ctx.gridIndex;
  if (!gi || !ctx.grid) return null;
  const flat = keepAlive('nufus', ctx.visible.population);
  const cols = keepAlive('nufus-3b', ctx.visible.population3d);
  if (!flat && !cols) return null;
  const pop = popByCell(ctx.grid, gi, ctx.solution?.layers.demand ?? null);
  const data = popCells(ctx.grid, gi, pop);
  const scale = densityScale(ctx.scheme);
  const fill = (i: number, { target }: { target: number[] }) => writeColor(target, scale.color(data.dens[i]));
  return [
    flat &&
    new SolidPolygonLayer<number>({
      id: 'nufus',
      data: data.cells,
      visible: ctx.visible.population,
      ...underLabels(ctx),
      positionFormat: 'XY',
      getPolygon: (i) => gi.polygon(i),
      getFillColor: fill,
      pickable: false,
      updateTriggers: { getFillColor: [ctx.scheme, data] },
    }),
    cols &&
    new SolidPolygonLayer<number>({
      id: 'nufus-3b',
      data: data.cells,
      visible: ctx.visible.population3d,
      ...underLabels(ctx),
      positionFormat: 'XY',
      extruded: true,
      wireframe: false,
      getPolygon: (i) => gi.columnPolygon(i),
      getElevation: (i) => data.dens[i] * COLUMN_M_PER_DENSITY,
      getFillColor: (i, { target }) => writeColor(target, scale.color(data.dens[i]), 1),
      material: { ambient: 0.55, diffuse: 0.6, shininess: 24, specularColor: [40, 40, 40] },
      // sütunlar birbirini doğru örtsün (harita düzeyinde derinlik testi kapalı)
      parameters: { depthCompare: 'less-equal', depthWriteEnabled: true },
      pickable: false,
      updateTriggers: { getFillColor: [ctx.scheme, data], getElevation: [data] },
    }),
  ].filter((l): l is SolidPolygonLayer<number> => !!l);
};
