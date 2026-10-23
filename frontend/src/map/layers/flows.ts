/**
 * Hasta akışları: hücreden hastaneye yaylar (ArcLayer; şartname 6.2).
 *
 * Girdi: assignment (paralel diziler: demand_cell, hospital, share, km, population — yayda taşınan nüfus) +
 * hücre merkezleri (gridIndex) + hastane konumları. 3 km'de ≈2.700, 2 km'de ≈6.000 yay: ikili öznitelikler
 * (Float64/Float32/Uint8 dizileri) doğrudan GPU'ya gider; konumlar çözüm değişince, renkler yalnız tema ya da odak
 * hastanesi değişince yeniden kurulur. Genişlik ∝ √nüfus (alan algısı), ince ve yarı saydam; renk = hedef
 * hastanenin sınıfı, kaynak ucunda soluk (yön okunur). Odak hastanesinin yayları öne çıkar, diğerleri silikleşir.
 */
import { ArcLayer } from '@deck.gl/layers';

import type { Assignment, Hospital } from '../../api/types.ts';
import type { Scheme } from '../../theme/palette.ts';
import type { GridIndex } from '../gridIndex.ts';
import { focusHospital, keepAlive, memoLast, staleOpacity } from './memo.ts';
import { hospitalColor } from './serviceAreas.ts';
import type { LayerBuilder } from './types.ts';

/** En kalın yay (px) — en büyük akış; diğerleri √(nüfus / en büyük) ile ölçeklenir. */
const MAX_WIDTH_PX = 6;
const MIN_WIDTH_PX = 0.6;

interface FlowGeom {
  n: number;
  src: Float64Array;
  tgt: Float64Array;
  width: Float32Array;
  hospital: Int32Array;
  /** yay → assignment konumu (geçerli yaylar; hücresi ızgarada olmayanlar atlanır) */
  arc: Int32Array;
}

const flowGeom = memoLast((gi: GridIndex, a: Assignment, hs: Hospital[]): FlowGeom => {
  const m = a.demand_cell.length;
  const src = new Float64Array(2 * m);
  const tgt = new Float64Array(2 * m);
  const width = new Float32Array(m);
  const hospital = new Int32Array(m);
  const arc = new Int32Array(m);
  let maxPop = 1;
  for (let k = 0; k < m; k++) if (a.population[k] > maxPop) maxPop = a.population[k];
  let n = 0;
  for (let k = 0; k < m; k++) {
    const i = gi.byId.get(a.demand_cell[k]);
    const h = hs[a.hospital[k]];
    if (i === undefined || !h || !(a.population[k] > 0)) continue;
    src[2 * n] = gi.centroids[2 * i];
    src[2 * n + 1] = gi.centroids[2 * i + 1];
    tgt[2 * n] = h.lon;
    tgt[2 * n + 1] = h.lat;
    width[n] = Math.max(MIN_WIDTH_PX, MAX_WIDTH_PX * Math.sqrt(a.population[k] / maxPop));
    hospital[n] = a.hospital[k];
    arc[n] = k;
    n++;
  }
  return { n, src: src.subarray(0, 2 * n), tgt: tgt.subarray(0, 2 * n), width: width.subarray(0, n), hospital: hospital.subarray(0, n), arc: arc.subarray(0, n) };
});

const flowData = memoLast((g: FlowGeom, hs: Hospital[], scheme: Scheme, focus: number | null) => {
  const srcColor = new Uint8Array(4 * g.n);
  const tgtColor = new Uint8Array(4 * g.n);
  const dark = scheme === 'dark';
  for (let k = 0; k < g.n; k++) {
    const h = g.hospital[k];
    const c = hospitalColor(hs[h], scheme);
    const on = focus === null ? null : h === focus;
    const aT = on === null ? (dark ? 170 : 150) : on ? 245 : 14;
    const aS = on === null ? (dark ? 55 : 45) : on ? 120 : 6;
    srcColor.set([c[0], c[1], c[2], aS], 4 * k);
    tgtColor.set([c[0], c[1], c[2], aT], 4 * k);
  }
  return {
    length: g.n,
    attributes: {
      getSourcePosition: { value: g.src, size: 2 },
      getTargetPosition: { value: g.tgt, size: 2 },
      getSourceColor: { value: srcColor, size: 4, normalized: true },
      getTargetColor: { value: tgtColor, size: 4, normalized: true },
      getWidth: { value: g.width, size: 1 },
    },
  };
});

/** Yay → assignment konumu (ipucu için; ikili veride `info.index` yay sırasıdır). */
export function flowArcIndex(gi: GridIndex, a: Assignment, hs: Hospital[], arcIndex: number): number {
  const g = flowGeom(gi, a, hs);
  return arcIndex >= 0 && arcIndex < g.n ? g.arc[arcIndex] : -1;
}

export const flowsLayer: LayerBuilder = (ctx) => {
  const gi = ctx.gridIndex;
  const sol = ctx.solution;
  if (!gi || !sol?.assignment || !sol.hospitals.length) return null;
  if (!keepAlive('akislar', ctx.visible.flows)) return null;
  const g = flowGeom(gi, sol.assignment, sol.hospitals);
  const focus = focusHospital(ctx);
  return new ArcLayer({
    id: 'akislar',
    data: flowData(g, sol.hospitals, ctx.scheme, ctx.visible.flows ? focus : null),
    visible: ctx.visible.flows,
    opacity: staleOpacity(ctx.stale),
    widthUnits: 'pixels',
    widthMinPixels: MIN_WIDTH_PX,
    getHeight: 0.35,
    greatCircle: false,
    // kısa kent içi yaylar için 16 parça yeterli (varsayılan 50; 2 km'de ≈6.000 yay → köşe sayısı 3 kat az)
    numSegments: 16,
    // yaylar toplanmaz: binlerce yayı her imleç hareketinde toplama tamponuna çizmek pahalı; hücre ipucu zaten
    // "hizmet aldığı hastane"yi söyler, hastane üzerine gelince yayları vurgulanır
    pickable: false,
  });
};
