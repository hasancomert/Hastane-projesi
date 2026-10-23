/**
 * Sunucu sözleşmesinin TypeScript karşılığı (docs/API.md, Faz 4). Alan adları JSON'daki gibidir (snake_case).
 * Para TL (2026 fiyatları), mesafeler yol km'si (aksi yazmadıkça). Sunucunun ayrıntılı tanı alanları
 * (lagrange, core_mip, improve …) gevşek tiplidir; arayüzün kullandığı her alan burada açıkça tiplidir.
 */

/* ------------------------------------------------------------------------------------------------ ortak */

export type GridKm = 2 | 3 | 5;
export type ScenarioYear = 2025 | 2030;
export type ObjectiveMode = 'toplam' | 'butce' | 'sabit_p';
export type ClassCode = 'IV-C' | 'V-B' | 'V-C' | 'V-D';
export const CLASS_CODES: readonly ClassCode[] = ['IV-C', 'V-B', 'V-C', 'V-D'] as const;

/** Parametre durumu (kayıt): kaynaklı · hesap · varsayım · model seçimi. */
export type ParamStatus = 'kaynakli' | 'hesap' | 'varsayim' | 'model_secimi';

/** Kayıttaki değer türleri: sayı, seçenek (metin ya da sayı), evet/hayır, dolambaç profili [[km, katsayı], …]. */
export type ParamValue = number | string | boolean | number[][];
export type Overrides = Record<string, ParamValue>;

export interface Locks {
  /** 'aç' kilidi: bu hücrelerde hastane zorunlu. */
  open: string[];
  /** 'yasak' kilidi: bu hücrelerde hastane açılamaz. */
  closed: string[];
}

/** Motorun çözüm durumları (backend/model/solve.py). */
export type SolveStatus =
  | 'optimal'
  | 'sure_doldu'
  | 'gap_ulasilamadi'
  | 'durduruldu'
  | 'sezgisel'
  | 'olurlu_degil'
  | 'cozum_yok'
  | 'hata';

/** Manuel değerlendirmenin durumları. */
export type EvaluateStatus = 'degerlendirildi' | 'olurlu_degil' | 'hata';

export type JobType = 'solve' | 'evaluate' | 'bounds' | 'explain' | (string & {});
export type JobState = 'queued' | 'running' | 'done' | 'failed' | 'cancelled';

/* ------------------------------------------------------------------------------------------------ hatalar */

/** 4xx/5xx gövdesi: her zaman Türkçe `message_tr`; 422'de `errors`. */
export interface ApiErrorBody {
  message_tr: string;
  errors?: ApiValidationItem[];
}

export type ApiValidationType =
  | 'bilinmeyen_parametre'
  | 'aralik_disi'
  | 'arayuz_araligi_disi'
  | 'tur_hatasi'
  | 'secenek_disi'
  | 'tutarsiz_parametre'
  | 'celiski'
  | 'bilinmeyen_hucre'
  | 'kilit_celiskisi'
  | 'tekrar'
  | 'sinif_kapali'
  | (string & {});

export interface ApiValidationItem {
  loc: (string | number)[];
  key?: string | null;
  field?: string;
  type: ApiValidationType;
  message_tr: string;
  value?: unknown;
  input?: unknown;
  allowed?: ParamAllowed | Record<string, unknown>;
  ui_range?: [number, number];
  suggestions?: string[];
}

/* ------------------------------------------------------------------------------------------------ /api/health */

export interface Health {
  ok: boolean;
  status_tr: string;
  api_version: string;
  engine_version: string;
  time: number;
  uptime_s: number;
  solver_ready: boolean;
  seeded: Record<string, number>;
  worker: {
    pid: number;
    alive: boolean;
    ready: boolean;
    incarnation: number;
    current_job: string | null;
    uptime_s: number;
    ready_after_s: number | null;
    last_heartbeat_age_s: number | null;
  } | null;
  restarts: number;
  restart_log: unknown[];
  queue: { queued: number; max_queued: number; max_queued_per_client: number };
  jobs: Partial<Record<JobState, number>>;
  job_types: string[];
  cache: Record<string, unknown>;
}

/* ------------------------------------------------------------------------------------------------ /api/meta */

export interface SourceRef {
  id: string;
  title: string;
  publisher: string;
  url: string;
  accessed: string;
  /** resmi · veri · akademik · ikincil · haber · hesap */
  kind: string;
}

