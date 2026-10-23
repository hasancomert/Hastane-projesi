/**
 * Haritada gösterilen çözüm (harita, lejant, ipucu ve menü aynı çözümü kullanır):
 *   - son sonuç olurluysa o;
 *   - son sonuç OLURSUZSA (ör. bir yasak R kapsamasını bozdu) son olurlu çözüm — soluk ("eski") gösterilir,
 *     nedeni sonuç panelinde yazar; harita boşalmaz;
 *   - ızgara değiştiyse ve yeni ızgaranın çözümü henüz yoksa null.
 */
import type { SolveResultOk } from '../api/types.ts';
import { selectOkResult, selectResultIsStale, useApp } from '../store/appStore.ts';

let lastOk: SolveResultOk | null = null;

export interface ShownSolution {
  solution: SolveResultOk | null;
  /** son sonuç olursuz / hatalı */
  failed: boolean;
  /** gösterilen çözüm güncel problemin çözümü değil (soluk çiz) */
  stale: boolean;
}

export function useShownSolution(): ShownSolution {
  const ok = useApp(selectOkResult);
  const failed = useApp((s) => !!s.result && !s.result.ok);
  const staleKey = useApp(selectResultIsStale);
  const gridKm = useApp((s) => s.problem.gridKm);
  if (ok) lastOk = ok;
  const r = ok ?? (failed ? lastOk : null);
  const solution = r && r.grid_km === gridKm ? r : null;
  return { solution, failed, stale: staleKey || (failed && !!solution) };
}
