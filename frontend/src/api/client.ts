/**
 * Yerel FastAPI sunucusunun tipli istemcisi (docs/API.md §2). Her uç nokta için bir fonksiyon; hepsi aynı kökene
 * (sunumda FastAPI, geliştirmede Vite vekili) gider — hiçbir istek 127.0.0.1 dışına çıkmaz.
 *
 * Hatalar `ApiError` olarak fırlatılır: `kind` ağ / zaman aşımı / HTTP / ayrıştırma ayrımını, `messageTr`
 * sunucunun Türkçe iletisini (yoksa Türkçe bir yedeği), `items` 422 doğrulama ayrıntılarını verir.
 */
import type {
  ApiErrorBody,
  ApiValidationItem,
  Boundaries,
  BoundsRequest,
  BoundsResult,
  EvaluateRequest,
  EvaluateResult,
  ExplainRequest,
  ExplainResult,
  GridCells,
  GridKm,
  Health,
  JobSnapshot,
  KnownJobType,
  JobRequestMap,
  JobResultMap,
  Measures,
  Meta,
  PrecomputedName,
  PSweep,
  ScenarioIndex,
  SolveRequest,
  SolveResult,
  SolveResultOk,
  SourcesResponse,
} from './types.ts';

/* ------------------------------------------------------------------------------------------------ taban */

let apiBase = '';

/** Sunucu kökü (varsayılan: aynı köken). Faz 8 / testler için; sonda '/' olmadan, ör. "http://127.0.0.1:8000". */
export function setApiBase(base: string): void {
  apiBase = base.replace(/\/+$/, '');
}
export function apiUrl(path: string): string {
  return path.startsWith('http') ? path : `${apiBase}${path}`;
}

export type ApiErrorKind = 'network' | 'timeout' | 'aborted' | 'http' | 'parse';

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number;
  readonly body: ApiErrorBody | null;
  readonly url: string;
  readonly retryAfterS: number | null;

  constructor(kind: ApiErrorKind, url: string, status = 0, body: ApiErrorBody | null = null, retryAfterS: number | null = null) {
    super(body?.message_tr ?? ApiError.fallbackMessage(kind, status));
    this.name = 'ApiError';
    this.kind = kind;
    this.url = url;
    this.status = status;
    this.body = body;
    this.retryAfterS = retryAfterS;
  }

  /** Kullanıcıya gösterilecek Türkçe ileti. */
  get messageTr(): string {
    return this.message;
  }

  /** 422 doğrulama ayrıntıları (parametre / kilit / hücre hataları). */
  get items(): ApiValidationItem[] {
    return this.body?.errors ?? [];
  }

  /** Sunucuya hiç ulaşılamadı mı (çevrimdışı moda geçmek için)? */
  get isUnreachable(): boolean {
    return this.kind === 'network' || this.kind === 'timeout';
  }

  static fallbackMessage(kind: ApiErrorKind, status: number): string {
    switch (kind) {
      case 'network':
        return 'Sunucuya ulaşılamadı (yerel sunucu kapalı olabilir).';
      case 'timeout':
        return 'Sunucu zamanında yanıt vermedi.';
      case 'aborted':
        return 'İstek iptal edildi.';
      case 'parse':
        return 'Sunucu yanıtı okunamadı.';
      default:
        if (status === 404) return 'Bulunamadı.';
        if (status === 429) return 'Kuyruk dolu; birkaç saniye sonra yeniden denenecek.';
        if (status === 501) return 'Bu özellik henüz kullanılamıyor.';
        if (status >= 500) return 'Sunucu hatası.';
        return `İstek başarısız (HTTP ${status}).`;
    }
  }
}

export interface RequestOptions {
  signal?: AbortSignal;
  /** ms; varsayılan 30 sn (büyük GeoJSON'lar için yeterli). 0 → sınırsız. */
  timeoutMs?: number;
}

/** Ham yanıt: gövde + durum kodu (202/200 ayrımı gibi durumlar için). */
export interface ApiResponse<T> {
  data: T;
  status: number;
  headers: Headers;
}

function combineSignals(signal: AbortSignal | undefined, timeoutMs: number): { signal: AbortSignal | undefined; timedOut: () => boolean; dispose: () => void } {
  if (!timeoutMs) return { signal, timedOut: () => false, dispose: () => undefined };
  const ctrl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    ctrl.abort();
  }, timeoutMs);
  const onAbort = () => ctrl.abort();
  if (signal) {
    if (signal.aborted) ctrl.abort();
    else signal.addEventListener('abort', onAbort, { once: true });
  }
  return {
    signal: ctrl.signal,
    timedOut: () => timedOut,
    dispose: () => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
    },
  };
}

