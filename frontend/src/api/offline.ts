/**
 * Çevrimdışı yedek (şartname 5 "Sağlamlık"; docs/API.md §8 notu).
 *
 * Bağlantı modları:
 *   - 'online'   : /api/health yanıt veriyor; canlı çözüm açık.
 *   - 'offline'  : /api/health yanıt vermiyor; sayfa önceden çözülmüş sonuçları gösterir
 *                  ("çevrimdışı mod — canlı çözüm kapalı" uyarısıyla), sağlık 5 sn'de bir yeniden denenir.
 *
 * Yedek sağlayıcılar sırayla denenir; ilk başarılı olanın verisi kullanılır (eksik parçalar sonrakilerden tamamlanır):
 *   1. 'sunucu-onceden' : HTTP katmanı ayakta ama sağlık/çözücü yanıt vermiyorsa /api/precomputed/*, /api/meta,
 *                         /api/boundaries, /api/grid/{km} (ör. işçi yeniden başlarken).
 *   2. 'gomulu'         : sunucu TAMAMEN kapalıyken de çalışan, derlemeye gömülü dosyalar (`/offline/*.json`:
 *                         varsayılan 3 km çözümü, 3 km ızgarası, sınırlar, /api/meta kopyası; build/offline.ts).
 *                         Servis çalışanı (sw.js) bunları ve sayfa kabuğunu ilk ziyarette önbelleğe alır: sunucu
 *                         kapanıp sayfa yeniden yüklense de varsayılan çözüm görünür.
 *
 * Bir sağlayıcı istenen ızgara/senaryo yerine KENDİ ızgarasının sonucunu verebilir (gömülü yedek yalnız 3 km
 * varsayılandır): birleştirilen pakette ızgara GeoJSON'u her zaman sonucun ızgarasıyla aynıdır (`gridKm`).
 */
import {
  ApiError,
  getBoundaries,
  getGrid,
  getHealth,
  getMeta,
  getScenarioSolution,
} from './client.ts';
import type { Boundaries, GridCells, GridKm, Health, Meta, SolveResultOk } from './types.ts';

export type ConnectionMode = 'connecting' | 'online' | 'offline';

export interface FallbackBundle {
  /** hangi sağlayıcıdan geldi (arayüzde rozet) */
  source: string;
  gridKm: GridKm;
  scenarioId: string;
  meta: Meta | null;
  result: SolveResultOk | null;
  boundaries: Boundaries | null;
  grid: GridCells | null;
  /** kullanıcıya gösterilecek açıklama */
  note_tr: string;
}

export interface FallbackProvider {
  id: string;
  label_tr: string;
  /**
   * Eksik parçalar null dönebilir; tamamen başarısızsa null. Hata fırlatmamalı. İstenenden başka bir ızgara ya da
   * senaryo veriyorsa `gridKm` / `scenarioId` alanlarında bildirir (yoksa istenenler varsayılır).
   */
  load(gridKm: GridKm, scenarioId: string): Promise<Partial<FallbackBundle> | null>;
}

interface Registered {
  provider: FallbackProvider;
  priority: number;
}

const registry: Registered[] = [];

/** Yedek sağlayıcı ekle (yüksek öncelik önce denenir). Aynı `id` yeniden eklenirse değiştirilir. */
export function registerFallbackProvider(provider: FallbackProvider, opts: { priority?: number } = {}): void {
  const i = registry.findIndex((r) => r.provider.id === provider.id);
  const entry = { provider, priority: opts.priority ?? 0 };
  if (i >= 0) registry[i] = entry;
  else registry.push(entry);
  registry.sort((a, b) => b.priority - a.priority);
}

export function fallbackProviders(): readonly FallbackProvider[] {
  return registry.map((r) => r.provider);
}

const settle = async <T>(p: Promise<T>): Promise<T | null> => {
  try {
    return await p;
  } catch {
    return null;
  }
};

/** 1. sağlayıcı: sunucunun önceden çözülmüş dosyaları (HTTP ayakta, çözücü değilse işe yarar). */
export const serverPrecomputedProvider: FallbackProvider = {
  id: 'sunucu-onceden',
  label_tr: 'sunucudaki önceden çözülmüş sonuç',
  async load(gridKm, scenarioId) {
    const opts = { timeoutMs: 8000 };
    const [result, meta, boundaries, grid] = await Promise.all([
      settle(getScenarioSolution(scenarioId, gridKm, opts)).then((r) => r ?? (scenarioId !== 'varsayilan' ? settle(getScenarioSolution('varsayilan', gridKm, opts)) : null)),
      settle(getMeta(opts)),
      settle(getBoundaries(opts)),
      settle(getGrid(gridKm, opts)),
    ]);
    if (!result && !meta) return null;
    return { result, meta, boundaries, grid, note_tr: 'Canlı çözücüye ulaşılamadı; önceden çözülmüş sonuç gösteriliyor.' };
  },
};

