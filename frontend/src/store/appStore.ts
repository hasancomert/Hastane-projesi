/**
 * Uygulama durumu (zustand). Tek depo, dilimlere ayrılmış alanlar; bileşenler seçicilerle abone olur:
 *   const result = useApp((s) => s.result);
 *
 * Veri akışı (docs/FRONTEND.md):
 *   bootstrap() → bağlantı + /api/meta + sınırlar + ızgara → ilk çözüm (önbellekten anında)
 *   kullanıcı değişikliği (setParam / applyScenario / setGridKm / setLock) → `problem` değişir
 *     → autoSolve 600 ms bekler → solveNow() → POST /api/solve (replace=true, kararlı client_id) → SSE ilerleme
 *     → `job.trace` birikir → `result` (yalnız beklenen işin sonucu) → harita ve paneller yeniden çizilir.
 *
 * Bu dosya yalnız DURUM ve SAF eylemleri içerir; ağ işleri store/solver.ts ve store/bootstrap.ts'tedir.
 */
import { create } from 'zustand';

import type {
  ApiValidationItem,
  Boundaries,
  EvaluateResult,
  GridCells,
  GridKm,
  Health,
  Locks,
  Meta,
  ObjectiveMode,
  Overrides,
  ParamValue,
  SolveResult,
  TraceEvent,
} from '../api/types.ts';
import type { ConnectionMode } from '../api/offline.ts';
import type { Scheme } from '../theme/palette.ts';
import { readPref, writePref } from './prefs.ts';

/* ------------------------------------------------------------------------------------------------ tipler */

/** Harita katmanları (şartname 6.2). Görünürlük `layers[id]`. */
export type LayerId =
  | 'boundaries'
  | 'population'
  | 'population3d'
  | 'candidates'
  | 'hospitals'
  | 'serviceAreas'
  | 'flows'
  | 'access'
  | 'rings'
  | 'shadowPrices'
  | 'measures';

export interface LayerDef {
  id: LayerId;
  label: string;
  description: string;
  defaultVisible: boolean;
  /** Faz 6'da dolacak katmanlar (gölge fiyat) */
  phase: 5 | 6;
}

export const LAYER_DEFS: readonly LayerDef[] = [
  { id: 'boundaries', label: 'İl ve ilçe sınırları', description: 'İl sınırı, 25 ilçe ve adları', defaultVisible: true, phase: 5 },
  { id: 'population', label: 'Nüfus yoğunluğu', description: 'Hücre nüfusu, log renk ölçeği', defaultVisible: false, phase: 5 },
  { id: 'population3d', label: '3B nüfus sütunları', description: 'Hücre nüfusu sütun yüksekliği olarak', defaultVisible: false, phase: 5 },
  { id: 'candidates', label: 'Aday hücreler', description: 'Hastane açılabilecek hücreler; kilitli / yasak hücreler', defaultVisible: false, phase: 5 },
  { id: 'hospitals', label: 'Hastaneler', description: 'Daire boyutu = yatak, renk = sınıf', defaultVisible: true, phase: 5 },
  { id: 'serviceAreas', label: 'Hizmet bölgeleri', description: 'Her hücre atandığı hastanenin renginde', defaultVisible: true, phase: 5 },
  { id: 'flows', label: 'Hasta akışları', description: 'Hücreden hastaneye yaylar', defaultVisible: false, phase: 5 },
  { id: 'access', label: 'Erişim ısı haritası', description: 'En yakın hastaneye yol mesafesi; R dışı vurgulu', defaultVisible: false, phase: 5 },
  { id: 'rings', label: 'R yarıçap halkaları', description: 'Hastanelerin çevresinde kuş uçuşu R karşılığı', defaultVisible: false, phase: 5 },
  { id: 'shadowPrices', label: 'Gölge fiyatlar', description: 'Hücre başına marjinal hizmet maliyeti (dual)', defaultVisible: false, phase: 6 },
  { id: 'measures', label: 'Ankara ölçüleri', description: 'En uzak iki sınır noktası, Kızılay → en uzak nokta, uç noktalar (info/MeasuresSection)', defaultVisible: false, phase: 6 },
];

export type Selection =
  | { kind: 'hospital'; index: number; cellId: string }
  | { kind: 'cell'; gridIndex: number; cellId: string }
  | null;

