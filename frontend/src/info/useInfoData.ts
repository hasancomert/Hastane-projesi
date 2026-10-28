/**
 * Bilgi sekmelerinin veri kancaları: bileşen takılınca bir kez yükler; sunucu çevrimdışıyken hata aldıysa sunucu
 * dönünce (bağlantı kipi 'online' olunca) yeniden dener. Ayrıca küçük yardımcılar: sınır kartı, parametre değeri.
 */
import { useEffect, useRef, useState } from 'react';

import { getBoundsCards } from '../api/client.ts';
import type { BoundsResult, GridKm, ParamMeta, ParamValue } from '../api/types.ts';
import { paramValue, useApp } from '../store/appStore.ts';
import { loadMeasures, loadSources, loadSweep, useInfo, type LoadState } from './infoStore.ts';

function useRetryOnline(state: LoadState<unknown>, load: (force?: boolean) => Promise<void>) {
  const mode = useApp((s) => s.connection.mode);
  const prev = useRef(mode);
  useEffect(() => {
    if (state.status === 'idle') void load();
  }, [state.status, load]);
  useEffect(() => {
    const was = prev.current;
    prev.current = mode;
    if (mode === 'online' && was !== 'online' && (state.status === 'error' || state.fromBundle)) void load(true);
  }, [mode, state.status, state.fromBundle, load]);
}

export function useMeasures() {
  const st = useInfo((s) => s.measures);
  useRetryOnline(st, loadMeasures);
  return st;
}

export function useSources() {
  const st = useInfo((s) => s.sources);
  useRetryOnline(st, loadSources);
  return st;
}

const loadSweep3 = (force?: boolean) => loadSweep(3, force);
const loadSweep5 = (force?: boolean) => loadSweep(5, force);
const IDLE = { status: 'idle', data: null, error: null } as const;

export function useSweep(km: 3 | 5) {
  const st = useInfo((s) => s.sweeps[km]) ?? IDLE;
  useRetryOnline(st, km === 3 ? loadSweep3 : loadSweep5);
  return st;
}

/* ------------------------------------------------------------------------------------------------ küçük yardımcılar */

const boundsCache = new Map<GridKm, Promise<BoundsResult | null>>();

/** Varsayılan parametrelerle önceden hesaplanmış sınır kartı (GET /api/bounds/{km}); yoksa null. */
export function useBoundsCard(km: GridKm): BoundsResult | null {
  const [card, setCard] = useState<BoundsResult | null>(null);
  useEffect(() => {
    let alive = true;
    let p = boundsCache.get(km);
    if (!p) {
      p = getBoundsCards(km).catch(() => {
        boundsCache.delete(km);
        return null;
      });
      boundsCache.set(km, p);
    }
    void p.then((c) => alive && setCard(c));
    return () => {
      alive = false;
    };
  }, [km]);
  return card;
}

/** Parametrenin kaydı ve ŞU ANKİ değeri (kullanıcı değişikliği → senaryo → kayıt). */
export function useParam(key: string): { p: ParamMeta | null; value: ParamValue | undefined } {
  const p = useApp((s) => s.meta?.parameters[key] ?? null);
  const value = useApp((s) => paramValue(s, key));
  return { p, value };
}

/** Sayısal parametre değeri (yoksa null). */
export function useNumParam(key: string): number | null {
  const v = useApp((s) => paramValue(s, key));
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}
