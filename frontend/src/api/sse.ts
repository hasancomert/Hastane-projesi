/**
 * İş olay akışı (SSE, GET /api/jobs/{id}/events) — fetch + ReadableStream ile.
 *
 * Neden EventSource değil: yeniden bağlanırken `Last-Event-ID` başlığını kendimiz gönderebilmek, AbortSignal ile
 * iptal, takılma (stall) tespiti ve 204 / 404 gibi durumları ayırt edebilmek için. Davranış docs/API.md §4 ile aynı:
 *   - `result` ya da `error` olayı SON olaydır → akış kapanır;
 *   - bitmiş iş + son olay zaten alınmış → 204 → 'gone';
 *   - iş yok → 404 → 'gone';
 *   - bağlantı koparsa (ağ hatası, sunucu yeniden başladı) üstel beklemeyle yeniden bağlanır; sunucu 2 sn'de bir
 *     `heartbeat` gönderir, `stallTimeoutMs` boyunca hiç bayt gelmezse bağlantı yeniden kurulur.
 * Aynı `id`'li olay iki kez iletilmez (yeniden bağlanmada tekrar gelenler atlanır).
 */
import type { JobEvent } from './types.ts';
import { apiUrl } from './client.ts';

export type SseEndReason = 'final' | 'gone' | 'aborted' | 'gave_up';

export interface SseEnd {
  reason: SseEndReason;
  /** son alınan olay kimliği (yeniden bağlanma için) */
  lastEventId: number | null;
  /** 'gave_up' / 'gone' durumunda açıklama */
  detail?: string;
}

export interface FollowOptions<R = unknown> {
  url: string;
  onEvent: (ev: JobEvent<R>) => void;
  signal?: AbortSignal;
  /** önceki bağlantıdan kalan son olay kimliği */
  lastEventId?: number | null;
  /** art arda başarısız bağlantı denemesi sınırı (varsayılan 8 ≈ 30 sn) */
  maxRetries?: number;
  /** hiç bayt gelmezse yeniden bağlanma süresi (ms; varsayılan 12 sn — sunucu 2 sn'de bir kalp atışı yollar) */
  stallTimeoutMs?: number;
  onReconnect?: (attempt: number, reason: string) => void;
}

const FINAL_EVENTS = new Set(['result', 'error']);

function backoff(attempt: number): number {
  return Math.min(500 * 2 ** attempt, 5000) + Math.random() * 200;
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal?.aborted) return resolve();
    const t = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => {
      clearTimeout(t);
      resolve();
    }, { once: true });
  });
}

/** SSE metin bloklarını ayrıştırır ("event:", "data:", "id:"; ":" ile başlayan satırlar yorum/ping). */
export class SseParser {
  private buf = '';

  /** Yeni metin parçası ekle; tamamlanan olayları döndür. */
  push(chunk: string): { event: string; data: string; id: string | null }[] {
    this.buf += chunk.replace(/\r\n?/g, '\n');
    const out: { event: string; data: string; id: string | null }[] = [];
    let sep: number;
    while ((sep = this.buf.indexOf('\n\n')) >= 0) {
      const block = this.buf.slice(0, sep);
      this.buf = this.buf.slice(sep + 2);
      let event = 'message';
      let id: string | null = null;
      const data: string[] = [];
      for (const line of block.split('\n')) {
        if (!line || line.startsWith(':')) continue;
        const colon = line.indexOf(':');
        const field = colon < 0 ? line : line.slice(0, colon);
        let value = colon < 0 ? '' : line.slice(colon + 1);
        if (value.startsWith(' ')) value = value.slice(1);
        if (field === 'event') event = value;
        else if (field === 'data') data.push(value);
        else if (field === 'id') id = value;
      }
      if (data.length) out.push({ event, data: data.join('\n'), id });
    }
    return out;
  }
}

/**
 * Bir işin olaylarını son olaya (`result`/`error`) dek izler. Dönen söz hiçbir zaman reddedilmez; nedeni `SseEnd`
 * verir. 'gave_up' ya da 'gone' durumunda çağıran işin son halini GET /api/jobs/{id} ile almalıdır (jobs.ts yapar).
 */
export async function followJobEvents<R = unknown>(opts: FollowOptions<R>): Promise<SseEnd> {
  const { onEvent, signal } = opts;
  const url = apiUrl(opts.url);
  const maxRetries = opts.maxRetries ?? 8;
  const stallMs = opts.stallTimeoutMs ?? 12_000;
  let lastId: number | null = opts.lastEventId ?? null;
  let failures = 0;
  let lastReason = '';

  while (!signal?.aborted) {
    const ctrl = new AbortController();
    const onAbort = () => ctrl.abort();
    signal?.addEventListener('abort', onAbort, { once: true });
    let stallTimer: ReturnType<typeof setTimeout> | null = null;
    let stalled = false;
    const armStall = () => {
      if (stallTimer) clearTimeout(stallTimer);
      stallTimer = setTimeout(() => {
        stalled = true;
        ctrl.abort();
      }, stallMs);
    };
    try {
      armStall();
      const headers: Record<string, string> = { accept: 'text/event-stream' };
      if (lastId !== null) headers['last-event-id'] = String(lastId);
      const res = await fetch(url, { headers, signal: ctrl.signal, cache: 'no-store' });
      if (res.status === 204) return { reason: 'gone', lastEventId: lastId, detail: 'iş bitmiş; son olay daha önce alındı' };
      if (res.status === 404) return { reason: 'gone', lastEventId: lastId, detail: 'iş bulunamadı' };
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
      const parser = new SseParser();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        armStall();
        for (const raw of parser.push(value)) {
          const id = raw.id !== null && /^\d+$/.test(raw.id) ? Number(raw.id) : null;
          if (id !== null) {
            if (lastId !== null && id <= lastId) continue;           // yeniden bağlanmada tekrar gelen olay
            lastId = id;
          }
          failures = 0;                                              // olay geldi: bağlantı sağlıklı
          let data: unknown;
          try {
            data = JSON.parse(raw.data);
          } catch {
            continue;
          }
          onEvent({ event: raw.event, id, data } as JobEvent<R>);
          if (FINAL_EVENTS.has(raw.event)) {
            reader.cancel().catch(() => undefined);
            return { reason: 'final', lastEventId: lastId };
          }
        }
      }
      lastReason = 'bağlantı son olaydan önce kapandı';
      failures += 1;
    } catch (ex) {
      if (signal?.aborted) break;
      lastReason = stalled ? 'olay gelmedi (takılma)' : ex instanceof Error ? ex.message : String(ex);
      failures += 1;
    } finally {
      if (stallTimer) clearTimeout(stallTimer);
      signal?.removeEventListener('abort', onAbort);
    }
    if (signal?.aborted) break;
    if (failures > maxRetries) return { reason: 'gave_up', lastEventId: lastId, detail: lastReason };
    opts.onReconnect?.(failures, lastReason);
    await sleep(backoff(failures), signal);
  }
  return { reason: 'aborted', lastEventId: lastId };
}