export type ParamAllowed =
  | { kind: 'number' | 'integer'; min: number | null; max: number | null; min_inclusive: boolean; max_inclusive: boolean }
  | { kind: 'choice'; options: (string | number)[] }
  | { kind: 'bool'; options: boolean[] }
  | { kind: 'profile'; format: string };

export interface ParamAlternative {
  label: string;
  value: ParamValue;
  status?: ParamStatus;
  source_ids?: string[];
}

export interface ParamMeta {
  key: string;
  label: string;
  value: ParamValue;
  unit: string;
  status: ParamStatus;
  status_tr: string;
  sources: SourceRef[];
  derivation: string | null;
  rationale: string | null;
  notes: string | null;
  /** Kaydırıcı aralığı (arayüz tercihi); null → kaydırıcı yok (senaryo değeri ya da seçenek). */
  range: [number, number] | null;
  range_note: string | null;
  alternatives: ParamAlternative[];
  /** Kayıttaki grup etiketi (ör. "G. Erişim"); panel gruplaması için `Meta.groups` kullanılmalı. */
  group: string;
  domain: string;
  /** Modelin tanım kümesi (sunucu bunun dışını her zaman reddeder). */
  allowed: ParamAllowed;
  /** true → değişince yapı yeniden kurulur (daha yavaş çözüm). */
  structure: boolean;
}

export interface ParamGroup {
  /** "A" … "I" (şartname 4.4). */
  id: string;
  title: string;
  keys: string[];
}

export interface ClassInfo {
  code: ClassCode;
  name: string;
  L: number;
  U: number;
  enabled: boolean;
}

export interface CostParts {
  yapi: number;
  altyapi: number;
  donanim: number;
  kdv: number;
  isletme: number;
  sabit?: number;
}

export interface DerivedClass extends ClassInfo {
  disabled_reason: string;
  fixed_tl: number;
  var_tl_per_bed: number;
  fixed_parts: CostParts;
  var_parts: CostParts;
}

export interface PrecomputedRef {
  name: string;
  url: string;
  status: SolveStatus;
  ub_tl: number;
  lb_tl: number;
  gap: number;
  n_hospitals: number;
  time_limit_s: number;
}

export interface LiveCheck {
  works_live: boolean;
  status: SolveStatus;
  gap: number;
  t_to_1pct_s: number | null;
  time_limit_s: number;
  wall_s: number;
}

export interface ScenarioMeta {
  id: string;
  title: string;
  status: ParamStatus;
  status_tr: string;
  overrides: Overrides;
  description_tr: string;
  rationale: string | null;
  derivation: string | null;
  source_keys: string[];
  mode: ObjectiveMode;
  warnings: string[];
  grids: Record<string, { precomputed: PrecomputedRef | null; live_check: LiveCheck | null }>;
  precomputed_grids: GridKm[];
  live_grids: GridKm[];
}

export interface GridMeta {
  km: GridKm;
  cells_url: string;
  bounds_url: string;
  n_cells: number;
  n_demand_cells: Record<string, number>;
  recommended_time_limit_s: number;
  precomputed: Record<string, string>;
  precomputed_scenarios: Record<string, string>;
}

export interface JobTypeMeta {
  description_tr: string;
  url: string;
  available: boolean;
  unavailable_tr: string | null;
}

export interface Meta {
  app: { title: string; api_version: string; engine_version: string };
  defaults: {
    grid_km: GridKm;
    mode: ObjectiveMode;
    scenario_year: ScenarioYear;
    time_limit_s: number;
    gap: number;
    recommended_time_limit_s: Record<string, number>;
    recommended_time_limit_note_tr?: string;
  };
  access_rings: { R_km: number; R_crowfly_km: number; note_tr: string };
  grids: GridMeta[];
  /** Kayıt anahtarı → parametre (69 anahtar). */
  parameters: Record<string, ParamMeta>;
  groups: ParamGroup[];
  derived: {
    pv_factor: number;
    lambda_tl_per_person_km: number;
    generalized_cost_tl_per_km: number;
    beds_per_1000: number;
    construction_multiplier: number;
    classes: DerivedClass[];
  };
  classes: ClassInfo[];
  modes: { id: ObjectiveMode; title: string }[];
  scenario_years: ScenarioYear[];
  scenarios: ScenarioMeta[];
  scenario_live_check: Record<string, unknown>;
  status_labels: Record<ParamStatus, string>;
  job_types: Record<string, JobTypeMeta>;
  files: {
    boundaries: string;
    measures: string;
    sources: string;
    basemap: string;
    glyphs: string;
    sprites: string;
  };
}

