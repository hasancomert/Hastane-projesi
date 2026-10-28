/**
 * Bilgi sekmelerinin (Faz 6: Hocanın Listesi · Veri · Model · Sınırlılıklar · Kaynakça) kullandığı, `types.ts`'te
 * gevşek tipli bırakılmış alanların ayrıntılı tipleri. Hepsi isteğe bağlıdır: sunucu bir alanı vermezse arayüz
 * o satırı göstermez (sayı uydurmaz).
 *
 *   - /api/measures (data/processed/ankara_olculer.json) ayrıntıları: uç noktalar, çap, Kızılay, ağırlık merkezi,
 *     su, hastane arsası × hücre karşılaştırması;
 *   - çözüm sonucunun tanı alanları (lagrange, heuristic, core_mip, model, improve) — Model sekmesindeki
 *     "bu çözümde" kutusu;
 *   - p taramasının kabul ve "fark gap içinde" alanları (U eğrisinin düz dibi).
 */
import type { ParamStatus } from './types.ts';

/* ------------------------------------------------------------------------------------------------ /api/measures */

export interface MeasureDistrictRef {
  ilce: string;
  ilce_key: string;
  inside: boolean;
  distance_to_district_m: number;
}

export interface MeasurePoint {
  lon: number;
  lat: number;
  label?: string;
  name?: string;
  district?: MeasureDistrictRef;
}

export interface MeasureValueLite {
  value: number;
  unit: string;
  method: string;
  text: string;
}

export interface MeasuresExtent {
  west?: MeasurePoint;
  east?: MeasurePoint;
  north?: MeasurePoint;
  south?: MeasurePoint;
  west_east_geodesic_km?: MeasureValueLite;
  north_south_geodesic_km?: MeasureValueLite;
  utm_bbox?: { width_km?: MeasureValueLite; height_km?: MeasureValueLite; note?: string };
  note?: string;
}

export interface MeasuresDiameter {
  distance_km: MeasureValueLite;
  p1: MeasurePoint;
  p2: MeasurePoint;
  proof?: string;
  hull_vertex_count?: number;
  bruteforce_pairs?: number;
}

export interface MeasuresKizilay {
  point: MeasurePoint;
  farthest: MeasurePoint & { distance_km: MeasureValueLite };
  nearest?: MeasurePoint & { distance_km: MeasureValueLite };
}

export interface MeasuresCentroid {
  point: MeasurePoint;
  method?: string;
  distance_to_kizilay_km?: MeasureValueLite;
}

export interface AreaComparisonRow {
  source: string;
  source_full?: string;
  value_km2: number;
  scope: string;
  source_id?: string;
  /** kayıt durumu; dosyada ek olarak 'kaynakli_ikincil' (ikincil kaynaktan) geçer */
  status?: ParamStatus | (string & {});
  lakes_excluded?: boolean;
  diff_km2: number;
  diff_pct: number;
  text: string;
  diff_vs_land_km2?: number;
  diff_vs_land_pct?: number;
  reference?: boolean;
}

export interface HospitalLandShare {
  cell_km: number;
  cell_area_km2: number;
  share_pct: number;
  fits_per_cell: number;
}

export interface HospitalLandCase {
  key: string;
  label: string;
  beds: number;
  beds_basis?: string;
  class: string;
  land_m2_per_bed: number;
  land_basis?: string;
  land_m2: number;
  land_km2: number;
  floor_area_m2?: number;
  floor_area_method?: string;
  floor_to_land_ratio?: number;
  shares: HospitalLandShare[];
  text?: string;
}

export interface HospitalLand {
  cells?: { cell_km: number; area_km2: number; default: boolean }[];
  cases?: HospitalLandCase[];
  min_parcel?: { m2: number; beds: number; share_pct_smallest_cell: number; method?: string };
  province_total?: { beds: number; land_km2: number; share_of_province_pct: number; method?: string };
  conclusion: string;
  caveat?: string;
}

export interface MeasuresWater {
  permanent_water_km2?: MeasureValueLite;
  land_area_km2?: MeasureValueLite;
  water_share_pct?: number;
  province_density_per_km2?: number;
  province_density_land_per_km2?: number;
}