export type ChartTabId = 'u_egrisi' | 'pareto' | 'tornado' | 'dal_sinir' | 'maliyet';
export type InfoTabId = 'hoca' | 'veri' | 'model' | 'sinirliliklar' | 'kaynakca';

/** Sonucun geldiği yer (rozet + "neden bu sonuç" açıklaması için). */
export type ResultSource = 'live' | 'cache' | 'precomputed' | 'fallback';

export interface ResultInfo {
  kind: 'solve' | 'evaluate';
  source: ResultSource;
  jobId: string | null;
  /** sonucun ait olduğu problem anahtarı (problemKey); güncel anahtardan farklıysa sonuç "eski" */
  problemKey: string;
  receivedAt: number;
  /** iptal edildi / yerini yeni isteğe bıraktı: en iyi bulunan çözüm, "optimum" denmemeli */
  stopped: boolean;
  stopReason: string | null;
  /** bu sonucu üreten canlı işin SSE ilerleme izi (önbellekten gelende boş; o zaman result.trace kullanılır) */
  liveTrace: TraceEvent[];
}

export type JobPhase = 'debounce' | 'submitting' | 'queued' | 'running' | 'cancelling';

export interface JobInfo {
  jobId: string | null;
  kind: 'solve' | 'evaluate';
  phase: JobPhase;
  problemKey: string;
  /** istemci saatine göre başlangıç (ms) */
  startedAt: number;
  position: number | null;
  /** son ilerleme olayı (aşama, lb, ub, gap, t) */
  progress: TraceEvent | null;
  /** tüm ilerleme olayları (dal-sınır yakınsaması grafiği) */
  trace: TraceEvent[];
  /** sunucu tarafı çalışma süresi (heartbeat run_s) */
  runS: number | null;
  reconnects: number;
}

export interface AppError {
  message: string;
  items?: ApiValidationItem[];
  at: number;
}

export interface ConnectionInfo {
  mode: ConnectionMode;
  health: Health | null;
  /** çevrimdışı moddaysa açıklama ("çevrimdışı mod — canlı çözüm kapalı") */
  note: string | null;
  /** çevrimdışı modda sonucu veren yedek sağlayıcı */
  fallbackSource: string | null;
}

export interface ProblemState {
  gridKm: GridKm;
  /** seçili hazır senaryo (meta.scenarios[].id) */
  scenarioId: string;
  /**
   * Sunucuya gönderilen parametre değişiklikleri = senaryonun overrides'ı + kullanıcının değişiklikleri.
   * 'grid.cell_km' burada TUTULMAZ (gridKm alanı); varsayılana eşit değerler silinir.
   */
  overrides: Overrides;
  locks: Locks;
  /** kaydırıcı aralığı dışındaki değerlere izin (sunucu uyarıyla kabul eder) */
  allowOutsideRange: boolean;
}

export interface UiState {
  chartTab: ChartTabId;
  infoTab: InfoTabId;
  /** dar ekranda çekmeceler */
  paramsDrawerOpen: boolean;
  resultDrawerOpen: boolean;
  /** parametre akordeonunda açık gruplar (meta.groups[].id) */
  openGroups: string[];
  autoSolve: boolean;
  /** Faz 7 */
  presentationMode: boolean;
  manualMode: boolean;
  /** Faz 6: sunum turu adımı (null = kapalı) */
  tourStep: number | null;
}

export interface AppState {
  connection: ConnectionInfo;
  meta: Meta | null;
  metaError: string | null;
  boundaries: Boundaries | null;
  /** ızgara GeoJSON'ları (bir kez yüklenir) */
  grids: Partial<Record<GridKm, GridCells>>;
  gridLoading: Partial<Record<GridKm, boolean>>;

  problem: ProblemState;

  job: JobInfo | null;
  /** gösterilen çözüm (solve) — `result.ok` false ise olursuz/hatalı (message_tr) */
  result: SolveResult | null;
  resultInfo: ResultInfo | null;
  /** Faz 7 manuel mod değerlendirmesi */
  evaluation: EvaluateResult | null;
  error: AppError | null;

  selection: Selection;
  hoveredHospital: number | null;
  layers: Record<LayerId, boolean>;
  colorScheme: Scheme;
  ui: UiState;

  /* ---- eylemler (saf durum değişiklikleri) ---- */
  setConnection(patch: Partial<ConnectionInfo>): void;
  setMeta(meta: Meta): void;
  setMetaError(message: string | null): void;
  setBoundaries(b: Boundaries): void;
  setGrid(km: GridKm, cells: GridCells): void;
  setGridLoading(km: GridKm, loading: boolean): void;