/* ------------------------------------------------------------------------------------------------ /api/sources */

export interface SourceEntry extends SourceRef {
  published?: string;
  kind_tr?: string;
  used_for?: string[];
  notes?: string | null;
  parameters?: string[];
}

export interface LiteratureEntry {
  id: string;
  authors: string[];
  year: string;
  title: string;
  journal: string;
  publisher: string;
  volume: string;
  issue: string;
  pages: string;
  doi: string;
  url: string;
  verified_via: string;
  notes: string;
}

export interface SourcesResponse {
  generated_from: string[];
  n_sources: number;
  domains: { id: string; title: string; file: string; sources: SourceEntry[] }[];
  literature: LiteratureEntry[];
}

/* ------------------------------------------------------------------------------------------------ GeoJSON */

export type Position = [number, number];
export interface PolygonGeometry { type: 'Polygon'; coordinates: Position[][] }
export interface MultiPolygonGeometry { type: 'MultiPolygon'; coordinates: Position[][][] }
export interface Feature<G, P> { type: 'Feature'; geometry: G; properties: P; id?: string | number }
export interface FeatureCollection<G, P> { type: 'FeatureCollection'; name?: string; features: Feature<G, P>[] }

/** GET /api/grid/{km} hücre özellikleri. Özellik sırası = `grid_index` (layers.access sırası). */
export interface GridCellProps {
  id: string;
  /** WorldPop 2025, TÜİK'e kalibre (kişi). Senaryo nüfusu için `result.layers.demand.population` kullanın. */
  pop: number;
  pop_ghs: number;
  pop2030: number;
  /** kişi / km² */
  dens: number;
  /** su payı (%) */
  water: number;
  /** yapılaşmış alan payı (%) */
  built: number;
  ilce: string;
  /** varsayılan parametrelerle aday mı (parametreye bağlı aday kümesi için layers.candidates). */
  aday: boolean;
}
export type GridCells = FeatureCollection<PolygonGeometry, GridCellProps>;

export interface IlProps { il: string; il_key: string; iso: string; area_km2: number; source: string }
export interface IlceProps { ilce: string; ilce_key: string; area_km2: number; source: string }
export interface Boundaries {
  il: FeatureCollection<PolygonGeometry | MultiPolygonGeometry, IlProps>;
  ilceler: FeatureCollection<PolygonGeometry | MultiPolygonGeometry, IlceProps>;
}

/* ------------------------------------------------------------------------------------------------ /api/measures */

export interface MeasureValue { value: number; unit: string; method: string; text: string }

/** data/processed/ankara_olculer.json ("Toplam mesafe" kartı). Faz 6'nın Hocanın Listesi/Veri sekmesi kullanır. */
export interface Measures {
  meta: Record<string, unknown>;
  area: { geodesic_km2: MeasureValue; note: string; [k: string]: unknown };
  perimeter: { geodesic_km: MeasureValue; note: string; [k: string]: unknown };
  extent: {
    west_east_geodesic_km: MeasureValue;
    north_south_geodesic_km: MeasureValue;
    [k: string]: unknown;
  };
  diameter: { distance_km: MeasureValue; p1: { lon: number; lat: number }; p2: { lon: number; lat: number }; [k: string]: unknown };
  kizilay: {
    point: { lon: number; lat: number; name: string };
    farthest: { lon: number; lat: number; distance_km: MeasureValue };
    [k: string]: unknown;
  };
  centroid: Record<string, unknown>;
  water: Record<string, unknown>;
  area_comparison: {
    reference_km2: number;
    rows: { source: string; value_km2: number; scope: string; status: ParamStatus; diff_km2: number; diff_pct: number; text: string; [k: string]: unknown }[];
    own_rows: Record<string, unknown>[];
    note: string;
    method: string;
  };
  districts: { rows: { ilce: string; ilce_key: string; area_geodesic_km2: number; pop_2025: number; density_per_km2: number; [k: string]: unknown }[]; [k: string]: unknown };
  hospital_land: { conclusion: string; [k: string]: unknown };
  card: {
    title: string;
    items: { key: string; label: string; value_text: string; tooltip: string }[];
    hospital_land_conclusion: string;
  };
}

