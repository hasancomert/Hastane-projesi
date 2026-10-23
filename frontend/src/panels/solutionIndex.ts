/**
 * Çözüm sonucundan kartların ihtiyaç duyduğu hızlı arama tabloları (her çözüm nesnesi için bir kez, WeakMap):
 *   - hücre kimliği → talep sırası (layers.demand, duals aynı sırada), aday sırası (layers.candidates),
 *     o hücrenin atama yayları (assignment), o hücredeki açık hastane;
 *   - hastane sırası → hizmet bölgesindeki hücre sayısı (layers.demand.service_hospital).
 */
import type { SolveResultOk } from '../api/types.ts';

export interface SolutionIndex {
  demandPos: Map<string, number>;
  candPos: Map<string, number>;
  /** hücre → atama yaylarının sırası (assignment dizilerinde) */
  arcsByCell: Map<string, number[]>;
  /** hücre → o hücrede açık hastanenin sırası */
  hospitalByCell: Map<string, number>;
  /** hastane sırası → hizmet bölgesindeki talep hücresi sayısı */
  servedCells: number[];
  /** duals.demand_cell sırası (talep sırasından farklıysa) */
  dualPos: Map<string, number> | null;
}

const cache = new WeakMap<SolveResultOk, SolutionIndex>();

export function solutionIndex(r: SolveResultOk): SolutionIndex {
  const hit = cache.get(r);
  if (hit) return hit;
  const demandPos = new Map<string, number>();
  r.layers.demand.cell_id.forEach((c, i) => demandPos.set(c, i));
  const candPos = new Map<string, number>();
  r.layers.candidates.cell_id.forEach((c, i) => candPos.set(c, i));
  const arcsByCell = new Map<string, number[]>();
  r.assignment.demand_cell.forEach((c, i) => {
    const a = arcsByCell.get(c);
    if (a) a.push(i);
    else arcsByCell.set(c, [i]);
  });
  const hospitalByCell = new Map<string, number>();
  r.hospitals.forEach((h, i) => hospitalByCell.set(h.cell_id, i));
  const servedCells = new Array<number>(r.hospitals.length).fill(0);
  for (const h of r.layers.demand.service_hospital) if (h >= 0 && h < servedCells.length) servedCells[h] += 1;
  let dualPos: Map<string, number> | null = null;
  const dc = r.duals?.demand_cell;
  if (dc && (dc.length !== r.layers.demand.cell_id.length || dc.some((c, i) => c !== r.layers.demand.cell_id[i]))) {
    dualPos = new Map();
    dc.forEach((c, i) => dualPos!.set(c, i));
  }
  const idx: SolutionIndex = { demandPos, candPos, arcsByCell, hospitalByCell, servedCells, dualPos };
  cache.set(r, idx);
  return idx;
}

/** Hücrenin gölge fiyatı (TL/kişi ve TL) — dual yoksa null. */
export function cellDual(r: SolveResultOk, cellId: string): { perPerson: number; total: number } | null {
  if (!r.duals) return null;
  const idx = solutionIndex(r);
  const i = idx.dualPos ? idx.dualPos.get(cellId) : idx.demandPos.get(cellId);
  if (i === undefined) return null;
  const pp = r.duals.marginal_cost_per_person_tl[i];
  const t = r.duals.marginal_cost_tl[i];
  if (!Number.isFinite(pp) && !Number.isFinite(t)) return null;
  return { perPerson: pp, total: t };
}
