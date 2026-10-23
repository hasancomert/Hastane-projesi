/**
 * İş yardımcıları: gönder → (önbellekten değilse) SSE ile izle → sonuç. docs/API.md §4 ve §7'deki kurallar:
 *   - `replace: true` + kararlı `client_id` ile "en son istek kazanır": eski iş `cancelled` + `durduruldu` sonucuyla
 *     biter; arayüz yalnız beklediği işin `done` sonucunu "optimum" diye göstermelidir (çağıran `jobId`'yi karşılaştırır);
 *   - iptal edilen iş de `result` olayıyla biter (motorun o ana dek bulduğu en iyi çözüm);
 *   - SSE koparsa ve yeniden bağlanılamazsa son durum GET /api/jobs/{id} ile alınır.
 */
import {
  ApiError,
  cancelJob as apiCancelJob,
  getJob,
  jobEventsUrl,
  submitJob,
} from './client.ts';
import { followJobEvents } from './sse.ts';
import type {
  ApiValidationItem,
  JobRequestMap,
  JobResultMap,
  JobSnapshot,
  KnownJobType,
  SseHeartbeatData,
  SseStateData,
  TraceEvent,
} from './types.ts';

export type JobOutcome<R> =
  /** iş bitti (önbellekten de olabilir); `result.ok` false ise olursuz / hatalı çözüm (message_tr nedeni verir) */
  | { kind: 'done'; jobId: string; result: R; fromCache: boolean }
  /** iptal edildi ya da yerini yeni isteğe bıraktı; `result` motorun o ana dek bulduğu en iyi çözüm (varsa) */
  | { kind: 'cancelled'; jobId: string; result: R | null; reason: string | null; replacedBy: string | null }
  /** gönderilemedi (422/429/501/ağ) ya da iş `failed` oldu */
  | { kind: 'failed'; jobId: string | null; message: string; status?: number; items?: ApiValidationItem[]; retryAfterS?: number | null; unreachable?: boolean }
  /** çağıran `detach()` ile izlemeyi bıraktı (sunucudaki iş sürer) */
  | { kind: 'detached'; jobId: string | null };

export interface JobCallbacks {
  /** iş kabul edildi (202) ya da önbellekten döndü (200) */
  onSubmitted?: (snap: JobSnapshot) => void;
  onState?: (data: SseStateData) => void;
  /** ilerleme olayı: aşama, lb/ub/gap, t (dal-sınır yakınsaması için biriktirin) */
  onProgress?: (ev: TraceEvent) => void;
  onHeartbeat?: (data: SseHeartbeatData) => void;
  onReconnect?: (attempt: number, reason: string) => void;
}

export interface JobHandle<R> {
  /** gönderim yanıtı gelene dek null */
  readonly jobId: string | null;
  readonly done: Promise<JobOutcome<R>>;
  /** sunucuda iptal et; söz yine `cancelled` sonucuyla (en iyi çözümle) çözülür */
  cancel(): Promise<void>;
  /** yalnız izlemeyi bırak (ör. yeni bir istek bu işin yerini aldıysa) */
  detach(): void;
}