/** /api/measures'ın Bilgi sekmelerinin okuduğu kısmı (tam dosya `Measures` tipinde). */
export interface MeasuresDetail {
  meta?: { title?: string; generated_by?: string; generated?: string; source_ids?: string[] };
  area?: { geodesic_km2?: MeasureValueLite; note?: string };
  perimeter?: { geodesic_km?: MeasureValueLite; note?: string };
  extent?: MeasuresExtent;
  diameter?: MeasuresDiameter;
  kizilay?: MeasuresKizilay;
  centroid?: MeasuresCentroid;
  water?: MeasuresWater;
  area_comparison?: { reference_km2: number; reference?: string; rows: AreaComparisonRow[]; own_rows?: AreaComparisonRow[]; note?: string; method?: string };
  districts?: {
    rows: { ilce: string; ilce_key: string; area_geodesic_km2: number; pop_2025: number; density_per_km2: number; water_km2?: number; density_land_per_km2?: number }[];
    sum_pop_2025?: number;
    sum_area_geodesic_km2?: number;
    method?: string;
  };
  hospital_land?: HospitalLand;
  card?: { title: string; items: { key: string; label: string; value_text: string; tooltip: string }[]; hospital_land_conclusion: string };
}

/* ------------------------------------------------------------------------------------------------ çözüm tanıları */

/** result.lagrange — Lagrange alt sınırı evresi (backend/model/lagrange.py). */
export interface LagrangeDiag {
  bound_tl?: number;
  ub_tl?: number;
  gap?: number;
  iters?: number;
  time_s?: number;
  init_s?: number;
  init_source?: string;
  lh_calls?: number;
  sub_ms?: number;
  phase_b?: { bound_tl?: number; iters?: number; time_s?: number };
}

/** result.model — kurulan MILP'in boyutu. */
export interface ModelDiag {
  rows?: number;
  cols?: number;
  binaries?: number;
  I?: number;
  J?: number;
  K?: number;
  arcs?: number;
  link_rows?: number;
  per_class_rows?: number;
  nnz?: number;
}

export interface HeuristicDiag {
  ok?: boolean;
  objective_tl?: number;
  time_s?: number;
}

export interface CoreMipDiag {
  attempt?: number;
  candidates?: number;
  arcs?: number;
  status?: string;
  primal_tl?: number;
  time_s?: number;
  improved?: boolean;
  is_full_model?: boolean;
}

export interface ImproveDiag {
  lp_round?: { lp_s?: number; lp_bound_tl?: number; time_s?: number };
  windows?: { windows?: number; improved?: number; time_s?: number; gain_tl?: number };
}

/** Sonucun tanı alanları (SolveResultOk'ta `[k: string]: unknown`). */
export interface SolveDiagnostics {
  lagrange?: LagrangeDiag | null;
  model?: ModelDiag | null;
  heuristic?: HeuristicDiag | null;
  core_mip?: CoreMipDiag | null;
  improve?: ImproveDiag | null;
  solution_source?: string;
  bounds?: { mip_kind?: string; lb_by_source?: Record<string, number>; lagrange_tl?: number; mip_dual_tl?: number; mip_nodes?: number };
}

/* ------------------------------------------------------------------------------------------------ p taraması */

export interface PSweepPointDetail {
  p: number;
  ub_tl: number;
  lb_tl: number;
  gap: number;
  /** bu p'nin en iyi bulunan çözümü − serbest optimum (TL) */
  fark_tl?: number;
  /** alt sınırı serbest çözümün değerini aşmıyor: n*'dan ayırt edilemiyor */
  fark_gap_icinde?: boolean;
  refined?: boolean;
}

export interface PSweepAcceptanceDetail {
  best_p: number;
  free_n?: number;
  free_ub_tl?: number;
  free_lb_tl?: number;
  tolerance?: number;
  plateau_p?: number[];
  unresolved_p?: number[];
  free_n_unique_within_tol?: boolean;
  summary_tr: string;
  passed: boolean;
}
