/**
 * Açılış sırası ve yan etkiler.
 *
 *   1. /api/health (2,5 sn zaman aşımı)
 *   2a. çevrimiçi → /api/meta → (paralel) sınırlar + geçerli ızgara → ilk çözüm (varsayılan parametreler önbellekten
 *       ≈70 ms'de döner, şartname 6.6 "< 3 sn")
 *   2b. çevrimdışı → yedek sağlayıcılar (api/offline.ts) → önceden çözülmüş sonuç + "çevrimdışı mod" uyarısı;
 *       sağlık 5 sn'de bir yoklanır, sunucu dönünce çevrimiçine geçilir ve problem yeniden çözülür.
 *
 * `startAppEffects()` main.tsx'ten BİR KEZ çağrılır (React dışında; StrictMode çift çağrısından etkilenmez).
 */
import { ApiError, getBoundaries, getGrid, getMeta } from '../api/client.ts';
import { loadFallback, probeHealth, watchHealth } from '../api/offline.ts';
import type { GridKm } from '../api/types.ts';
import { problemKey, useApp } from './appStore.ts';
import { setOfflineHook, solveNow, startAutoSolve } from './solver.ts';
import { restoreProblemFromUrl, startUrlSync } from './urlState.ts';

let healthWatch: { stop(): void } | null = null;
let urlRestored = false;

const msg = (ex: unknown) => (ex instanceof ApiError ? ex.messageTr : ex instanceof Error ? ex.message : String(ex));

/** Izgara GeoJSON'unu bir kez yükler (zaten varsa ya da yükleniyorsa hiçbir şey yapmaz). */
export async function ensureGrid(km: GridKm): Promise<void> {
  const s = useApp.getState();
  if (s.grids[km] || s.gridLoading[km]) return;
  s.setGridLoading(km, true);
  try {
    s.setGrid(km, await getGrid(km));
  } catch (ex) {
    useApp.getState().setGridLoading(km, false);
    if (ex instanceof ApiError && ex.isUnreachable) goOffline(ex.messageTr);
  }
}

export async function ensureBoundaries(): Promise<void> {
  if (useApp.getState().boundaries) return;
  try {
    useApp.getState().setBoundaries(await getBoundaries());
  } catch {
    /* sınırlar yedekten de gelebilir; harita sınırsız da çalışır */
  }
}

/** Sunucuya ulaşılamıyor: yedeğe geç ve sağlığı yokla. */
export function goOffline(reason: string): void {
  stopOnlineWatch();
  const s = useApp.getState();
  if (s.connection.mode !== 'offline') {
    s.setConnection({ mode: 'offline', note: `Çevrimdışı mod — canlı çözüm kapalı. (${reason})` });
  }
  s.setJob(null);
  void applyFallback();
  healthWatch?.stop();
  healthWatch = watchHealth((h) => {
    healthWatch = null;
    void goOnline(h);
  });
}

let fallbackLoading: Promise<void> | null = null;

/** Yedek sonucu uygula (eşzamanlı çağrılar tek yüklemede birleşir). */
function applyFallback(): Promise<void> {
  fallbackLoading ??= doApplyFallback().finally(() => {
    fallbackLoading = null;
  });
  return fallbackLoading;
}