/**
 * Derlemeye gömülü yedek dosyalar (build/offline.ts üretir; `backend/static/offline/`). Yalnız varsayılan senaryo,
 * 3 km. `meta.json` derleme anındaki /api/meta kopyasıdır (Python bulunamazsa yoktur).
 */
export const BUNDLED_FALLBACK_FILES = {
  gridKm: 3 as GridKm,
  scenarioId: 'varsayilan',
  meta: 'offline/meta.json',
  sources: 'offline/sources.json',
  boundaries: 'offline/boundaries.json',
  grid: 'offline/grid_3km.json',
  result: 'offline/default_3km.json',
  manifest: 'offline/manifest.json',
} as const;

/** Gömülü dosyayı oku (sunucu kapalıyken servis çalışanının önbelleğinden gelir). Yoksa / bozuksa null. */
async function bundledJson<T>(rel: string, timeoutMs = 8000): Promise<T | null> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${import.meta.env.BASE_URL}${rel}`, { signal: ctrl.signal, headers: { accept: 'application/json' } });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/** 2. sağlayıcı: derlemeye gömülü varsayılan 3 km çözümü (sunucu tamamen kapalıyken). */
export const bundledProvider: FallbackProvider = {
  id: 'gomulu',
  label_tr: 'sayfaya gömülü varsayılan çözüm (3 km)',
  async load() {
    const f = BUNDLED_FALLBACK_FILES;
    const [result, meta, boundaries, grid] = await Promise.all([
      bundledJson<SolveResultOk>(f.result),
      bundledJson<Meta>(f.meta),
      bundledJson<Boundaries>(f.boundaries),
      bundledJson<GridCells>(f.grid),
    ]);
    if (!result && !meta) return null;
    return {
      gridKm: f.gridKm,
      scenarioId: f.scenarioId,
      result,
      meta,
      boundaries,
      grid,
      note_tr: 'Sunucuya ulaşılamıyor; sayfaya gömülü varsayılan çözüm (3 km, önceden çözülmüş) gösteriliyor.',
    };
  },
};

// sunucu ayaktaysa (yalnız çözücü yanıt vermiyorsa) önce sunucudaki, istenen ızgara/senaryonun sonucu
registerFallbackProvider(serverPrecomputedProvider, { priority: 10 });
registerFallbackProvider(bundledProvider, { priority: 0 });

/**
 * Kayıtlı sağlayıcıları öncelik sırasıyla dener ve parçaları birleştirir. Sonuç ilk sonuç veren sağlayıcıdandır;
 * ızgara GeoJSON'u yalnız sonucun ızgarasıyla aynı ızgarayı veren sağlayıcılardan alınır. Hiçbiri sonuç ya da meta
 * veremezse null.
 */
export async function loadFallback(gridKm: GridKm, scenarioId = 'varsayilan'): Promise<FallbackBundle | null> {
  const merged: FallbackBundle = { source: '', gridKm, scenarioId, meta: null, result: null, boundaries: null, grid: null, note_tr: '' };
  const grids: { km: GridKm; grid: GridCells }[] = [];
  for (const { provider } of registry) {
    const part = await settle(provider.load(gridKm, scenarioId));
    if (!part) continue;
    const km = part.gridKm ?? gridKm;
    if (!merged.result && part.result) {
      merged.result = part.result;
      merged.source = provider.id;
      merged.note_tr = part.note_tr ?? '';
      merged.gridKm = km;
      merged.scenarioId = part.scenarioId ?? scenarioId;
    }
    if (part.grid) grids.push({ km, grid: part.grid });
    merged.meta ??= part.meta ?? null;
    merged.boundaries ??= part.boundaries ?? null;
    if (merged.meta && merged.result && merged.boundaries && grids.some((g) => g.km === merged.gridKm)) break;
  }
  merged.grid = grids.find((g) => g.km === merged.gridKm)?.grid ?? null;
  return merged.result || merged.meta ? merged : null;
}

/** Sağlık yoklaması: yanıt veriyorsa Health, vermiyorsa null (hata fırlatmaz). */
export async function probeHealth(timeoutMs = 2500): Promise<Health | null> {
  try {
    const h = await getHealth({ timeoutMs });
    return h && h.ok ? h : null;
  } catch (ex) {
    if (ex instanceof ApiError && ex.kind === 'http' && ex.status >= 500) return null;
    return null;
  }
}

/**
 * Sunucu geri gelene dek sağlığı yoklar (çevrimdışı moddayken). `stop()` ile durur. Geri gelince `onBack` bir kez
 * çağrılır ve yoklama durur.
 */
export function watchHealth(onBack: (h: Health) => void, intervalMs = 5000): { stop(): void } {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const tick = async () => {
    if (stopped) return;
    const h = await probeHealth();
    if (stopped) return;
    if (h) {
      stopped = true;
      onBack(h);
      return;
    }
    timer = setTimeout(tick, intervalMs);
  };
  timer = setTimeout(tick, intervalMs);
  return {
    stop() {
      stopped = true;
      if (timer) clearTimeout(timer);
    },
  };
}
