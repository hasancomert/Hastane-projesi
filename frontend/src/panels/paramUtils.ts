/**
 * Parametre paneli yardımcıları (saf fonksiyonlar):
 *   - gösterim ölçeği: "oran" birimli kesirler yüzde (%) olarak, büyük TL değerleri milyon/milyar TL olarak girilir;
 *   - doğrulama: modelin tanım kümesi (`allowed`, sunucu her zaman reddeder) ve kaydırıcı aralığı (`range`,
 *     yalnız "aralık dışına izin ver" açıkken gönderilir);
 *   - ilgililik: seçili moda/seçeneğe göre kullanılmayan anahtarlar (backend/model/params.py `effective_values()`
 *     ile aynı kurallar) soluk gösterilir ve nedeni yazılır;
 *   - seçenek etiketleri.
 */
import type { Meta, ParamAlternative, ParamMeta, ParamValue } from '../api/types.ts';
import { fmtNumber } from '../format/number.ts';

/** Üstteki "Problem" bölümünde gösterilenler: gruplarda yalnız özet satırı olarak görünür (iki kez giriş yok). */
export const QUICK_KEYS = new Set(['grid.cell_km', 'population.scenario_year', 'solver.objective_mode', 'solver.budget_tl', 'solver.p_fixed']);

/**
 * Bilgi amaçlı anahtarlar: modelde doğrudan kullanılmaz (params.py effective_values "bilgi amaçlı" listesi; yakıt
 * girdileri ayrı ele alınır çünkü c'yi türetebilir). Panelde salt okunur satır olarak gösterilir.
 */
export const INFO_ONLY_KEYS = new Set([
  'demand.hb.current_occupancy',
  'land.min_parcel_m2',
  'cost.unit.V-A',
  'population.province_2025',
  'population.province_2030',
  'access.visits_per_capita_total',
  'access.share_hospital_visits',
]);

const INFO_ONLY_NOTE: Record<string, string> = {
  'demand.hb.current_occupancy': 'Bilgi amaçlı: Türkiye’nin gözlenen doluluğu. Modelde hedef doluluk (Hill-Burton) kullanılır.',
  'land.min_parcel_m2': 'Bilgi amaçlı: asgari parsel. Arsa maliyeti yatak başı arsa alanıyla hesaplanır.',
  'cost.unit.V-A': 'Bilgi amaçlı: eğitim-araştırma hastanesi birim maliyeti. Modelin sınıfları IV-C, V-B, V-C, V-D.',
  'population.province_2025': 'Bilgi amaçlı: hücre nüfusları ilçe bazında TÜİK 2025’e zaten kalibre (il toplamı bu sayı).',
  'population.province_2030': 'Bilgi amaçlı: 2030 senaryosunun il toplamı (ızgaralara önceden işlendi).',
  'access.visits_per_capita_total': 'Bilgi amaçlı: v’nin alternatif türetmesi için girdi (v = toplam × hastane payı); model v’yi doğrudan kullanır.',
  'access.share_hospital_visits': 'Bilgi amaçlı: v’nin alternatif türetmesi için girdi; model v’yi doğrudan kullanır.',
};

export function infoOnlyNote(key: string): string {
  return INFO_ONLY_NOTE[key] ?? 'Bilgi amaçlı: modelde doğrudan kullanılmaz.';
}

/* ------------------------------------------------------------------------------------------------ seçenekler */

const CHOICE_LABELS: Record<string, Record<string, string>> = {
  'demand.mode': { oran: 'Oran', hill_burton: 'Hill-Burton' },
  'population.grid_source': { worldpop_r2025a: 'WorldPop', ghs_pop_r2023a: 'GHS-POP' },
  'access.circuity_mode': { mesafeye_bagli: 'Mesafeye bağlı', sabit: 'Sabit katsayı' },
  'solver.objective_mode': { toplam: 'Toplam', butce: 'Bütçe', sabit_p: 'Sabit p' },
  'population.scenario_year': { '2025': '2025', '2030': '2030' },
  'grid.cell_km': { '2': '2 km', '3': '3 km', '5': '5 km' },
};

