/**
 * Bilgi sekmelerinin canlı sayıları: gösterilen çözümden (`result`), /api/meta'dan, /api/measures'tan ve önceden
 * çözülmüş dosyalardan (p taraması, senaryo dizini) TÜRETİLİR — burada yeni sayı yoktur. Saf fonksiyonlar (birim
 * testli); her biri veri yoksa null / boş döndürür, arayüz o cümleyi göstermez.
 */
import type { MeasuresDetail, PSweepAcceptanceDetail, PSweepPointDetail } from '../api/infoTypes.ts';
import type { ClassCode, DerivedClass, GridKm, Hospital, Meta, PSweep, SolveResultOk } from '../api/types.ts';

/* ------------------------------------------------------------------------------------------------ sınıflar */

export interface ClassCost {
  code: ClassCode;
  name: string;
  L: number;
  U: number;
  enabled: boolean;
  disabledReason: string;
  /** hastane başı sabit kısım (çekirdek alan A0 + F_sabit [+ işletme]) TL */
  fixedTl: number;
  /** yatak başı (arsa hariç) TL */
  perBedTl: number;
  fixedParts: DerivedClass['fixed_parts'];
  varParts: DerivedClass['var_parts'];
}

/** Sınıf maliyetleri: gösterilen çözümün ayarlarından (result.instance.classes), yoksa varsayılan (meta.derived). */
export function classCosts(result: SolveResultOk | null, meta: Meta | null): { rows: ClassCost[]; fromResult: boolean } {
  const src = result?.instance?.classes?.length ? result.instance.classes : meta?.derived.classes ?? [];
  return {
    fromResult: !!result?.instance?.classes?.length,
    rows: src.map((c) => ({
      code: c.code,
      name: c.name,
      L: c.L,
      U: c.U,
      enabled: c.enabled,
      disabledReason: c.disabled_reason,
      fixedTl: c.fixed_tl,
      perBedTl: c.var_tl_per_bed,
      fixedParts: c.fixed_parts,
      varParts: c.var_parts,
    })),
  };
}

export function minMax(xs: number[]): [number, number] | null {
  const v = xs.filter((x) => Number.isFinite(x));
  if (!v.length) return null;
  return [Math.min(...v), Math.max(...v)];
}

/* ------------------------------------------------------------------------------------------------ boyut yığılması */

export interface BoundPile {
  code: ClassCode;
  beds: number;
  n: number;
  /** sınıftaki toplam hastane */
  total: number;
  next?: ClassCode;
}

/** Sınıf üst sınırında (ör. 199, 399) ve alt sınırında (ör. 50) kalan hastaneler. */
export function sizePiles(result: SolveResultOk | null): { upper: BoundPile[]; lower: BoundPile[] } {
  const upper: BoundPile[] = [];
  const lower: BoundPile[] = [];
  if (!result) return { upper, lower };
  const byClass = new Map<ClassCode, Hospital[]>();
  for (const h of result.hospitals) byClass.set(h.class, [...(byClass.get(h.class) ?? []), h]);
  for (const [code, hs] of byClass) {
    const up = hs.filter((h) => h.size?.at_bound === 'ust');
    const lo = hs.filter((h) => h.size?.at_bound === 'alt');
    if (up.length) upper.push({ code, beds: up[0].size.class_max_beds, n: up.length, total: hs.length, next: up[0].size.next_class?.class });
    if (lo.length) lower.push({ code, beds: lo[0].size.class_min_beds, n: lo.length, total: hs.length });
  }
  const order = (c: ClassCode) => ['IV-C', 'V-B', 'V-C', 'V-D'].indexOf(c);
  upper.sort((a, b) => order(a.code) - order(b.code));
  lower.sort((a, b) => order(a.code) - order(b.code));
  return { upper, lower };
}

/* ------------------------------------------------------------------------------------------------ kırsal hastaneler */

export interface RuralFacts {
  /** sınıfın en küçük boyutunda (alt sınır) açılan hastaneler, doluluğa göre artan */
  atMin: Hospital[];
  minBeds: number | null;
  /** en düşük doluluklu hastane */
  lowest: Hospital | null;
  /** R kapsama kısıtı (yol km) */
  R: number | null;
}

export function ruralFacts(result: SolveResultOk | null, meta: Meta | null): RuralFacts {
  const R = typeof result?.layers?.rings?.R_km === 'number' ? result.layers.rings.R_km : meta?.access_rings?.R_km ?? null;
  if (!result?.hospitals.length) return { atMin: [], minBeds: null, lowest: null, R };
  const atMin = result.hospitals.filter((h) => h.size?.at_bound === 'alt').sort((a, b) => a.occupancy - b.occupancy);
  const lowest = [...result.hospitals].sort((a, b) => a.occupancy - b.occupancy)[0] ?? null;
  return { atMin, minBeds: atMin[0]?.size.class_min_beds ?? null, lowest, R };
}

/** İlçe adları (tekrarsız, ilk geliş sırasıyla). */
export function districtsOf(hs: Hospital[]): string[] {
  return [...new Set(hs.map((h) => h.district).filter(Boolean))];
}

/* ------------------------------------------------------------------------------------------------ ızgara boyutu */

export interface GridCompareRow {
  km: GridKm;
  n: number;
  ubTl: number;
  lbTl: number;
  gap: number;
  status: string;
  timeLimitS: number;
}

