/**
 * Canlı çözüm denetleyicisi: gecikmeli (debounce) otomatik çözüm, "en son istek kazanır", iptal.
 *
 * Kurallar (docs/API.md §4, §7):
 *   - her istek aynı `client_id` (sekme boyunca kararlı) ve `replace: true` taşır → sunucu aynı istemcinin eski
 *     işini iptal eder; arayüz eski işin olay akışını bırakır (detach) ve yalnız son işin sonucunu gösterir;
 *   - iptal edilen işin `durduruldu` sonucu "en iyi bulunan çözüm" olarak gösterilir, "optimum" denmez
 *     (resultInfo.stopped = true);
 *   - 429 (kuyruk dolu) → Retry-After kadar sonra bir kez yeniden denenir;
 *   - ağ hatası → çevrimdışı moda geçilir (bootstrap.goOffline).
 */
import { makeClientId, solve as runSolve, type JobHandle } from '../api/jobs.ts';
import type { SolveRequest, SolveResult } from '../api/types.ts';
import { problemKey, selectCanSolve, useApp, type AppState } from './appStore.ts';

/** Son değişiklikten sonra çözümün başlaması için beklenen süre (ms) — kaydırıcı ve sayı kutusu değişiklikleri. */
export const AUTO_SOLVE_DELAY_MS = 600;
/** Kesikli seçimler (ızgara, senaryo, amaç modu, nüfus yılı) beklemeden çözülür: tek tıklama, ardı gelmez. */
export const DISCRETE_SOLVE_DELAY_MS = 0;

/** Sekme boyunca kararlı istemci kimliği. */
export const CLIENT_ID = makeClientId('arayuz');

let current: JobHandle<SolveResult> | null = null;
let debounceTimer: ReturnType<typeof setTimeout> | null = null;
let retryTimer: ReturnType<typeof setTimeout> | null = null;
let offlineHook: ((reason: string) => void) | null = null;
/** kaydırıcı sürüklenirken otomatik çözüm bekletilir (holdAutoSolve / releaseAutoSolve) */
let held = 0;
let heldPending = false;
let heldDelay = AUTO_SOLVE_DELAY_MS;

/** bootstrap.ts çevrimdışı geçişini buraya bağlar (döngüsel içe aktarmayı önlemek için). */
export function setOfflineHook(fn: (reason: string) => void): void {
  offlineHook = fn;
}

/** Depodaki problemden çözüm isteği. Boş kilitler ve varsayılan alanlar gönderilmez. */
export function buildSolveRequest(s: Pick<AppState, 'problem'>, extra: Partial<SolveRequest> = {}): SolveRequest {
  const p = s.problem;
  const req: SolveRequest = {
    grid_km: p.gridKm,
    overrides: p.overrides,
    client_id: CLIENT_ID,
    replace: true,
    duals: true,
  };
  if (p.locks.open.length || p.locks.closed.length) req.locks = p.locks;
  if (p.allowOutsideRange) req.allow_outside_range = true;
  return { ...req, ...extra };
}

function clearTimers() {
  if (debounceTimer) clearTimeout(debounceTimer);
  if (retryTimer) clearTimeout(retryTimer);
  debounceTimer = null;
  retryTimer = null;
}

/** Şimdi çöz (otomatik çözümün çağırdığı fonksiyon; "Yeniden çöz" düğmesi de bunu çağırır). */
export async function solveNow(opts: { useCache?: boolean; timeLimit?: number } = {}): Promise<void> {
  clearTimers();
  const s = useApp.getState();
  if (!selectCanSolve(s)) return;
  const key = problemKey(s.problem);
  current?.detach();

  s.setJob({
    jobId: null,
    kind: 'solve',
    phase: 'submitting',
    problemKey: key,
    startedAt: Date.now(),
    position: null,
    progress: null,
    trace: [],
    runS: null,
    reconnects: 0,
  });

  let jobId: string | null = null;
  const mine = () => current === handle;
  const patch = (p: Parameters<AppState['patchJob']>[1]) => {
    if (mine()) useApp.setState((st) => (st.job ? { job: { ...st.job, ...p } } : {}));
  };

  const req = buildSolveRequest(s, {
    ...(opts.useCache === false ? { use_cache: false } : {}),
    ...(opts.timeLimit ? { time_limit: opts.timeLimit } : {}),
  });
  const handle: JobHandle<SolveResult> = runSolve(req, {
    onSubmitted: (snap) => {
      jobId = snap.job_id;
      patch({ jobId: snap.job_id, phase: snap.state === 'queued' ? 'queued' : 'running', position: snap.position });
    },
    onState: (d) => {
      if (d.cancel_requested) patch({ phase: 'cancelling' });
      else patch({ phase: d.state === 'queued' ? 'queued' : 'running', position: d.position ?? null });
    },
    onProgress: (ev) => {
      if (mine() && jobId) useApp.getState().pushProgress(jobId, ev);
    },
    onHeartbeat: (d) => patch({ runS: d.run_s, position: d.position }),
    onReconnect: (n) => patch({ reconnects: n }),
  });
  current = handle;

  const out = await handle.done;
  if (!mine()) return;                                 // yerini yeni bir istek aldı
  current = null;
  const st = useApp.getState();
  const trace = st.job?.trace ?? [];
  switch (out.kind) {
    case 'done':
      st.setResult(out.result, {
        kind: 'solve',
        source: out.fromCache ? 'cache' : 'live',
        jobId: out.jobId,
        problemKey: key,
        receivedAt: Date.now(),
        stopped: false,
        stopReason: null,
        liveTrace: trace,
      });
      st.setJob(null);
      break;
    case 'cancelled':
      if (out.result && out.result.ok) {
        st.setResult(out.result, {
          kind: 'solve',
          source: 'live',
          jobId: out.jobId,
          problemKey: key,
          receivedAt: Date.now(),
          stopped: true,
          stopReason: out.reason,
          liveTrace: trace,
        });
      }
      st.setJob(null);
      break;
    case 'failed':
      st.setJob(null);
      if (out.unreachable) {
        offlineHook?.(out.message);
        break;
      }
      st.setError({ message: out.message, items: out.items, at: Date.now() });
      if (out.status === 429) {
        retryTimer = setTimeout(() => void solveNow(opts), (out.retryAfterS ?? 5) * 1000);
      }
      break;
    case 'detached':
      break;
  }
}