/* ------------------------------------------------------------------------------------------------ sınır kartları */

export interface BoundStep {
  status: string;
  status_tr?: string;
  value: number | null;
  lower_bound: number | null;
  proven: boolean;
  time_s?: number;
  k_nearest?: number;
  [k: string]: unknown;
}

/** GET /api/bounds/{km} (önceden hesaplı) ve POST /api/bounds sonucu (backend.model.bounds.bound_cards). */
export interface BoundsResult {
  grid_km?: number;
  precomputed?: boolean;
  status?: SolveStatus | string;
  max_hospitals_grid: number;
  candidates_total?: number;
  capacity: {
    total_demand_beds: number;
    with_city: number;
    without_city: number;
    applies: number;
    U_max_general: number;
    U_max_city: number;
  };
  coverage_lscp: BoundStep;
  coverage_lscp_k_list: BoundStep;
  p_min: BoundStep & { start_value?: number };
  labels_tr: Record<string, string>;
  time_s?: number;
  [k: string]: unknown;
}

/* ------------------------------------------------------------------------------------------------ çözüm sonucu */

export interface HospitalCosts {
  yapi: number;
  altyapi: number;
  donanim: number;
  kdv: number;
  sabit: number;
  isletme: number;
  arsa: number;
  /** açılış = inşaat + sabit + arsa [+ işletme] */
  acilis: number;
  /** bu hastaneye atanan nüfusun erişim maliyeti */
  erisim: number;
}

export interface NeighbourClassCost {
  class: ClassCode;
  beds: number;
  opening_tl: number;
  per_bed_tl: number;
}

/** Şartname 7.5 "neden bu boyut?". */
export interface HospitalSize {
  class_min_beds: number;
  class_max_beds: number;
  at_bound: 'ust' | 'alt' | null;
  fixed_tl: number;
  per_bed_tl: number;
  opening_tl: number;
  next_class?: NeighbourClassCost;
  prev_class?: NeighbourClassCost;
}

export interface Hospital {
  cell_id: string;
  /** `layers.candidates` dizisindeki konum. */
  cand_pos: number;
  /** ızgara GeoJSON'undaki özellik sırası. */
  grid_index: number;
  lon: number;
  lat: number;
  district: string;
  class: ClassCode;
  class_name: string;
  beds: number;
  assigned_beds: number;
  population: number;
  mean_km: number;
  max_km: number;
  occupancy: number;
  costs: HospitalCosts;
  size: HospitalSize;
  locked_open?: boolean;
  /** yalnız manuel değerlendirmede */
  class_given?: boolean;
  beds_given?: boolean;
}

/** Atama yayları (paralel diziler). `hospital` = `hospitals` sırası. Hasta akışı yayları için. */
export interface Assignment {
  demand_cell: string[];
  hospital: number[];
  share: number[];
  km: number[];
  population: number[];
}

export interface CostBreakdown {
  yapi: number;
  kdv: number;
  altyapi: number;
  donanim: number;
  sabit: number;
  arsa: number;
  isletme: number;
  erisim: number;
  insaat: number;
  acilis: number;
  toplam: number;
}

export interface Kpis {
  n_hospitals: number;
  count_by_class: Record<ClassCode, number>;
  total_beds: number;
  total_demand_beds: number;
  population: number;
  pop_weighted_mean_km: number;
  /** anahtarlar "10", "20", "30" (km) → nüfus payı (0–1) */
  share_pop_within_km: Record<string, number>;
  max_km: number;
  mean_occupancy: number;
  beds_per_1000: number;
  cost_per_capita_tl: number;
}

export interface SolutionBounds {
  ub_tl: number | null;
  lb_tl: number | null;
  gap: number | null;
  lb_source: string | null;
  lb_scope: string | null;
  objective_tl: number | null;
  model_objective_tl?: number | null;
  budget_eps_tl?: number | null;
  lb_by_source?: Record<string, number>;
  [k: string]: unknown;
}

export interface CheckReport {
  ok: boolean;
  violations: unknown[];
  checks: Record<string, boolean>;
  n_checks: number;
  summary_tr: string;
  [k: string]: unknown;
}

