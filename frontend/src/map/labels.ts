/**
 * İlçe adı noktaları: her ilçenin (en büyük parçasının) içinde, sınırdan en uzak noktaya yakın bir konum
 * ("erişilmezlik kutbu" kaba ızgara + iki inceltme adımı). Ağırlık merkezi içbükey ilçelerde dışarı düşebilir;
 * bu nokta her zaman içeridedir. 25 ilçe için birkaç milisaniye; sınırlar bir kez yüklenir.
 */
import type { Boundaries, Position } from '../api/types.ts';

export interface LabelFeature {
  type: 'Feature';
  geometry: { type: 'Point'; coordinates: [number, number] };
  properties: { ad: string; ad_tr: string; alan: number };
}
export interface LabelCollection {
  type: 'FeatureCollection';
  features: LabelFeature[];
}

type Ring = Position[];

function ringArea(r: Ring, k: number): number {
  let a = 0;
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) a += (r[j][0] * k) * r[i][1] - (r[i][0] * k) * r[j][1];
  return Math.abs(a / 2);
}

function inside(x: number, y: number, rings: Ring[]): boolean {
  let c = false;
  for (const r of rings) {
    for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
      const xi = r[i][0];
      const yi = r[i][1];
      const xj = r[j][0];
      const yj = r[j][1];
      if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c;
    }
  }
  return c;
}

function edgeDist(x: number, y: number, rings: Ring[], k: number): number {
  let best = Infinity;
  for (const r of rings) {
    for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
      const ax = r[j][0] * k;
      const ay = r[j][1];
      const bx = r[i][0] * k;
      const by = r[i][1];
      const px = x * k;
      const dx = bx - ax;
      const dy = by - ay;
      const len = dx * dx + dy * dy;
      const t = len > 0 ? Math.max(0, Math.min(1, ((px - ax) * dx + (y - ay) * dy) / len)) : 0;
      const qx = ax + t * dx - px;
      const qy = ay + t * dy - y;
      const d = qx * qx + qy * qy;
      if (d < best) best = d;
    }
  }
  return Math.sqrt(best);
}

function labelPoint(rings: Ring[]): [number, number] {
  const outer = rings[0];
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const p of outer) {
    if (p[0] < minX) minX = p[0];
    if (p[0] > maxX) maxX = p[0];
    if (p[1] < minY) minY = p[1];
    if (p[1] > maxY) maxY = p[1];
  }
  const k = Math.cos((((minY + maxY) / 2) * Math.PI) / 180);
  let best: [number, number] = [(minX + maxX) / 2, (minY + maxY) / 2];
  let bestD = inside(best[0], best[1], rings) ? edgeDist(best[0], best[1], rings, k) : -1;
  let sx = (maxX - minX) / 24;
  let sy = (maxY - minY) / 24;
  let x0 = minX;
  let y0 = minY;
  let nx = 25;
  let ny = 25;
  for (let round = 0; round < 3; round++) {
    for (let a = 0; a < nx; a++) {
      for (let b = 0; b < ny; b++) {
        const x = x0 + a * sx;
        const y = y0 + b * sy;
        if (!inside(x, y, rings)) continue;
        const d = edgeDist(x, y, rings, k);
        if (d > bestD) {
          bestD = d;
          best = [x, y];
        }
      }
    }
    x0 = best[0] - 2 * sx;
    y0 = best[1] - 2 * sy;
    sx /= 3;
    sy /= 3;
    nx = 13;
    ny = 13;
  }
  return best;
}

const cache = new WeakMap<Boundaries, LabelCollection>();

/** İlçe adı noktaları (MapLibre sembol katmanı kaynağı). Adlar Türkçe büyük harfle ("ŞEREFLİKOÇHİSAR"). */
export function districtLabels(b: Boundaries): LabelCollection {
  const hit = cache.get(b);
  if (hit) return hit;
  const features: LabelFeature[] = [];
  for (const f of b.ilceler.features) {
    const g = f.geometry;
    const polys: Ring[][] = g.type === 'Polygon' ? [g.coordinates] : g.coordinates;
    let bestPoly = polys[0];
    let bestA = -1;
    for (const p of polys) {
      const k = Math.cos(((p[0][0]?.[1] ?? 39.9) * Math.PI) / 180);
      const a = ringArea(p[0], k);
      if (a > bestA) {
        bestA = a;
        bestPoly = p;
      }
    }
    if (!bestPoly?.[0]?.length) continue;
    const name = f.properties.ilce;
    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: labelPoint(bestPoly) },
      properties: { ad: name.toLocaleUpperCase('tr-TR'), ad_tr: name, alan: f.properties.area_km2 ?? 0 },
    });
  }
  const out: LabelCollection = { type: 'FeatureCollection', features };
  cache.set(b, out);
  return out;
}

export const EMPTY_LABELS: LabelCollection = { type: 'FeatureCollection', features: [] };