export function choiceLabel(param: ParamMeta, value: ParamValue | undefined): string {
  if (value === undefined) return '—';
  const known = CHOICE_LABELS[param.key]?.[String(value)];
  if (known) return known;
  if (typeof value === 'boolean') return value ? 'evet' : 'hayır';
  return String(value);
}

export function choiceOptions(param: ParamMeta): (string | number)[] {
  return param.allowed.kind === 'choice' ? param.allowed.options : [];
}

/* ------------------------------------------------------------------------------------------------ sayı gösterimi */

export interface NumberSpec {
  integer: boolean;
  /** gösterilen = değer / factor (yüzde: 0,01; milyon TL: 1e6) */
  factor: number;
  /** giriş kutusundaki birim: '%', 'milyon TL', 'milyar TL' ya da null (kayıttaki birim aynen) */
  displayUnit: string | null;
  /** giriş kutusunun ondalık basamağı */
  decimals: number;
  /** kaydırıcı ve ok tuşu adımı (gösterim biriminde) */
  step: number;
  /** kaydırıcı aralığı (gösterim biriminde); null → kaydırıcı yok */
  slider: [number, number] | null;
}

const round6 = (x: number) => Math.round(x * 1e6) / 1e6;

function decimalsOf(x: number): number {
  if (!Number.isFinite(x)) return 0;
  const s = String(round6(x));
  const i = s.indexOf('.');
  return i < 0 ? 0 : s.length - i - 1;
}

/** "Güzel" adım: 1 · 2 · 2,5 · 5 × 10^k. */
export function niceStep(x: number): number {
  if (!(x > 0)) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(x)));
  const m = x / p;
  const n = m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10;
  return round6(n * p);
}

export function isPercentParam(p: ParamMeta): boolean {
  const u = p.unit.toLocaleLowerCase('tr-TR');
  if (!/(^|[^a-zçğıöşü])oran([^a-zçğıöşü]|$)/.test(u) || u.includes('boyutsuz')) return false;
  if (p.allowed.kind !== 'number') return false;
  const hi = p.range ? p.range[1] : typeof p.value === 'number' ? p.value : 1;
  return hi <= 1;
}

export function numberSpec(p: ParamMeta): NumberSpec {
  const integer = p.allowed.kind === 'integer';
  const value = typeof p.value === 'number' ? p.value : 0;
  const u = p.unit.toLocaleLowerCase('tr-TR');
  let factor = 1;
  let displayUnit: string | null = null;
  if (isPercentParam(p)) {
    factor = 0.01;
    displayUnit = u.includes('/ yıl') ? '% / yıl' : '%';
  } else if (u.startsWith('tl')) {
    const maxAbs = Math.max(Math.abs(p.range?.[1] ?? 0), Math.abs(value));
    if (maxAbs >= 1e11) {
      factor = 1e9;
      displayUnit = 'milyar TL';
    } else if (maxAbs >= 1e8) {
      factor = 1e6;
      displayUnit = 'milyon TL';
    }
  }
  const slider: [number, number] | null = p.range ? [round6(p.range[0] / factor), round6(p.range[1] / factor)] : null;
  const span = slider ? slider[1] - slider[0] : Math.abs(value / factor) || 1;
  let step = integer ? Math.max(1, Math.round(niceStep(span / 300))) : niceStep(span / 200);
  if (factor !== 1 && integer) step = 1;
  const altDecimals = p.alternatives.filter((a) => typeof a.value === 'number').map((a) => decimalsOf((a.value as number) / factor));
  // para (milyon/milyar TL) en çok 1, yüzde en çok 2, diğerleri en çok 4 ondalık
  const cap = factor >= 1e6 ? 1 : factor < 1 ? 2 : 4;
  const decimals = integer ? 0 : Math.min(cap, Math.max(decimalsOf(step), decimalsOf(value / factor), ...altDecimals, 0));
  return { integer, factor, displayUnit, decimals, step, slider };
}

export const toDisplay = (spec: NumberSpec, v: number) => round6(v / spec.factor);

export function fromDisplay(spec: NumberSpec, d: number): number {
  if (spec.integer) return Math.round(d);
  if (spec.factor >= 1e6) return Math.round(d * spec.factor);
  return round6(d * spec.factor);
}