export interface Duals {
  /** talep hücreleri (layers.demand ile aynı sıra) */
  demand_cell: string[];
  marginal_cost_tl: number[];
  marginal_cost_per_person_tl: number[];
  /** hastane sırasıyla: 1 ek yatağın değeri (TL) */
  bed_value_tl: number[];
  note_tr?: string;
}

export interface LayerRings {
  R_km: number;
  /** haritada çizilecek kuş uçuşu yarıçap (km) */
  R_crowfly_km: number;
  factor: number | null;
  note_tr: string;
}

/** Izgaranın TÜM hücreleri, GeoJSON sırasıyla. */
export interface LayerAccess {
  order: 'grid' | 'demand';
  n: number;
  /** en yakın AÇIK hastaneye yol km'si */
  nearest_km: number[];
  /** `hospitals` sırası */
  nearest_hospital: number[];
  beyond_R_cells: number;
  beyond_R_population?: number;
  note_tr: string;
}

/** Modelin talep hücreleri (nüfus > 0). */
export interface LayerDemand {
  cell_id: string[];
  grid_index: number[];
  /** bu senaryonun nüfusu (2030 ve GHS kaynağında GeoJSON `pop` yerine bu kullanılmalı) */
  population: number[];
  demand_beds: number[];
  /** talebin en büyük payını alan hastane (`hospitals` sırası; −1 = atanmamış) */
  service_hospital: number[];
  service_share: number[];
  note_tr: string;
}

/** Modelin aday hücreleri (`hospitals[].cand_pos` bu sıradaki konum). */
export interface LayerCandidates {
  cell_id: string[];
  grid_index: number[];
  /** 0 aday kuralı · 1 kapsama onarımı · 2 kullanıcı 'aç' kilidi */
  origin: (0 | 1 | 2)[];
  open: (0 | 1)[];
  /** konum listeleri (bu dizideki sıra) */
  locked_open: number[];
  locked_closed: number[];
  origin_tr: Record<string, string>;
}

export interface ResultLayers {
  grid_km: number;
  rings: LayerRings;
  /** hastane yoksa (olursuz) bulunmaz */
  access?: LayerAccess;
  demand: LayerDemand;
  candidates: LayerCandidates;
}

/** İz / ilerleme olayı (SSE `progress` ve `result.trace`). */
export interface TraceEvent {
  kind: 'phase' | 'cozum' | 'heuristic' | 'lagrange' | 'incumbent' | 'log' | 'bitti' | (string & {});
  phase?: string | number;
  phase_tr?: string;
  /** işin (SSE) ya da evrenin (trace) başından saniye */
  t?: number;
  t_total?: number;
  lb?: number | null;
  ub?: number | null;
  gap?: number | null;
  primal?: number | null;
  dual?: number | null;
  iter?: number;
  status?: SolveStatus;
  source?: string;
  [k: string]: unknown;
}

export interface SeedInfo {
  mode: ObjectiveMode;
  n: number;
  ok: boolean;
  adopted: boolean;
  objective_tl: number;
}

export interface InstanceInfo {
  I: number;
  J: number;
  K: number;
  arcs: number;
  total_demand_beds: number;
  classes: DerivedClass[];
  lambda_tl_per_person_km: number;
  pv_factor: number;
  coverage_repair?: Record<string, number>;
  [k: string]: unknown;
}

interface ResultBase {
  grid_km: GridKm;
  mode: ObjectiveMode;
  params_hash: string;
  structure_hash: string;
  overrides: Overrides;
  locks: Locks;
  message_tr: string;
  times: Record<string, number>;
  warnings?: string[];
}

/** Başarılı (olurlu) çözüm. `status` 'durduruldu' olabilir (iptal / yerini yeni isteğe bıraktı). */
export interface SolveResultOk extends ResultBase {
  ok: true;
  status: SolveStatus;
  status_tr: string;
  /** şartname 7.2 */
  certificate_tr: string;
  solver_status: string;
  solution_source: string;
  time_limit_s: number;
  target_gap: number;
  bounds: SolutionBounds;
  hospitals: Hospital[];
  assignment: Assignment;
  costs: CostBreakdown;
  kpis: Kpis;
  check: CheckReport;
  duals?: Duals | null;
  layers: ResultLayers;
  trace: TraceEvent[];
  seeds?: SeedInfo[] | null;
  instance?: InstanceInfo;
  budget?: Record<string, unknown> | null;
  first_incumbent_s?: number | null;
  first_dual_s?: number | null;
  /** yalnız önceden çözülmüş dosyalarda */
  precomputed?: Record<string, unknown>;
  [k: string]: unknown;
}