  /** Parametre değiştir (kayıt anahtarı). 'grid.cell_km' → setGridKm. Varsayılana eşitse override silinir. */
  setParam(key: string, value: ParamValue): void;
  /** Parametreyi senaryonun (yoksa kaydın) değerine döndür. */
  resetParam(key: string): void;
  /** Tüm kullanıcı değişikliklerini sil (seçili senaryonun değerleri kalır). */
  resetAllParams(): void;
  /** Hazır senaryoyu uygula (overrides = senaryonun overrides'ı). */
  applyScenario(id: string): void;
  setGridKm(km: GridKm): void;
  setMode(mode: ObjectiveMode): void;
  /** Hücre kilidi: 'open' zorunlu, 'closed' yasak, null kaldır. */
  setLock(cellId: string, lock: 'open' | 'closed' | null): void;
  clearLocks(): void;
  setAllowOutsideRange(v: boolean): void;

  setJob(job: JobInfo | null): void;
  patchJob(jobId: string | null, patch: Partial<JobInfo>): void;
  /** ilerleme olayı ekle (yalnız beklenen iş için) */
  pushProgress(jobId: string, ev: TraceEvent): void;
  setResult(result: SolveResult, info: ResultInfo): void;
  setEvaluation(ev: EvaluateResult | null): void;
  setError(err: AppError | null): void;

  select(sel: Selection): void;
  hoverHospital(index: number | null): void;
  setLayerVisible(id: LayerId, visible: boolean): void;
  toggleLayer(id: LayerId): void;
  setColorScheme(s: Scheme): void;
  toggleColorScheme(): void;
  setUi(patch: Partial<UiState>): void;
}

/* ------------------------------------------------------------------------------------------------ yardımcılar */

const NO_LOCKS: Locks = { open: [], closed: [] };