/** Gösterim biriminde biçimli değer: "250 milyon TL", "%15", "3,15". */
export function fmtDisplay(spec: NumberSpec, v: number | null | undefined, withUnit = true): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  const d = toDisplay(spec, v);
  const n = fmtNumber(d, Math.max(spec.decimals, 0));
  if (!withUnit || !spec.displayUnit) return n;
  if (spec.displayUnit.startsWith('%')) return `%${n}${spec.displayUnit.slice(1)}`;
  return `${n} ${spec.displayUnit}`;
}

/** Kayıttaki birimin kısa hali (parantezsiz): "TL/m² (2026; …)" → "TL/m²". */
export function shortUnit(unit: string): string {
  const s = unit.replace(/\s*\(.*$/, '').trim();
  return s || unit;
}

/* ------------------------------------------------------------------------------------------------ doğrulama */

export interface ValueCheck {
  /** modelin tanım kümesi dışında: gönderilmez */
  error?: string;
  /** kaydırıcı aralığı dışında (tanım kümesi içinde) */
  outside?: boolean;
}

export function describeAllowed(p: ParamMeta, spec?: NumberSpec): string {
  const a = p.allowed;
  if (a.kind === 'choice') return `Seçenekler: ${a.options.map((o) => CHOICE_LABELS[p.key]?.[String(o)] ?? String(o)).join(' · ')}`;
  if (a.kind === 'bool') return 'Evet / hayır';
  if (a.kind === 'profile') return `Profil: ${a.format}`;
  const f = (x: number) => (spec ? fmtDisplay(spec, x) : fmtNumber(x, 4));
  const parts: string[] = [];
  if (a.min !== null) parts.push(`${a.min_inclusive ? '≥' : '>'} ${f(a.min)}`);
  if (a.max !== null) parts.push(`${a.max_inclusive ? '≤' : '<'} ${f(a.max)}`);
  const kind = a.kind === 'integer' ? 'tamsayı' : 'sayı';
  return parts.length ? `${kind}, ${parts.join(' ve ')}` : kind;
}

export function checkValue(p: ParamMeta, v: number, spec: NumberSpec): ValueCheck {
  const a = p.allowed;
  if (!Number.isFinite(v)) return { error: 'Geçerli bir sayı girin.' };
  if (a.kind === 'number' || a.kind === 'integer') {
    if (a.kind === 'integer' && !Number.isInteger(v)) return { error: 'Tamsayı olmalı.' };
    const lowOk = a.min === null || (a.min_inclusive ? v >= a.min : v > a.min);
    const highOk = a.max === null || (a.max_inclusive ? v <= a.max : v < a.max);
    if (!lowOk || !highOk) return { error: `Modelin tanım kümesi dışında (${describeAllowed(p, spec)}).` };
  }
  if (p.range && (v < p.range[0] - 1e-12 || v > p.range[1] + 1e-12)) return { outside: true };
  return {};
}

/* ------------------------------------------------------------------------------------------------ ilgililik */

export interface Relevance {
  /** false → bu ayarlarda modele girmiyor (soluk gösterilir) */
  active: boolean;
  note: string;
}

type Getter = (key: string) => ParamValue | undefined;

/**
 * Seçili moda/seçeneklere göre parametrenin modele girip girmediği (params.py effective_values ile aynı kurallar)
 * ya da değerinin nasıl kullanıldığına dair kısa not. null → özel bir not yok.
 */
export function relevance(key: string, get: Getter, ctx: { overrides: Record<string, unknown>; meta: Meta; gridKm: number }): Relevance | null {
  const mode = get('solver.objective_mode');
  const demandMode = get('demand.mode');
  switch (key) {
    case 'solver.budget_tl':
      return mode === 'butce' ? null : { active: false, note: 'Yalnız bütçe modunda kullanılır.' };
    case 'solver.p_fixed':
      return mode === 'sabit_p' ? null : { active: false, note: 'Yalnız “sabit p” modunda kullanılır.' };
    case 'size.min_hospitals':
    case 'size.max_hospitals':
      return mode === 'sabit_p' ? { active: false, note: 'Sabit p modunda hastane sayısını p belirler.' } : null;
    case 'demand.hb.admissions_per_1000':
    case 'demand.hb.alos_days':
    case 'demand.hb.target_occupancy':
      return demandMode === 'hill_burton' ? null : { active: false, note: 'Yalnız Hill-Burton talep yönteminde kullanılır (talep yöntemi: oran).' };
    case 'demand.beds_per_1000':
      return demandMode === 'hill_burton' ? { active: false, note: 'Hill-Burton yöntemi seçili; oran kullanılmaz.' } : null;
    case 'land.price_base_tl_m2':
    case 'land.price_beta':
    case 'land.m2_per_bed':
      return get('land.include') ? null : { active: false, note: 'Arsa maliyeti dahil değil (“Arsa maliyeti dahil mi?” kapalı).' };
    case 'opex.fixed_per_hospital_year':
    case 'opex.per_bed_year':
      return get('opex.include') ? null : { active: false, note: 'İşletme maliyeti dahil değil (F grubundaki anahtar kapalı).' };
    case 'cost.vat_rate':
      return get('cost.include_vat') === false ? { active: false, note: 'KDV dahil değil.' } : null;
    case 'size.city_min_beds':
    case 'size.city_max_beds':
      return get('size.city_enabled') === false ? { active: false, note: 'Şehir hastanesi sınıfı kapalı.' } : null;
    case 'access.circuity':
      return get('access.circuity_mode') === 'sabit' ? null : { active: false, note: 'Mesafeye bağlı profil seçili; sabit katsayı kullanılmaz.' };
    case 'access.circuity_profile':
      return get('access.circuity_mode') === 'sabit' ? { active: false, note: 'Sabit katsayı seçili; profil kullanılmaz.' } : null;
    case 'access.fuel_price_tl_per_l':
    case 'access.fuel_l_per_100km':
      return 'access.cost_tl_per_km' in ctx.overrides
        ? { active: false, note: 'c (TL/km) elle değiştirildiği için kullanılmaz.' }
        : { active: true, note: 'Değiştirilirse c = fiyat × tüketim / 100 olarak yeniden hesaplanır.' };
    case 'access.cost_tl_per_km': {
      const fuelChanged = 'access.fuel_price_tl_per_l' in ctx.overrides || 'access.fuel_l_per_100km' in ctx.overrides;
      if (fuelChanged && !('access.cost_tl_per_km' in ctx.overrides)) {
        const c = Number(get('access.fuel_price_tl_per_l')) * Number(get('access.fuel_l_per_100km')) / 100;
        return { active: true, note: `Yakıttan hesaplanıyor: c = ${fmtNumber(c, 2)} TL/km (bu kutudaki değer kullanılmaz).` };
      }
      return null;
    }
    case 'solver.time_limit_s': {
      if ('solver.time_limit_s' in ctx.overrides) return null;
      const rec = ctx.meta.defaults.recommended_time_limit_s[String(ctx.gridKm)];
      return rec ? { active: true, note: `Değiştirilmedikçe ızgaranın önerilen süresi kullanılır: ${fmtNumber(rec, 0)} sn (${ctx.gridKm} km).` } : null;
    }
    default:
      if (INFO_ONLY_KEYS.has(key)) return { active: false, note: infoOnlyNote(key) };
      return null;
  }
}

/* ------------------------------------------------------------------------------------------------ alternatifler */

/** Alternatifin kısa etiketi: ilk ':' / '—' / '(' öncesi, en çok 22 karakter. */
export function shortAltLabel(label: string): string {
  const cut = label.split(/[:—(;]/)[0].trim();
  const s = cut || label;
  return s.length > 22 ? `${s.slice(0, 21).trimEnd()}…` : s;
}

export function numericAlternatives(p: ParamMeta): (ParamAlternative & { value: number })[] {
  return p.alternatives.filter((a): a is ParamAlternative & { value: number } => typeof a.value === 'number' && Number.isFinite(a.value));
}

export function sameNumber(a: unknown, b: unknown): boolean {
  return typeof a === 'number' && typeof b === 'number' && Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(a), Math.abs(b));
}

/** Arama için Türkçe küçük harf + aksan sadeleştirme. */
export function normalizeTr(s: string): string {
  return s
    .toLocaleLowerCase('tr-TR')
    .replace(/ı/g, 'i')
    .replace(/ş/g, 's')
    .replace(/ğ/g, 'g')
    .replace(/ü/g, 'u')
    .replace(/ö/g, 'o')
    .replace(/ç/g, 'c')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '');
}