export async function requestJson<T>(method: 'GET' | 'POST', path: string, body?: unknown, opts: RequestOptions = {}): Promise<ApiResponse<T>> {
  const url = apiUrl(path);
  const { signal, timedOut, dispose } = combineSignals(opts.signal, opts.timeoutMs ?? 30_000);
  let res: Response;
  try {
    res = await fetch(url, {
      method,
      signal,
      headers: body === undefined ? { accept: 'application/json' } : { accept: 'application/json', 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-cache',
    });
  } catch (ex) {
    dispose();
    if (timedOut()) throw new ApiError('timeout', url);
    if (opts.signal?.aborted || (ex instanceof DOMException && ex.name === 'AbortError')) throw new ApiError('aborted', url);
    throw new ApiError('network', url);
  }
  let parsed: unknown = null;
  try {
    const text = await res.text();
    parsed = text ? JSON.parse(text) : null;
  } catch {
    dispose();
    if (timedOut()) throw new ApiError('timeout', url);
    if (!res.ok) throw new ApiError('http', url, res.status);
    throw new ApiError('parse', url, res.status);
  }
  dispose();
  if (!res.ok) {
    const ra = Number(res.headers.get('retry-after'));
    throw new ApiError('http', url, res.status, (parsed as ApiErrorBody) ?? null, Number.isFinite(ra) && ra > 0 ? ra : null);
  }
  return { data: parsed as T, status: res.status, headers: res.headers };
}

const get = async <T>(path: string, opts?: RequestOptions) => (await requestJson<T>('GET', path, undefined, opts)).data;

/* ------------------------------------------------------------------------------------------------ üst veri */

/** GET /api/health — sunucu ve çözücü işçisinin durumu. Çevrimdışı tespiti için kısa zaman aşımıyla çağırın. */
export const getHealth = (opts?: RequestOptions) => get<Health>('/api/health', { timeoutMs: 2500, ...opts });

/** GET /api/meta — parametre kaydı, gruplar, senaryolar, ızgaralar, sınıflar. */
export const getMeta = (opts?: RequestOptions) => get<Meta>('/api/meta', opts);

/** GET /api/sources — kaynak listesi ve kaynakça (Kaynakça sekmesi). */
export const getSources = (opts?: RequestOptions) => get<SourcesResponse>('/api/sources', opts);

/* ------------------------------------------------------------------------------------------------ veri dosyaları */

/** GET /api/grid/{km} — hücre GeoJSON'u (özellik sırası = grid_index). */
export const getGrid = (km: GridKm, opts?: RequestOptions) => get<GridCells>(`/api/grid/${km}`, opts);

/** GET /api/boundaries — il ve ilçe sınırları tek yanıtta. */
export const getBoundaries = (opts?: RequestOptions) => get<Boundaries>('/api/boundaries', opts);

/** GET /api/boundaries/{il|ilceler} — tek GeoJSON (MapLibre kaynağına doğrudan URL olarak da verilebilir). */
export const boundaryUrl = (name: 'il' | 'ilceler') => apiUrl(`/api/boundaries/${name}`);
export const getBoundary = (name: 'il' | 'ilceler', opts?: RequestOptions) => get<Boundaries['il'] | Boundaries['ilceler']>(`/api/boundaries/${name}`, opts);

/** GET /api/measures — "Toplam mesafe" kartı ve Ankara ölçüleri. */
export const getMeasures = (opts?: RequestOptions) => get<Measures>('/api/measures', opts);

/** GET /api/bounds/{km} — varsayılan parametrelerle önceden hesaplanmış sınır kartları. */
export const getBoundsCards = (km: GridKm, opts?: RequestOptions) => get<BoundsResult>(`/api/bounds/${km}`, opts);

/* ------------------------------------------------------------------------------------------------ önceden çözülmüş */

/** GET /api/precomputed/{ad} — beyaz listedeki önceden çözülmüş dosyalar. */
export const getPrecomputed = <T = unknown>(name: PrecomputedName, opts?: RequestOptions) => get<T>(`/api/precomputed/${name}`, opts);

/** Varsayılan senaryonun uzun süreli çözümü (precomputed/default_{km}km.json). */
export const getDefaultSolution = (km: GridKm, opts?: RequestOptions) => getPrecomputed<SolveResultOk>(`default_${km}km`, opts);

/** Hazır senaryonun önceden çözülmüş sonucu; 'varsayilan' → default_{km}km. */
export const getScenarioSolution = (scenarioId: string, km: GridKm, opts?: RequestOptions) =>
  scenarioId === 'varsayilan' ? getDefaultSolution(km, opts) : getPrecomputed<SolveResultOk>(`scenario_${scenarioId}_${km}km`, opts);

export const getScenarioIndex = (opts?: RequestOptions) => getPrecomputed<ScenarioIndex>('scenarios', opts);

/** U eğrisi (p taraması) — yalnız 3 ve 5 km. */
export const getPSweep = (km: 3 | 5, opts?: RequestOptions) => getPrecomputed<PSweep>(`p_sweep_${km}km`, opts);

/* ------------------------------------------------------------------------------------------------ işler */

/**
 * İş gönderimi: 202 (kuyruğa alındı) ya da 200 (önbellekten; `result` dolu). Doğrulama hatası 422 → ApiError
 * (`items`), kuyruk dolu 429 → ApiError (`retryAfterS`), iş türü kapalı 501.
 * Çoğu durumda doğrudan değil, `jobs.ts` içindeki `runJob` / `solve` / `evaluate` / `bounds` kullanılmalı.
 */
export async function submitJob<T extends KnownJobType>(type: T, body: JobRequestMap[T], opts?: RequestOptions): Promise<JobSnapshot<JobResultMap[T]>> {
  const path = type === 'solve' || type === 'evaluate' || type === 'bounds' || type === 'explain' ? `/api/${type}` : `/api/jobs/${type}`;
  return (await requestJson<JobSnapshot<JobResultMap[T]>>('POST', path, body, opts)).data;
}

export const submitSolve = (body: SolveRequest, opts?: RequestOptions) => submitJob('solve', body, opts) as Promise<JobSnapshot<SolveResult>>;
export const submitEvaluate = (body: EvaluateRequest, opts?: RequestOptions) => submitJob('evaluate', body, opts) as Promise<JobSnapshot<EvaluateResult>>;
export const submitBounds = (body: BoundsRequest, opts?: RequestOptions) => submitJob('bounds', body, opts) as Promise<JobSnapshot<BoundsResult>>;
/** Faz 6: şimdilik 501 döner (meta.job_types.explain.available = false). */
export const submitExplain = (body: ExplainRequest, opts?: RequestOptions) => submitJob('explain', body, opts) as Promise<JobSnapshot<ExplainResult>>;

/** POST /api/jobs/{tür} — kayıtlı her iş türü (Faz 6: sweep, pareto, sensitivity …). */
export async function submitGenericJob<R = unknown>(type: string, payload: Record<string, unknown>, opts?: RequestOptions): Promise<JobSnapshot<R>> {
  return (await requestJson<JobSnapshot<R>>('POST', `/api/jobs/${encodeURIComponent(type)}`, payload, opts)).data;
}

/** GET /api/jobs — son işlerin özeti. */
export const listJobs = (clientId?: string, limit = 50, opts?: RequestOptions) =>
  get<{ jobs: JobSnapshot[] }>(`/api/jobs?limit=${limit}${clientId ? `&client_id=${encodeURIComponent(clientId)}` : ''}`, opts);

/** GET /api/jobs/{id} — işin durumu ve (bittiyse) sonucu. */
export const getJob = <R = unknown>(jobId: string, includeResult = true, opts?: RequestOptions) =>
  get<JobSnapshot<R>>(`/api/jobs/${encodeURIComponent(jobId)}${includeResult ? '' : '?result=false'}`, opts);

/** POST /api/jobs/{id}/cancel — kuyruktaki iş hemen, çalışan iş motor durunca `cancelled` olur. */
export const cancelJob = async (jobId: string, opts?: RequestOptions) =>
  (await requestJson<JobSnapshot>('POST', `/api/jobs/${encodeURIComponent(jobId)}/cancel`, undefined, opts)).data;

/** SSE adresi (GET /api/jobs/{id}/events). */
export const jobEventsUrl = (jobId: string) => apiUrl(`/api/jobs/${encodeURIComponent(jobId)}/events`);

/* ------------------------------------------------------------------------------------------------ altlık harita */

/** Altlık harita dosyalarının mutlak adresleri (MapLibre'ye mutlak URL gerekir). */
export function basemapUrls(origin: string = window.location.origin) {
  const root = apiBase || origin;
  return {
    pmtiles: `pmtiles://${root}/basemap/ankara.pmtiles`,
    glyphs: `${root}/basemap/fonts/{fontstack}/{range}.pbf`,
    sprite: (flavor: 'light' | 'dark') => `${root}/basemap/sprites/v4/${flavor}`,
  };
}