/** Varsayılan senaryonun önceden çözülmüş sonuçları (meta.scenarios[varsayilan].grids). */
export function gridComparison(meta: Meta | null, scenarioId = 'varsayilan'): GridCompareRow[] {
  const sc = meta?.scenarios.find((s) => s.id === scenarioId);
  if (!sc) return [];
  const out: GridCompareRow[] = [];
  for (const [k, g] of Object.entries(sc.grids)) {
    const p = g.precomputed;
    if (!p) continue;
    out.push({ km: Number(k) as GridKm, n: p.n_hospitals, ubTl: p.ub_tl, lbTl: p.lb_tl, gap: p.gap, status: p.status, timeLimitS: p.time_limit_s });
  }
  return out.sort((a, b) => b.km - a.km);
}

/* ------------------------------------------------------------------------------------------------ U eğrisinin dibi */

export interface PlateauFacts {
  km: number;
  freeN: number;
  freeUb: number;
  freeLb: number;
  freeGap: number;
  /** n*'dan ayırt edilemeyen p'ler (fark gap içinde), n* dahil, sıralı */
  plateau: number[];
  unique: boolean;
  /** n* − 1 ve n* + 1 noktaları (taranmışsa) */
  neighbors: { p: number; diffTl: number; gap: number; inGap: boolean }[];
  summary: string;
}

/** Ardışık tamsayıları aralık olarak yaz: [82,83,84,86] → "82–84, 86". */
export function rangesText(ps: number[]): string {
  const v = [...new Set(ps)].sort((a, b) => a - b);
  const out: string[] = [];
  let i = 0;
  while (i < v.length) {
    let j = i;
    while (j + 1 < v.length && v[j + 1] === v[j] + 1) j++;
    out.push(j > i ? `${v[i]}–${v[j]}` : `${v[i]}`);
    i = j + 1;
  }
  return out.join(', ');
}

export function plateauFacts(sweep: PSweep | null): PlateauFacts | null {
  if (!sweep) return null;
  const acc = sweep.acceptance as unknown as PSweepAcceptanceDetail;
  const freeN = acc.free_n ?? sweep.free.n_hospitals;
  const pts = sweep.points as unknown as PSweepPointDetail[];
  const neighbors = [freeN - 1, freeN + 1]
    .map((p) => pts.find((x) => x.p === p))
    .filter((x): x is PSweepPointDetail => !!x && typeof x.fark_tl === 'number')
    .map((x) => ({ p: x.p, diffTl: x.fark_tl as number, gap: x.gap, inGap: !!x.fark_gap_icinde }));
  const plateau = acc.plateau_p?.length ? [...acc.plateau_p].sort((a, b) => a - b) : [freeN];
  return {
    km: sweep.grid_km,
    freeN,
    freeUb: sweep.free.ub_tl,
    freeLb: sweep.free.lb_tl,
    freeGap: sweep.free.gap,
    plateau,
    unique: acc.free_n_unique_within_tol ?? plateau.length <= 1,
    neighbors,
    summary: acc.summary_tr,
  };
}

/* ------------------------------------------------------------------------------------------------ ölçüler */

export function measureItem(m: MeasuresDetail | null, key: string): { label: string; value: string; tooltip: string } | null {
  const it = m?.card?.items.find((x) => x.key === key);
  return it ? { label: it.label, value: it.value_text, tooltip: it.tooltip } : null;
}

/** "Arazi bağlayıcı değil" karşılaştırmasının ana satırı (300 yataklı genel hastane) ve seçili ızgaradaki payı. */
export function landShare(m: MeasuresDetail | null, gridKm: number): { label: string; landM2: number; landKm2: number; cellKm2: number; sharePct: number; floorM2?: number } | null {
  const c = m?.hospital_land?.cases?.[0];
  if (!c) return null;
  const s = c.shares.find((x) => x.cell_km === gridKm) ?? c.shares[0];
  if (!s) return null;
  return { label: c.label, landM2: c.land_m2, landKm2: c.land_km2, cellKm2: s.cell_area_km2, sharePct: s.share_pct, floorM2: c.floor_area_m2 };
}

/** Bir metinden belirli bir ifadeyi içeren cümleyi çıkar (cümle sınırı ". "; kayıttaki gerekçe/not metinleri). */
export function sentenceWith(text: string | null | undefined, needle: string): string | null {
  if (!text) return null;
  const i = text.indexOf(needle);
  if (i < 0) return null;
  const prev = text.lastIndexOf('. ', i);
  const start = prev < 0 ? 0 : prev + 2;
  const m = /\.(\s|$)/.exec(text.slice(i));
  const end = m ? i + m.index + 1 : text.length;
  return text.slice(start, end).trim();
}

/** İfadeyi içeren TIRNAK İÇİ alıntıyı çıkar (ör. TÜİK metaverisinden "geçici koruma" cümlesi); yoksa null. */
export function quoteWith(text: string | null | undefined, needle: string): string | null {
  if (!text) return null;
  const i = text.indexOf(needle);
  if (i < 0) return null;
  const open = Math.max(text.lastIndexOf('"', i), text.lastIndexOf('“', i));
  if (open < 0) return null;
  const closeA = text.indexOf('"', i);
  const closeB = text.indexOf('”', i);
  const close = [closeA, closeB].filter((x) => x >= 0).sort((a, b) => a - b)[0];
  if (close === undefined) return null;
  return text.slice(open + 1, close).trim();
}