function sameValue(a: unknown, b: unknown): boolean {
  if (typeof a === 'number' && typeof b === 'number') return Math.abs(a - b) <= 1e-12 * Math.max(1, Math.abs(a), Math.abs(b));
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Bir anahtarın "taban" değeri: seçili senaryonun override'ı, yoksa kayıttaki varsayılan. */
export function baseValue(meta: Meta | null, scenarioId: string, key: string): ParamValue | undefined {
  const sc = meta?.scenarios.find((s) => s.id === scenarioId);
  if (sc && key in sc.overrides) return sc.overrides[key];
  return meta?.parameters[key]?.value;
}

/** Parametrenin geçerli değeri (override → senaryo → kayıt). 'grid.cell_km' için gridKm. */
export function paramValue(state: Pick<AppState, 'meta' | 'problem'>, key: string): ParamValue | undefined {
  if (key === 'grid.cell_km') return state.problem.gridKm;
  if (key in state.problem.overrides) return state.problem.overrides[key];
  return state.meta?.parameters[key]?.value;
}

/** Kullanıcının seçili senaryoya göre değiştirdiği anahtarlar. */
export function changedKeys(state: Pick<AppState, 'meta' | 'problem'>): string[] {
  const sc = state.meta?.scenarios.find((s) => s.id === state.problem.scenarioId);
  const base = sc?.overrides ?? {};
  const keys = new Set([...Object.keys(base), ...Object.keys(state.problem.overrides)]);
  return [...keys].filter((k) => !sameValue(base[k] ?? state.meta?.parameters[k]?.value, state.problem.overrides[k] ?? state.meta?.parameters[k]?.value));
}

/** Kararlı problem anahtarı: ızgara + sıralı overrides + sıralı kilitler (önbellek/eskilik karşılaştırması için). */
export function problemKey(p: ProblemState): string {
  const ov = Object.keys(p.overrides)
    .sort()
    .map((k) => [k, p.overrides[k]]);
  return JSON.stringify([p.gridKm, ov, [...p.locks.open].sort(), [...p.locks.closed].sort(), p.allowOutsideRange]);
}

function initialScheme(): Scheme {
  const saved = readPref('theme');
  if (saved === 'light' || saved === 'dark') return saved;
  try {
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

const defaultLayers = Object.fromEntries(LAYER_DEFS.map((l) => [l.id, l.defaultVisible])) as Record<LayerId, boolean>;

/* ------------------------------------------------------------------------------------------------ depo */

export const useApp = create<AppState>()((set, get) => ({
  connection: { mode: 'connecting', health: null, note: null, fallbackSource: null },
  meta: null,
  metaError: null,
  boundaries: null,
  grids: {},
  gridLoading: {},

  problem: { gridKm: 3, scenarioId: 'varsayilan', overrides: {}, locks: NO_LOCKS, allowOutsideRange: false },

  job: null,
  result: null,
  resultInfo: null,
  evaluation: null,
  error: null,

  selection: null,
  hoveredHospital: null,
  layers: { ...defaultLayers, ...(readPref<Partial<Record<LayerId, boolean>>>('layers') ?? {}) },
  colorScheme: initialScheme(),
  ui: {
    chartTab: 'dal_sinir',
    infoTab: 'hoca',
    paramsDrawerOpen: false,
    resultDrawerOpen: false,
    openGroups: ['A'],
    autoSolve: true,
    presentationMode: false,
    manualMode: false,
    tourStep: null,
  },

  setConnection: (patch) => set((s) => ({ connection: { ...s.connection, ...patch } })),
  setMeta: (meta) =>
    set((s) => ({
      meta,
      metaError: null,
      // ilk yüklemede kayıttaki varsayılan ızgara
      problem: s.meta ? s.problem : { ...s.problem, gridKm: meta.defaults.grid_km ?? s.problem.gridKm },
    })),
  setMetaError: (message) => set({ metaError: message }),
  setBoundaries: (boundaries) => set({ boundaries }),
  setGrid: (km, cells) => set((s) => ({ grids: { ...s.grids, [km]: cells }, gridLoading: { ...s.gridLoading, [km]: false } })),
  setGridLoading: (km, loading) => set((s) => ({ gridLoading: { ...s.gridLoading, [km]: loading } })),

  setParam: (key, value) => {
    if (key === 'grid.cell_km') {
      if (value === 2 || value === 3 || value === 5) get().setGridKm(value);
      return;
    }
    set((s) => {
      const overrides = { ...s.problem.overrides };
      const base = s.meta?.parameters[key]?.value;
      const sc = s.meta?.scenarios.find((x) => x.id === s.problem.scenarioId);
      const inScenario = !!sc && key in sc.overrides;
      if (!inScenario && base !== undefined && sameValue(base, value)) delete overrides[key];
      else overrides[key] = value;
      return { problem: { ...s.problem, overrides } };
    });
  },
  resetParam: (key) => {
    if (key === 'grid.cell_km') {
      get().setGridKm(get().meta?.defaults.grid_km ?? 3);
      return;
    }
    set((s) => {
      const overrides = { ...s.problem.overrides };
      const sc = s.meta?.scenarios.find((x) => x.id === s.problem.scenarioId);
      if (sc && key in sc.overrides) overrides[key] = sc.overrides[key];
      else delete overrides[key];
      return { problem: { ...s.problem, overrides } };
    });
  },
  resetAllParams: () =>
    set((s) => {
      const sc = s.meta?.scenarios.find((x) => x.id === s.problem.scenarioId);
      return { problem: { ...s.problem, overrides: { ...(sc?.overrides ?? {}) } } };
    }),
  applyScenario: (id) =>
    set((s) => {
      const sc = s.meta?.scenarios.find((x) => x.id === id);
      if (!sc) return {};
      const overrides = { ...sc.overrides };
      delete overrides['grid.cell_km'];
      return { problem: { ...s.problem, scenarioId: id, overrides }, selection: null };
    }),
  setGridKm: (km) =>
    set((s) =>
      s.problem.gridKm === km
        ? {}
        : // kilitler hücre kimliğine bağlıdır ('3km_…'); ızgara değişince geçersizdir
          { problem: { ...s.problem, gridKm: km, locks: NO_LOCKS }, selection: null, hoveredHospital: null },
    ),
  setMode: (mode) => get().setParam('solver.objective_mode', mode),
  setLock: (cellId, lock) =>
    set((s) => {
      const open = s.problem.locks.open.filter((c) => c !== cellId);
      const closed = s.problem.locks.closed.filter((c) => c !== cellId);
      if (lock === 'open') open.push(cellId);
      if (lock === 'closed') closed.push(cellId);
      return { problem: { ...s.problem, locks: { open, closed } } };
    }),
  clearLocks: () => set((s) => ({ problem: { ...s.problem, locks: NO_LOCKS } })),
  setAllowOutsideRange: (v) => set((s) => ({ problem: { ...s.problem, allowOutsideRange: v } })),

  setJob: (job) => set({ job }),
  patchJob: (jobId, patch) =>
    set((s) => (s.job && s.job.jobId === jobId ? { job: { ...s.job, ...patch } } : {})),
  pushProgress: (jobId, ev) =>
    set((s) => (s.job && s.job.jobId === jobId ? { job: { ...s.job, progress: ev, trace: [...s.job.trace, ev] } } : {})),
  setResult: (result, info) =>
    set((s) => {
      // seçili hastane yeni çözümde yoksa seçimi kaldır
      let selection = s.selection;
      if (selection?.kind === 'hospital') {
        const hs = result.ok ? result.hospitals : [];
        const idx = hs.findIndex((h) => h.cell_id === selection!.cellId);
        selection = idx >= 0 ? { kind: 'hospital', index: idx, cellId: selection.cellId } : null;
      }
      return { result, resultInfo: info, selection, hoveredHospital: null, error: null };
    }),
  setEvaluation: (evaluation) => set({ evaluation }),
  setError: (error) => set({ error }),

  select: (selection) => set({ selection }),
  hoverHospital: (index) => set((s) => (s.hoveredHospital === index ? {} : { hoveredHospital: index })),
  setLayerVisible: (id, visible) =>
    set((s) => {
      const layers = { ...s.layers, [id]: visible };
      writePref('layers', layers);
      return { layers };
    }),
  toggleLayer: (id) => get().setLayerVisible(id, !get().layers[id]),
  setColorScheme: (colorScheme) => {
    writePref('theme', colorScheme);
    try {
      document.documentElement.setAttribute('data-theme', colorScheme);
    } catch {
      /* belge yok (test) */
    }
    set({ colorScheme });
  },
  toggleColorScheme: () => get().setColorScheme(get().colorScheme === 'dark' ? 'light' : 'dark'),
  setUi: (patch) => set((s) => ({ ui: { ...s.ui, ...patch } })),
}));

/* ------------------------------------------------------------------------------------------------ seçiciler */

/** Gösterilen başarılı çözüm (olursuz/hatalıysa null). */
export const selectOkResult = (s: AppState) => (s.result && s.result.ok ? s.result : null);
/** Gösterilen sonuç güncel problemden mi (değilse arayüz "eski sonuç" diye soluklaştırır)? */
export const selectResultIsStale = (s: AppState) => !!s.resultInfo && s.resultInfo.problemKey !== problemKey(s.problem);
/**
 * Gösterilen sonuç "önceki sonuç" diye işaretlenmeli mi (başlık soluk + "(önceki sonuç)", optimallik rozeti gizli)?
 * Çevrimdışı yedek sonuç bootstrap'ta problemle eşitlendiği için eski sayılmaz.
 */
export const selectShowStale = (s: AppState) => selectResultIsStale(s) && s.resultInfo?.source !== 'fallback';
/** Arayüzün hedef farkı (istekteki solver.mip_gap; yoksa meta.defaults.gap, o da yoksa %1) — optimallik etiketi. */
export const selectUiTargetGap = (s: AppState): number => {
  const ov = s.problem.overrides['solver.mip_gap'];
  return typeof ov === 'number' ? ov : s.meta?.defaults.gap ?? 0.01;
};
/** Sonucun ait olduğu problem varsayılan parametreler mi (ızgara dışında değişiklik ve kilit yok)? */
export function isDefaultProblemKey(key: string | null | undefined): boolean {
  if (!key) return false;
  try {
    const [, ov, open, closed] = JSON.parse(key) as [number, unknown[], unknown[], unknown[]];
    return ov.length === 0 && open.length === 0 && closed.length === 0;
  } catch {
    return false;
  }
}
/** Canlı çözüm şu an mümkün mü? */
export const selectCanSolve = (s: AppState) => s.connection.mode === 'online' && !!s.meta;
/** Geçerli ızgara GeoJSON'u (yüklenmediyse null). */
export const selectGrid = (s: AppState) => s.grids[s.problem.gridKm] ?? null;
/** Seçili hastane nesnesi. */
export const selectSelectedHospital = (s: AppState) => {
  const r = selectOkResult(s);
  return s.selection?.kind === 'hospital' && r ? r.hospitals[s.selection.index] ?? null : null;
};