/** Olursuz ya da hatalı çözüm: `message_tr` nedeni ve öneriyi verir; harita katmanı yoktur. */
export interface SolveResultFailed extends Partial<Omit<ResultBase, 'message_tr' | 'times'>> {
  ok: false;
  status: SolveStatus;
  status_tr?: string;
  message_tr: string;
  times?: Record<string, number>;
  trace?: TraceEvent[];
  [k: string]: unknown;
}

export type SolveResult = SolveResultOk | SolveResultFailed;

export interface EvaluateComparison {
  available: boolean;
  diff_tl?: number;
  pct_vs_best?: number;
  pct_vs_lower_bound?: number;
  reference?: { ub_tl: number; lb_tl: number; gap: number; status: SolveStatus; n_hospitals: number; time_limit_bucket_s: number; cache_entry: string };
  message_tr: string;
}

/** POST /api/evaluate sonucu (manuel mod). */
export interface EvaluateResult extends Omit<ResultBase, 'warnings'> {
  ok: boolean;
  status: EvaluateStatus | string;
  status_tr: string;
  objective_tl: number;
  method: 'motor_evaluate' | 'motor_evaluate_sabit_sinif' | 'karisik' | string;
  hospitals: Hospital[];
  assignment: Assignment;
  costs: CostBreakdown;
  kpis: Kpis;
  check: CheckReport;
  duals?: Duals | null;
  layers: ResultLayers;
  comparison: EvaluateComparison;
  added_candidates: string[];
  warnings: string[];
  [k: string]: unknown;
}

/* ------------------------------------------------------------------------------------------------ istekler */

export interface JobOptions {
  client_id?: string;
  /** aynı client_id'nin aynı türdeki bekleyen/çalışan işlerini iptal et ("en son istek kazanır") */
  replace?: boolean;
  use_cache?: boolean;
}

export interface ProblemRequest extends JobOptions {
  grid_km?: GridKm;
  scenario_year?: ScenarioYear;
  mode?: ObjectiveMode;
  overrides?: Overrides;
  allow_outside_range?: boolean;
}

export interface SolveRequest extends ProblemRequest {
  locks?: Locks;
  time_limit?: number;
  gap?: number;
  duals?: boolean;
}

export interface HospitalInput {
  cell_id: string;
  class?: ClassCode;
  beds?: number;
}

export interface EvaluateRequest extends ProblemRequest {
  hospitals: HospitalInput[];
  duals?: boolean;
}

export interface BoundsRequest extends ProblemRequest {
  locks?: Locks;
  time_limit?: number;
  with_pmin?: boolean;
}

/** Faz 6: POST /api/explain (şimdilik 501). */
export interface ExplainRequest extends ProblemRequest {
  locks?: Locks;
  hospitals: HospitalInput[];
  what: ('hastane_kapat' | 'n_eksi_1' | 'n_arti_1')[];
  close?: string[];
  time_limit?: number | null;
}

export interface ExplainItem {
  kind: 'hastane_kapat' | 'n_eksi_1' | 'n_arti_1';
  cell_id?: string;
  feasible?: boolean;
  p?: number;
  status?: SolveStatus;
  objective_tl: number | null;
  opening_tl?: number;
  access_tl?: number;
  lb_tl?: number;
  gap?: number;
  delta_tl: number | null;
  delta_opening_tl?: number;
  delta_access_tl?: number;
  message_tr: string;
}

export interface ExplainResult {
  ok: boolean;
  status: string;
  base: { objective_tl: number; n_hospitals: number };
  items: ExplainItem[];
}

/** İş türü → istek / sonuç eşlemesi (jobs.ts genel yardımcıları için). */
export interface JobRequestMap {
  solve: SolveRequest;
  evaluate: EvaluateRequest;
  bounds: BoundsRequest;
  explain: ExplainRequest;
}
export interface JobResultMap {
  solve: SolveResult;
  evaluate: EvaluateResult;
  bounds: BoundsResult;
  explain: ExplainResult;
}
export type KnownJobType = keyof JobRequestMap;

/* ------------------------------------------------------------------------------------------------ iş yanıtları */

