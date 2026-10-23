/**
 * Hizmet bölgeleri (şartname 6.2; hocanın 3b maddesi "hastanenin hizmet bölgesi"): her talep hücresi, talebinin
 * en büyük payını alan hastanenin SINIF renginde (harita, lejant ve kartta aynı renk = aynı anlam). 89 hastaneyi
 * tek tek renklendirmek yerine (kategorik renk 4'ten sonra ayırt edilemez, döngüsel renk anlam taşımaz) bölgeler
 * arasına yüzey renginde 1,5 px'lik ayırıcı çizilir: her bölge kendi sınırıyla okunur, rengi sınıfını söyler.
 *
 * Dolgu açık bir tint'tir (açık temada %22, koyuda %30 opaklık): bölgenin sınıfını söyler ama hastane işaretleriyle
 * yarışmaz. Girdi: layers.demand (grid_index, service_hospital, service_share) + hospitals[].class. Talebi bölünmüş hücreler
 * (service_share < 1) daha soluk. Üzerine gelinen ya da seçili hastanenin bölgesi koyu + mürekkep çerçeveli,
 * diğerleri geri çekilir (şartname 6.2 "üzerine gelince hizmet bölgesi vurgulanır").
 */
import { LineLayer, SolidPolygonLayer } from '@deck.gl/layers';

import type { Hospital, LayerDemand } from '../../api/types.ts';
import { classColorRgba, type Rgba, type Scheme } from '../../theme/palette.ts';
import type { GridIndex } from '../gridIndex.ts';
import { ink, surfaceRing } from '../mapColors.ts';
import { focusHospital, keepAlive, memoLast, staleOpacity, writeColor, underLabels } from './memo.ts';
import type { LayerBuilder } from './types.ts';

interface ServiceData {
  /** talep konumları (layers.demand sırası) */
  positions: number[];
  /** grid_index → hizmet veren hastane (−1 talep yok) */
  svcByCell: Int32Array;
  /** bölge sınırı kenarları: [x1, y1, x2, y2, a, b] — a, b iki yandaki hastaneler */
  edges: Float64Array;
  nEdges: number;
  /** deck.gl ikili veri nesnesi (aynı referans → yeniden yükleme yok) */
  edgeData: { length: number; attributes: Record<string, { value: Float64Array; size: number; stride: number; offset: number }> };
}

const serviceData = memoLast((gi: GridIndex, demand: LayerDemand): ServiceData => {
  const svcByCell = new Int32Array(gi.n).fill(-1);
  const positions: number[] = new Array(demand.grid_index.length);
  for (let k = 0; k < demand.grid_index.length; k++) {
    positions[k] = k;
    const i = demand.grid_index[k];
    if (i >= 0 && i < gi.n) svcByCell[i] = demand.service_hospital[k];
  }
  const tmp: number[] = [];
  for (let k = 0; k < demand.grid_index.length; k++) {
    const i = demand.grid_index[k];
    const a = svcByCell[i];
    if (a < 0) continue;
    for (const j of [gi.east[i], gi.north[i]]) {
      if (j < 0) continue;
      const b = svcByCell[j];
      if (b < 0 || b === a) continue;
      const e = gi.sharedEdge(i, j);
      if (e) tmp.push(e[0], e[1], e[2], e[3], a, b);
    }
  }
  const edges = Float64Array.from(tmp);
  const nEdges = tmp.length / 6;
  // ikili öznitelikler: kenar dizisi [x1, y1, x2, y2, a, b] doğrudan GPU'ya (adım 48 bayt)
  const edgeData = {
    length: nEdges,
    attributes: {
      getSourcePosition: { value: edges, size: 2, stride: 48, offset: 0 },
      getTargetPosition: { value: edges, size: 2, stride: 48, offset: 16 },
    },
  };
  return { positions, svcByCell, edges, nEdges, edgeData };
});

/** Sınıf renkleri (tema başına bir kez). */
const classRgba = memoLast((scheme: Scheme) => ({
  'IV-C': classColorRgba('IV-C', scheme),
  'V-B': classColorRgba('V-B', scheme),
  'V-C': classColorRgba('V-C', scheme),
  'V-D': classColorRgba('V-D', scheme),
}));

export function hospitalColor(h: Hospital | undefined, scheme: Scheme): Rgba {
  return h ? classRgba(scheme)[h.class] : [128, 128, 128, 255];
}

export const serviceAreasLayer: LayerBuilder = (ctx) => {
  const gi = ctx.gridIndex;
  const sol = ctx.solution;
  if (!gi || !sol?.layers?.demand) return null;
  if (!keepAlive('hizmet', ctx.visible.serviceAreas)) return null;
  const demand = sol.layers.demand;
  const data = serviceData(gi, demand);
  const hs = sol.hospitals;
  const focus = focusHospital(ctx);
  const dark = ctx.scheme === 'dark';
  // açık tint: 67 "küçük" (mavi) hastanenin bölgeleri ili maviye boyamasın, hastaneler zeminden ayrılsın
  const base = dark ? 0.3 : 0.22;
  const opacity = staleOpacity(ctx.stale);

  const fillAlpha = (k: number) => {
    const h = demand.service_hospital[k];
    const split = demand.service_share[k] < 0.999 ? 0.6 : 1;
    if (focus === null) return 255 * base * split;
    return h === focus ? 255 * Math.min(0.7, base * 2.6) * split : 255 * base * 0.45 * split;
  };

  const sep = surfaceRing(ctx.scheme, dark ? 220 : 235);
  const focusLine = ink(ctx.scheme, 'primary', 230);

  // odak bölgesinin dış sınırı: iki yanından biri odak olan kenarlar + odak bölgesinin talep olmayan komşularla sınırı
  const focusEdges: number[] = [];
  if (focus !== null && ctx.visible.serviceAreas) {
    for (let e = 0; e < data.nEdges; e++) {
      const a = data.edges[6 * e + 4];
      const b = data.edges[6 * e + 5];
      if (a === focus || b === focus) focusEdges.push(e);
    }
  }

  return [
    new SolidPolygonLayer<number>({
      id: 'hizmet',
      data: data.positions,
      visible: ctx.visible.serviceAreas,
      ...underLabels(ctx),
      opacity,
      positionFormat: 'XY',
      getPolygon: (k) => gi.polygon(demand.grid_index[k]),
      getFillColor: (k, { target }) => {
        const c = hospitalColor(hs[demand.service_hospital[k]], ctx.scheme);
        return writeColor(target, [c[0], c[1], c[2], fillAlpha(k)]);
      },
      pickable: false,
      updateTriggers: { getFillColor: [ctx.scheme, data, hs, focus] },
    }),
    new LineLayer({
      id: 'hizmet-sinir',
      data: data.edgeData,
      visible: ctx.visible.serviceAreas,
      ...underLabels(ctx),
      opacity,
      getColor: sep,
      widthUnits: 'pixels',
      getWidth: dark ? 1.25 : 1.5,
      pickable: false,
      updateTriggers: { getColor: [ctx.scheme] },
    }),
    new LineLayer<number>({
      id: 'hizmet-odak',
      data: focusEdges,
      visible: ctx.visible.serviceAreas && focusEdges.length > 0,
      ...underLabels(ctx),
      getSourcePosition: (e) => [data.edges[6 * e], data.edges[6 * e + 1]],
      getTargetPosition: (e) => [data.edges[6 * e + 2], data.edges[6 * e + 3]],
      getColor: focusLine,
      widthUnits: 'pixels',
      getWidth: 2,
      pickable: false,
      updateTriggers: { getColor: [ctx.scheme] },
    }),
  ];
};
