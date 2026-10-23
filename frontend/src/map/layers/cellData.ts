/**
 * Hücre başına türetilmiş diziler (grid_index sırasıyla), not alınmış: katmanlar ve ipuçları (tooltip) ortak kullanır.
 * Girdi nesneleri (ızgara, çözümün layers.* alanları) değişmedikçe aynı dizi döner.
 */
import type { Duals, GridCells, LayerCandidates, LayerDemand } from '../../api/types.ts';
import type { GridIndex } from '../gridIndex.ts';
import { memoLast } from './memo.ts';

/**
 * Senaryonun hücre nüfusu (kişi). Çözüm varsa `layers.demand.population` (2030 / GHS senaryolarında GeoJSON'daki
 * `pop` değil bu geçerli), yoksa GeoJSON `pop` (WorldPop 2025, TÜİK'e kalibre).
 */
export const popByCell = memoLast((grid: GridCells, gi: GridIndex, demand: LayerDemand | null): Float64Array => {
  const out = new Float64Array(gi.n);
  if (demand) {
    for (let k = 0; k < demand.grid_index.length; k++) {
      const i = demand.grid_index[k];
      if (i >= 0 && i < gi.n) out[i] = demand.population[k];
    }
  } else {
    grid.features.forEach((f, i) => {
      out[i] = f.properties.pop || 0;
    });
  }
  return out;
});

/** grid_index → layers.demand konumu (talep hücresi değilse −1). */
export const demandPosByCell = memoLast((gi: GridIndex, demand: LayerDemand | null): Int32Array => {
  const out = new Int32Array(gi.n).fill(-1);
  if (demand) for (let k = 0; k < demand.grid_index.length; k++) out[demand.grid_index[k]] = k;
  return out;
});

/** grid_index → layers.candidates konumu (aday değilse −1). */
export const candPosByCell = memoLast((gi: GridIndex, cand: LayerCandidates | null): Int32Array => {
  const out = new Int32Array(gi.n).fill(-1);
  if (cand) for (let k = 0; k < cand.grid_index.length; k++) out[cand.grid_index[k]] = k;
  return out;
});

/** grid_index → duals konumu (dual yoksa ya da talep hücresi değilse −1). */
export const dualPosByCell = memoLast((gi: GridIndex, duals: Duals | null | undefined): Int32Array => {
  const out = new Int32Array(gi.n).fill(-1);
  if (duals) {
    duals.demand_cell.forEach((id, k) => {
      const i = gi.byId.get(id);
      if (i !== undefined) out[i] = k;
    });
  }
  return out;
});

/** Hücre nüfusundan yoğunluk (kişi/km²). */
export function densityOf(pop: number, gi: GridIndex): number {
  return pop / gi.cellAreaKm2;
}
