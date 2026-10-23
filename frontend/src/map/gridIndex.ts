/**
 * Izgara dizini: GET /api/grid/{km} GeoJSON'undan bir kez türetilen hızlı erişim tabloları.
 * `grid_index` = GeoJSON özellik sırası (sunucudaki `layers.access` sırası ve `hospitals[].grid_index`).
 *
 * Hücreler eşit alanlı bir projeksiyondaki kare hücrelerin WGS84 karşılığıdır (4 köşe, ≈0,2° dönük); komşu
 * hücrelerin ortak köşeleri GeoJSON'da birebir aynıdır. Buradaki tablolar deck.gl katmanlarına düz (ikili)
 * diziler olarak verilir: her katman aynı `polygon(i)` görünümlerini kullanır, veri bir kez hazırlanır.
 */
import type { GridCells } from '../api/types.ts';

export interface GridIndex {
  km: number;
  n: number;
  /** hücre kimliği → grid_index */
  byId: Map<string, number>;
  /** grid_index → [boylam, enlem] merkez */
  centroid(i: number): [number, number];
  /** düz dizi: [lon0, lat0, lon1, lat1, …] (deck.gl ikili öznitelikleri için) */
  centroids: Float64Array;
  /** düz köşe dizisi: hücre i → [8i, 8i+8) = 4 köşe (GB, GD, KD, KB; kapanış noktası yok) */
  corners: Float64Array;
  /** hücre i'nin köşe görünümü (deck.gl `positionFormat: 'XY'` düz çokgen) */
  polygon(i: number): Float64Array;
  /** sütunlar için %80'e küçültülmüş köşeler (3B nüfus sütunları arasında boşluk) */
  columnPolygon(i: number): Float64Array;
  /** noktayı içeren hücre (yoksa −1) */
  cellAt(lon: number, lat: number): number;
  /** doğu ve kuzey komşusu (yoksa −1) — hizmet bölgesi sınırları için */
  east: Int32Array;
  north: Int32Array;
  /** i ile komşusu j'nin ortak kenarı [x1, y1, x2, y2] (ortak köşe yoksa null) */
  sharedEdge(i: number, j: number): [number, number, number, number] | null;
  /** hücre alanı (km²) — yoğunluk = nüfus / alan */
  cellAreaKm2: number;
}

const cache = new WeakMap<GridCells, GridIndex>();

function pointInQuad(c: Float64Array, o: number, x: number, y: number): boolean {
  let inside = false;
  for (let a = 0, b = 3; a < 4; b = a++) {
    const xa = c[o + 2 * a];
    const ya = c[o + 2 * a + 1];
    const xb = c[o + 2 * b];
    const yb = c[o + 2 * b + 1];
    if (ya > y !== yb > y && x < ((xb - xa) * (y - ya)) / (yb - ya) + xa) inside = !inside;
  }
  return inside;
}

