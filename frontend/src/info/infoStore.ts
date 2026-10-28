/**
 * Bilgi sekmelerinin küçük deposu (zustand) ve veri yükleyicileri.
 *
 *   - Veri sekmesinin alt bölümü (Ankara ölçüleri · Nüfus 2026 · Parametreler), parametre tablosunun süzgeçleri;
 *   - `pendingAnchor`: tembel yüklenen bir sekmenin içindeki çapaya (ör. `kaynak-<id>`, `param-<anahtar>`,
 *     `model-alt-sinir`) kaydırma isteği — sekme ve verisi gelince InfoTabs çapayı bulur, kaydırır, vurgular;
 *   - bir kez yüklenen sunucu verileri: /api/measures, /api/sources, p taraması (U eğrisinin dibi).
 *
 * Sunucu kapalıysa yükleyiciler derlemeye gömülü yedeği dener (`offline/<ad>.json`; build/offline.ts'in ürettiği
 * dosyalar — şu an sources.json var; measures.json ve p_sweep_3km.json yoksa sekme bunu açıkça söyler).
 */
import { create } from 'zustand';

import { getMeasures, getPSweep, getSources } from '../api/client.ts';
import type { MeasuresDetail } from '../api/infoTypes.ts';
import type { ParamStatus, PSweep, SourcesResponse } from '../api/types.ts';
import { useApp } from '../store/appStore.ts';

export type VeriSection = 'olculer' | 'nufus' | 'parametreler';

export interface LoadState<T> {
  status: 'idle' | 'loading' | 'ok' | 'error';
  data: T | null;
  error: string | null;
  /** veri gömülü yedekten mi geldi (sunucu kapalı) */
  fromBundle?: boolean;
}

const IDLE = { status: 'idle', data: null, error: null } as const;

/** /api/measures (ayrıntılı tip infoTypes.ts'te; `Measures` ile aynı dosya). */
export type MeasuresData = MeasuresDetail;

interface InfoState {
  veriSection: VeriSection;
  paramQuery: string;
  paramStatus: ParamStatus | 'hepsi';
  pendingAnchor: string | null;
  measures: LoadState<MeasuresData>;
  sources: LoadState<SourcesResponse>;
  sweeps: Partial<Record<3 | 5, LoadState<PSweep>>>;
  setVeriSection(s: VeriSection): void;
  setParamQuery(q: string): void;
  setParamStatus(s: ParamStatus | 'hepsi'): void;
  /** çapaya kaydır (sekme/veri hazır olunca); null → iptal */
  setPendingAnchor(a: string | null): void;
}

export const useInfo = create<InfoState>()((set) => ({
  veriSection: 'olculer',
  paramQuery: '',
  paramStatus: 'hepsi',
  pendingAnchor: null,
  measures: { ...IDLE },
  sources: { ...IDLE },
  sweeps: {},
  setVeriSection: (veriSection) => set({ veriSection }),
  setParamQuery: (paramQuery) => set({ paramQuery }),
  setParamStatus: (paramStatus) => set({ paramStatus }),
  setPendingAnchor: (pendingAnchor) => set({ pendingAnchor }),
}));

/* ------------------------------------------------------------------------------------------------ yükleyiciler */

async function bundled<T>(name: string): Promise<T | null> {
  try {
    const res = await fetch(`${import.meta.env.BASE_URL}offline/${name}`, { headers: { accept: 'application/json' } });
    if (!res.ok) return null;
    const ct = res.headers.get('content-type') ?? '';
    // SPA yedeği (index.html) JSON değildir
    if (!ct.includes('json')) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

function errText(ex: unknown): string {
  if (ex && typeof ex === 'object' && 'messageTr' in ex) return String((ex as { messageTr: string }).messageTr);
  return ex instanceof Error ? ex.message : String(ex);
}

/** Ölçüler yüklenince harita katmanı (ölçü çizgileri) açıksa haritayı yeniden çizdir. */
function touchMapIfMeasuresShown() {
  const st = useApp.getState();
  if (st.layers.measures) useApp.setState({ layers: { ...st.layers } });
}

let measuresPromise: Promise<void> | null = null;
export function loadMeasures(force = false): Promise<void> {
  const cur = useInfo.getState().measures;
  if (!force && (cur.status === 'ok' || measuresPromise)) return measuresPromise ?? Promise.resolve();
  useInfo.setState({ measures: { ...cur, status: 'loading', error: null } });
  measuresPromise = (async () => {
    try {
      const data = (await getMeasures()) as unknown as MeasuresData;
      useInfo.setState({ measures: { status: 'ok', data, error: null } });
    } catch (ex) {
      const b = await bundled<MeasuresData>('measures.json');
      if (b) useInfo.setState({ measures: { status: 'ok', data: b, error: null, fromBundle: true } });
      else useInfo.setState({ measures: { status: 'error', data: null, error: errText(ex) } });
    } finally {
      measuresPromise = null;
      touchMapIfMeasuresShown();
    }
  })();
  return measuresPromise;
}

let sourcesPromise: Promise<void> | null = null;
export function loadSources(force = false): Promise<void> {
  const cur = useInfo.getState().sources;
  if (!force && (cur.status === 'ok' || sourcesPromise)) return sourcesPromise ?? Promise.resolve();
  useInfo.setState({ sources: { ...cur, status: 'loading', error: null } });
  sourcesPromise = (async () => {
    try {
      const data = await getSources();
      useInfo.setState({ sources: { status: 'ok', data, error: null } });
    } catch (ex) {
      const b = await bundled<SourcesResponse>('sources.json');
      if (b) useInfo.setState({ sources: { status: 'ok', data: b, error: null, fromBundle: true } });
      else useInfo.setState({ sources: { status: 'error', data: null, error: errText(ex) } });
    } finally {
      sourcesPromise = null;
    }
  })();
  return sourcesPromise;
}

const sweepPromises: Partial<Record<3 | 5, Promise<void>>> = {};
export function loadSweep(km: 3 | 5, force = false): Promise<void> {
  const cur = useInfo.getState().sweeps[km] ?? { ...IDLE };
  if (!force && (cur.status === 'ok' || sweepPromises[km])) return sweepPromises[km] ?? Promise.resolve();
  const put = (s: LoadState<PSweep>) => useInfo.setState((st) => ({ sweeps: { ...st.sweeps, [km]: s } }));
  put({ ...cur, status: 'loading', error: null });
  const p = (async () => {
    try {
      put({ status: 'ok', data: await getPSweep(km), error: null });
    } catch (ex) {
      const b = await bundled<PSweep>(`p_sweep_${km}km.json`);
      if (b) put({ status: 'ok', data: b, error: null, fromBundle: true });
      else put({ status: 'error', data: null, error: errText(ex) });
    } finally {
      delete sweepPromises[km];
    }
  })();
  sweepPromises[km] = p;
  return p;
}

/** Ölçüler (harita katmanı için senkron okuma; yoksa null). */
export function measuresNow(): MeasuresData | null {
  return useInfo.getState().measures.data;
}