/** POST /api/solve|evaluate|bounds yanıtı (202 kuyrukta / 200 önbellekten, `result` dolu) ve GET /api/jobs/{id}. */
export interface JobSnapshot<R = unknown> {
  job_id: string;
  type: JobType;
  state: JobState;
  state_tr: string;
  client_id: string | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  queued_s?: number;
  run_s?: number;
  from_cache: boolean;
  cancel_requested: boolean;
  cancel_reason_tr?: string | null;
  replaced_by: string | null;
  position: number | null;
  progress: TraceEvent | null;
  n_progress?: number;
  info: {
    grid_km?: GridKm;
    mode?: ObjectiveMode;
    scenario_year?: ScenarioYear;
    time_limit_s?: number;
    gap?: number;
    locks?: { open: number; closed: number };
    params_hash?: string;
    overrides?: Overrides;
    [k: string]: unknown;
  };
  warnings: string[];
  error: { message_tr: string; detail?: string } | null;
  has_result?: boolean;
  result_evicted?: boolean;
  events_url: string;
  cancel_url: string;
  /** yalnız gönderim yanıtında: bu istekle iptal edilen eski işler */
  replaced?: string[];
  /** yalnız iptal yanıtında */
  cancel_effective?: boolean;
  result?: R;
}

/* ------------------------------------------------------------------------------------------------ SSE */

export interface SseStateData {
  state: JobState;
  position?: number;
  cancel_requested?: boolean;
  message_tr?: string;
}

export interface SseHeartbeatData {
  state: JobState;
  position: number | null;
  elapsed_s: number;
  run_s: number | null;
  progress: TraceEvent | null;
  cancel_requested: boolean;
}

export interface SseResultData<R = unknown> {
  state: 'done' | 'cancelled';
  from_cache: boolean;
  job_id: string;
  type: JobType;
  cancel_reason_tr: string | null;
  error: { message_tr: string } | null;
  result: R;
}

export interface SseErrorData {
  state: 'failed' | 'cancelled';
  job_id: string;
  type: JobType;
  message_tr: string;
  detail?: string;
}

export type JobEvent<R = unknown> =
  | { event: 'state'; id: number | null; data: SseStateData }
  | { event: 'progress'; id: number | null; data: TraceEvent }
  | { event: 'heartbeat'; id: number | null; data: SseHeartbeatData }
  | { event: 'result'; id: number | null; data: SseResultData<R> }
  | { event: 'error'; id: number | null; data: SseErrorData };

/* ------------------------------------------------------------------------------------------------ önceden çözülmüş */

/** GET /api/precomputed/scenarios */
export interface ScenarioIndex {
  generated_by: string;
  generated_at: string;
  note_tr: string;
  scenarios: Record<string, { title: string; grids: Record<string, PrecomputedRef> }>;
  live_check: Record<string, unknown>;
}

export interface PSweepPoint {
  p: number;
  ok: boolean;
  status: SolveStatus;
  ub_tl: number;
  lb_tl: number;
  gap: number;
  lb_source: string;
  opening_tl: number;
  access_tl: number;
  total_tl: number;
  count_by_class: Record<ClassCode, number>;
  total_beds: number;
  pop_weighted_mean_km: number;
  time_s: number;
  /** açık hücre kimlikleri: U eğrisinde bir p'ye tıklayınca haritada gösterilebilir */
  hospitals: string[];
  [k: string]: unknown;
}

/** GET /api/precomputed/p_sweep_{3,5}km (U eğrisi; Faz 6). */
export interface PSweep {
  generated_by: string;
  generated_at: string;
  grid_km: GridKm;
  settings: Record<string, unknown>;
  free: {
    ub_tl: number;
    lb_tl: number;
    gap: number;
    n_hospitals: number;
    opening_tl: number;
    access_tl: number;
    count_by_class: Record<ClassCode, number>;
    status: SolveStatus;
    lb_source: string;
  };
  points: PSweepPoint[];
  acceptance: { best_p: number; summary_tr: string; passed: boolean; [k: string]: unknown };
  [k: string]: unknown;
}

/** /api/precomputed/{ad} için izinli adlar. */
export type PrecomputedName =
  | `default_${GridKm}km`
  | `p_sweep_${3 | 5}km`
  | 'scenarios'
  | `scenario_${string}_${GridKm}km`;