/** Bir işi başlatıp sonucuna dek izler. Söz hiçbir zaman reddedilmez; sonuç `JobOutcome` ile döner. */
export function runJob<T extends KnownJobType>(type: T, request: JobRequestMap[T], cb: JobCallbacks = {}): JobHandle<JobResultMap[T]> {
  type R = JobResultMap[T];
  const ctrl = new AbortController();
  let jobId: string | null = null;
  let detached = false;
  let cancelWanted = false;

  const done = (async (): Promise<JobOutcome<R>> => {
    let snap: JobSnapshot<R>;
    try {
      snap = (await submitJob(type, request, { signal: ctrl.signal })) as JobSnapshot<R>;
    } catch (ex) {
      if (detached) return { kind: 'detached', jobId: null };
      if (ex instanceof ApiError) {
        return { kind: 'failed', jobId: null, message: ex.messageTr, status: ex.status, items: ex.items, retryAfterS: ex.retryAfterS, unreachable: ex.isUnreachable };
      }
      return { kind: 'failed', jobId: null, message: String(ex) };
    }
    jobId = snap.job_id;
    cb.onSubmitted?.(snap as JobSnapshot);
    if (snap.state === 'done' && snap.result !== undefined) {
      return { kind: 'done', jobId: snap.job_id, result: snap.result, fromCache: snap.from_cache };
    }
    if (cancelWanted) void apiCancelJob(snap.job_id).catch(() => undefined);

    let outcome = null as JobOutcome<R> | null;           // olay geri çağrısında atanır
    const end = await followJobEvents<R>({
      url: snap.events_url || jobEventsUrl(snap.job_id),
      signal: ctrl.signal,
      onReconnect: cb.onReconnect,
      onEvent: (ev) => {
        switch (ev.event) {
          case 'state':
            cb.onState?.(ev.data);
            break;
          case 'progress':
            cb.onProgress?.(ev.data);
            break;
          case 'heartbeat':
            cb.onHeartbeat?.(ev.data);
            break;
          case 'result': {
            const d = ev.data;
            outcome = d.state === 'done'
              ? { kind: 'done', jobId: d.job_id, result: d.result, fromCache: d.from_cache }
              : { kind: 'cancelled', jobId: d.job_id, result: d.result ?? null, reason: d.cancel_reason_tr ?? d.error?.message_tr ?? null, replacedBy: null };
            break;
          }
          case 'error': {
            const d = ev.data;
            outcome = d.state === 'cancelled'
              ? { kind: 'cancelled', jobId: d.job_id, result: null, reason: d.message_tr, replacedBy: null }
              : { kind: 'failed', jobId: d.job_id, message: d.message_tr };
            break;
          }
        }
      },
    });
    if (outcome) return outcome;
    if (detached || end.reason === 'aborted') return { kind: 'detached', jobId };
    // SSE son olayı vermeden bitti: işin son halini oku (birkaç deneme)
    return await pollFinal<R>(snap.job_id, ctrl.signal);
  })();

  return {
    get jobId() {
      return jobId;
    },
    done,
    async cancel() {
      cancelWanted = true;
      if (jobId) {
        try {
          await apiCancelJob(jobId);
        } catch {
          /* bitmiş ya da bulunamayan iş: sonuç yine olay akışından gelir */
        }
      }
    },
    detach() {
      detached = true;
      ctrl.abort();
    },
  };
}

async function pollFinal<R>(jobId: string, signal: AbortSignal): Promise<JobOutcome<R>> {
  for (let i = 0; i < 20 && !signal.aborted; i++) {
    try {
      const s = await getJob<R>(jobId, true, { signal, timeoutMs: 5000 });
      if (s.state === 'done' && s.result !== undefined) return { kind: 'done', jobId, result: s.result, fromCache: s.from_cache };
      if (s.state === 'cancelled') return { kind: 'cancelled', jobId, result: s.result ?? null, reason: s.cancel_reason_tr ?? null, replacedBy: s.replaced_by };
      if (s.state === 'failed') return { kind: 'failed', jobId, message: s.error?.message_tr ?? 'İş başarısız oldu.' };
    } catch (ex) {
      if (ex instanceof ApiError && ex.status === 404) return { kind: 'failed', jobId, message: 'İş sunucuda bulunamadı (sunucu yeniden başlamış olabilir).', status: 404 };
      if (ex instanceof ApiError && ex.isUnreachable && i >= 3) return { kind: 'failed', jobId, message: ex.messageTr, unreachable: true };
    }
    await new Promise((r) => setTimeout(r, Math.min(500 * (i + 1), 3000)));
  }
  return signal.aborted ? { kind: 'detached', jobId } : { kind: 'failed', jobId, message: 'İşin sonucu alınamadı.' };
}

/* ------------------------------------------------------------------------------------------------ kısayollar */

export const solve = (req: JobRequestMap['solve'], cb?: JobCallbacks) => runJob('solve', req, cb);
export const evaluate = (req: JobRequestMap['evaluate'], cb?: JobCallbacks) => runJob('evaluate', req, cb);
export const bounds = (req: JobRequestMap['bounds'], cb?: JobCallbacks) => runJob('bounds', req, cb);
/** Faz 6'da etkinleşir (şimdilik 501 → 'failed'). */
export const explain = (req: JobRequestMap['explain'], cb?: JobCallbacks) => runJob('explain', req, cb);

/** Sekme boyunca kararlı istemci kimliği ("en son istek kazanır" ve istemci başına kuyruk sınırı için). */
export function makeClientId(prefix = 'arayuz'): string {
  const rnd = (globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2) + Date.now().toString(36)).replace(/[^A-Za-z0-9]/g, '');
  return `${prefix}-${rnd.slice(0, 16)}`;
}