/** Çalışan çözümü iptal et: motor en iyi çözümüyle durur ('durduruldu'), arayüz onu "en iyi bulunan" diye gösterir. */
export async function cancelSolve(): Promise<void> {
  clearTimers();
  const s = useApp.getState();
  if (!current) {
    if (s.job?.phase === 'debounce') s.setJob(null);
    return;
  }
  if (s.job) s.setJob({ ...s.job, phase: 'cancelling' });
  await current.cancel();
}

/**
 * Kaydırıcı sürüklenmeye başlayınca çağrılır: bekleyen gecikmeli çözüm durdurulur ve sürükleme bitene dek yenisi
 * kurulmaz (önceki sürüklemenin sayacı sürükleme ortasında dolup eski değeri göndermesin; sunucu o işi hemen
 * iptal etmek zorunda kalıyordu). releaseAutoSolve bekleyen çözümü yeniden planlar.
 */
export function holdAutoSolve(): void {
  held += 1;
  if (held > 1) return;
  if (debounceTimer) {
    clearTimeout(debounceTimer);
    debounceTimer = null;
    heldPending = true;
  }
}

/** Sürükleme bitti: bekletilen (ya da sürükleme sırasında istenen) çözümü yeniden planla. */
export function releaseAutoSolve(): void {
  if (held === 0) return;
  held -= 1;
  if (held > 0 || !heldPending) return;
  heldPending = false;
  scheduleSolve(heldDelay);
}

/** Gecikmeli çözüm planla (her çağrı sayacı sıfırlar). */
export function scheduleSolve(delayMs = AUTO_SOLVE_DELAY_MS): void {
  const s = useApp.getState();
  if (!selectCanSolve(s)) return;
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = null;
  if (held > 0) {
    // sürükleme sürüyor: bırakılınca planlanır
    heldPending = true;
    heldDelay = delayMs;
    if (!current && !s.job) {
      s.setJob({
        jobId: null,
        kind: 'solve',
        phase: 'debounce',
        problemKey: problemKey(s.problem),
        startedAt: Date.now(),
        position: null,
        progress: null,
        trace: [],
        runS: null,
        reconnects: 0,
      });
    }
    return;
  }
  if (!current) {
    s.setJob({
      jobId: null,
      kind: 'solve',
      phase: 'debounce',
      problemKey: problemKey(s.problem),
      startedAt: Date.now(),
      position: null,
      progress: null,
      trace: [],
      runS: null,
      reconnects: 0,
    });
  }
  debounceTimer = setTimeout(() => {
    debounceTimer = null;
    void solveNow();
  }, delayMs);
}

/** Çözüm sürüyor mu (gönderim, kuyruk, çalışma, iptal)? */
export function isSolving(): boolean {
  return current !== null;
}

/**
 * Otomatik çözüm aboneliği: problem anahtarı değişince (parametre, senaryo, ızgara, kilit) ve `ui.autoSolve`
 * açıkken gecikmeli çözüm planlar. Dönen fonksiyon aboneliği kaldırır.
 */
export function startAutoSolve(): () => void {
  let last = useApp.getState().problem;
  let lastKey = problemKey(last);
  return useApp.subscribe((s) => {
    const key = problemKey(s.problem);
    if (key === lastKey) return;
    const prev = last;
    last = s.problem;
    lastKey = key;
    if (!s.ui.autoSolve) return;
    const p = s.problem;
    const discrete =
      p.gridKm !== prev.gridKm ||
      p.scenarioId !== prev.scenarioId ||
      p.overrides['solver.objective_mode'] !== prev.overrides['solver.objective_mode'] ||
      p.overrides['population.scenario_year'] !== prev.overrides['population.scenario_year'];
    scheduleSolve(discrete ? DISCRETE_SOLVE_DELAY_MS : AUTO_SOLVE_DELAY_MS);
  });
}