async function doApplyFallback(): Promise<void> {
  const s = useApp.getState();
  const bundle = await loadFallback(s.problem.gridKm, s.problem.scenarioId);
  if (!bundle) return;
  const st = useApp.getState();
  if (bundle.meta && !st.meta) st.setMeta(bundle.meta);
  if (bundle.boundaries && !st.boundaries) st.setBoundaries(bundle.boundaries);
  // ızgara sonuçtan ÖNCE: problem ızgarası değişince ensureGrid sunucuya gitmesin
  if (bundle.grid && !st.grids[bundle.gridKm]) st.setGrid(bundle.gridKm, bundle.grid);
  const cur = useApp.getState();
  if (bundle.result && !cur.result) {
    // Yedek sonuç kendi ızgarası + senaryosu içindir (gömülü yedek: 3 km varsayılan). Gösterilen problem ona
    // eşitlenir: sonuç "eski" sayılmaz, harita doğru ızgarayı çizer. (Bu yalnız henüz hiç sonuç yokken olur —
    // çevrimdışı açılış —; kullanıcının değişiklikleri yoktur. Çevrimiçiyken alınmış sonuç olduğu gibi kalır.)
    const sc = (cur.meta ?? bundle.meta)?.scenarios.find((x) => x.id === bundle.scenarioId);
    const overrides = { ...(sc?.overrides ?? {}) };
    delete overrides['grid.cell_km'];
    const problem = { ...cur.problem, gridKm: bundle.gridKm, scenarioId: sc ? bundle.scenarioId : cur.problem.scenarioId, overrides, locks: { open: [], closed: [] } };
    useApp.setState({ problem });
    useApp.getState().setResult(bundle.result, {
      kind: 'solve',
      source: 'fallback',
      jobId: null,
      problemKey: problemKey(problem),
      receivedAt: Date.now(),
      stopped: false,
      stopReason: null,
      liveTrace: [],
    });
  }
  useApp.getState().setConnection({ fallbackSource: bundle.source || null, note: useApp.getState().connection.note ?? bundle.note_tr });
}

/**
 * Çevrimiçiyken sunucu nabzı: sunucu boştayken kapanırsa (çözüm ya da istek yokken) da sayfa bunu fark etsin —
 * 10 sn'de bir /api/health; art arda iki yanıtsızlıkta çevrimdışı moda geçilir. Sekme gizliyken yoklanmaz.
 */
let onlineWatch: ReturnType<typeof setInterval> | null = null;
const ONLINE_PING_MS = 10_000;

function startOnlineWatch(): void {
  if (onlineWatch) return;
  let misses = 0;
  let busy = false;
  onlineWatch = setInterval(() => {
    const st = useApp.getState();
    if (st.connection.mode !== 'online') {
      stopOnlineWatch();
      return;
    }
    if (busy || (typeof document !== 'undefined' && document.visibilityState === 'hidden')) return;
    busy = true;
    void probeHealth()
      .then((h) => {
        if (h) {
          misses = 0;
          if (useApp.getState().connection.mode === 'online') useApp.getState().setConnection({ health: h });
        } else if (++misses >= 2) {
          goOffline('sunucu yanıt vermiyor');
        }
      })
      .finally(() => {
        busy = false;
      });
  }, ONLINE_PING_MS);
}

function stopOnlineWatch(): void {
  if (onlineWatch) clearInterval(onlineWatch);
  onlineWatch = null;
}

async function goOnline(health: NonNullable<Awaited<ReturnType<typeof probeHealth>>>): Promise<void> {
  const s = useApp.getState();
  s.setConnection({ mode: 'online', health, note: null, fallbackSource: null });
  startOnlineWatch();
  if (!s.meta) {
    try {
      useApp.getState().setMeta(await getMeta());
    } catch (ex) {
      useApp.getState().setMetaError(msg(ex));
      return;
    }
    // adresteki problem (#izgara=…&senaryo=…&p.…): yeniden yüklemede durum kaybolmasın
    restoreProblemFromUrl();
  } else if (!urlRestored && s.connection.fallbackSource === null && !s.result) {
    restoreProblemFromUrl();
  }
  urlRestored = true;
  void ensureBoundaries();
  void ensureGrid(useApp.getState().problem.gridKm);
  await solveNow();
}

/** Açılış: bağlantı, üst veri, ilk çözüm. */
export async function bootstrap(): Promise<void> {
  const health = await probeHealth();
  if (!health) {
    goOffline('sunucu yanıt vermedi');
    return;
  }
  await goOnline(health);
}

/** Tüm yan etkileri başlatır (otomatik çözüm, ızgara yükleme, çevrimdışı kancası) ve açılışı tetikler. */
export function startAppEffects(): () => void {
  setOfflineHook(goOffline);
  const stopAuto = startAutoSolve();
  const stopUrl = startUrlSync();
  let lastKm = useApp.getState().problem.gridKm;
  const stopGrid = useApp.subscribe((s) => {
    if (s.problem.gridKm !== lastKm) {
      lastKm = s.problem.gridKm;
      void ensureGrid(lastKm);
    }
  });
  void bootstrap();
  return () => {
    stopAuto();
    stopUrl();
    stopGrid();
    healthWatch?.stop();
    stopOnlineWatch();
  };
}