export function gridIndexOf(cells: GridCells, km: number): GridIndex {
  const hit = cache.get(cells);
  if (hit) return hit;
  const n = cells.features.length;
  const centroids = new Float64Array(n * 2);
  const corners = new Float64Array(n * 8);
  const shrunk = new Float64Array(n * 8);
  const byId = new Map<string, number>();
  const byXY = new Map<string, number>();
  const xy = new Int32Array(n * 2).fill(-2147483648);
  let minLon = Infinity;
  let minLat = Infinity;
  let maxLon = -Infinity;
  let maxLat = -Infinity;

  cells.features.forEach((f, i) => {
    const id = f.properties.id;
    byId.set(id, i);
    // kimlik "3km_182_1429" → (sütun, satır)
    const parts = id.split('_');
    if (parts.length >= 3) {
      const a = Number(parts[parts.length - 2]);
      const b = Number(parts[parts.length - 1]);
      if (Number.isInteger(a) && Number.isInteger(b)) {
        xy[2 * i] = a;
        xy[2 * i + 1] = b;
        byXY.set(`${a}_${b}`, i);
      }
    }
    const ring = f.geometry.coordinates[0];
    // kapalı halkanın son noktası ilkine eşit: ortalamaya katma
    const m = ring.length > 1 && ring[0][0] === ring[ring.length - 1][0] && ring[0][1] === ring[ring.length - 1][1] ? ring.length - 1 : ring.length;
    let x = 0;
    let y = 0;
    for (let k = 0; k < m; k++) {
      x += ring[k][0];
      y += ring[k][1];
    }
    const cx = x / m;
    const cy = y / m;
    centroids[2 * i] = cx;
    centroids[2 * i + 1] = cy;
    for (let k = 0; k < 4; k++) {
      const p = ring[Math.min(k, m - 1)];
      corners[8 * i + 2 * k] = p[0];
      corners[8 * i + 2 * k + 1] = p[1];
      shrunk[8 * i + 2 * k] = cx + (p[0] - cx) * 0.8;
      shrunk[8 * i + 2 * k + 1] = cy + (p[1] - cy) * 0.8;
      if (p[0] < minLon) minLon = p[0];
      if (p[0] > maxLon) maxLon = p[0];
      if (p[1] < minLat) minLat = p[1];
      if (p[1] > maxLat) maxLat = p[1];
    }
  });

  const polys: Float64Array[] = new Array(n);
  const colPolys: Float64Array[] = new Array(n);
  for (let i = 0; i < n; i++) {
    polys[i] = corners.subarray(8 * i, 8 * i + 8);
    colPolys[i] = shrunk.subarray(8 * i, 8 * i + 8);
  }

  const east = new Int32Array(n).fill(-1);
  const north = new Int32Array(n).fill(-1);
  for (let i = 0; i < n; i++) {
    const a = xy[2 * i];
    if (a === -2147483648) continue;
    const b = xy[2 * i + 1];
    east[i] = byXY.get(`${a + 1}_${b}`) ?? -1;
    north[i] = byXY.get(`${a}_${b + 1}`) ?? -1;
  }

  // nokta → hücre: hücre boyutunda kovalar (her kovada ≈1 hücre), 3×3 komşu kovada çokgen içi denetimi
  const midLat = (minLat + maxLat) / 2;
  const dLat = km / 111.32;
  const dLon = km / (111.32 * Math.cos((midLat * Math.PI) / 180));
  const W = Math.max(1, Math.ceil((maxLon - minLon) / dLon) + 1);
  const H = Math.max(1, Math.ceil((maxLat - minLat) / dLat) + 1);
  const buckets = new Map<number, number[]>();
  for (let i = 0; i < n; i++) {
    const bx = Math.floor((centroids[2 * i] - minLon) / dLon);
    const by = Math.floor((centroids[2 * i + 1] - minLat) / dLat);
    const key = by * W + bx;
    const list = buckets.get(key);
    if (list) list.push(i);
    else buckets.set(key, [i]);
  }
  const cellAt = (lon: number, lat: number): number => {
    if (!(lon >= minLon && lon <= maxLon && lat >= minLat && lat <= maxLat)) return -1;
    const bx = Math.floor((lon - minLon) / dLon);
    const by = Math.floor((lat - minLat) / dLat);
    for (let dy = -1; dy <= 1; dy++) {
      const yy = by + dy;
      if (yy < 0 || yy >= H) continue;
      for (let dx = -1; dx <= 1; dx++) {
        const xx = bx + dx;
        if (xx < 0 || xx >= W) continue;
        const list = buckets.get(yy * W + xx);
        if (!list) continue;
        for (const i of list) if (pointInQuad(corners, 8 * i, lon, lat)) return i;
      }
    }
    return -1;
  };

  const sharedEdge = (i: number, j: number): [number, number, number, number] | null => {
    const out: number[] = [];
    for (let a = 0; a < 4 && out.length < 4; a++) {
      const x = corners[8 * i + 2 * a];
      const y = corners[8 * i + 2 * a + 1];
      for (let b = 0; b < 4; b++) {
        if (Math.abs(corners[8 * j + 2 * b] - x) < 1e-9 && Math.abs(corners[8 * j + 2 * b + 1] - y) < 1e-9) {
          out.push(x, y);
          break;
        }
      }
    }
    return out.length === 4 ? [out[0], out[1], out[2], out[3]] : null;
  };

  const idx: GridIndex = {
    km,
    n,
    byId,
    centroids,
    centroid: (i) => [centroids[2 * i], centroids[2 * i + 1]],
    corners,
    polygon: (i) => polys[i],
    columnPolygon: (i) => colPolys[i],
    cellAt,
    east,
    north,
    sharedEdge,
    cellAreaKm2: km * km,
  };
  cache.set(cells, idx);
  return idx;
}
