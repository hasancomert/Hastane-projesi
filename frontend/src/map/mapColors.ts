/**
 * Harita katmanlarının renk ölçekleri ve lejant verisi. Tüm renkler theme/palette.ts'ten gelir (harita, lejant ve
 * grafikler aynı tabloları kullanır); burada yalnız hangi büyüklüğün hangi rampaya, hangi alana ve hangi tik
 * değerlerine bağlandığı tanımlanır.
 *
 * Kurallar (dataviz yöntemi):
 *   - büyüklük = tek tonlu sıralı rampa, katman başına AYRI ton (palette.ts): nüfus yoğunluğu menekşe, erişim
 *     mesafesi kehribar, gölge fiyat kızıl — hiçbiri hastane sınıf renklerinden biri değil (önceden üçü de "küçük"
 *     sınıfın mavisiydi). Koyu temada rampa ters çevrilir: küçük değer koyu yüzeye yakın, büyük değer açık;
 *   - bu üç hücre katmanı aynı anda gösterilmez (lejantta tek seçimli "Hücre katmanı" grubu);
 *   - "R dışı" bir DURUMDUR → STATUS_COLORS.critical, lejantta simge + etiketle;
 *   - kilitler de durumdur: zorunlu = good (+ işareti), yasak = critical (× işareti).
 */
import { CHROME, SEQ_AMBER, SEQ_CRIMSON, SEQ_VIOLET, STATUS_COLORS, hexToRgba, rampScale, type Rgba, type Scheme } from '../theme/palette.ts';
import { fmtInt, fmtNumber } from '../format/number.ts';

export interface LegendTick {
  /** 0–1 konum (rampa çubuğu üzerinde) */
  at: number;
  label: string;
}

export interface SequentialScale {
  color: (x: number | null | undefined) => Rgba;
  /** CSS linear-gradient (lejant çubuğu) */
  gradient: string;
  ticks: LegendTick[];
  domain: [number, number];
  log: boolean;
}

type RampName = 'density' | 'access' | 'shadow';
const RAMPS: Record<RampName, readonly string[]> = { density: SEQ_VIOLET, access: SEQ_AMBER, shadow: SEQ_CRIMSON };

function rampFor(scheme: Scheme, name: RampName): readonly string[] {
  const r = RAMPS[name];
  return scheme === 'dark' ? [...r].reverse() : r;
}

function gradientCss(ramp: readonly string[]): string {
  const n = ramp.length - 1;
  return `linear-gradient(to right, ${ramp.map((c, i) => `${c} ${((i / n) * 100).toFixed(1)}%`).join(', ')})`;
}

function makeScale(scheme: Scheme, name: RampName, domain: [number, number], log: boolean, alpha: number, tickValues: number[], fmt: (x: number) => string): SequentialScale {
  const ramp = rampFor(scheme, name);
  const t = (x: number) => (log ? Math.log10(Math.max(x, 1e-9)) : x);
  const lo = t(domain[0]);
  const hi = t(domain[1]);
  const at = (x: number) => (hi === lo ? 0 : Math.min(1, Math.max(0, (t(x) - lo) / (hi - lo))));
  return {
    color: rampScale(ramp, domain, { log, alpha }),
    gradient: gradientCss(ramp),
    ticks: tickValues.filter((v) => v >= domain[0] - 1e-9 && v <= domain[1] + 1e-9).map((v) => ({ at: at(v), label: fmt(v) })),
    domain,
    log,
  };
}

/* ------------------------------------------------------------------------------------------------ nüfus yoğunluğu */

/** kişi/km², log ölçek. Alan iki ızgarada da aynı (2 km en yüksek ≈30.092; 3 km ≈28.151) → lejant karşılaştırılabilir. */
export const DENSITY_DOMAIN: [number, number] = [1, 30000];

const densityCache = new Map<string, SequentialScale>();
export function densityScale(scheme: Scheme): SequentialScale {
  const k = scheme;
  let s = densityCache.get(k);
  if (!s) {
    s = makeScale(scheme, 'density', DENSITY_DOMAIN, true, scheme === 'dark' ? 215 : 200, [1, 10, 100, 1000, 10000], (v) => fmtInt(v));
    densityCache.set(k, s);
  }
  return s;
}

/* ------------------------------------------------------------------------------------------------ erişim */

/** En yakın açık hastaneye yol km'si: 0 … R doğrusal; R'nin ötesi durum rengi (critical). */
export function accessScale(scheme: Scheme, R_km: number): SequentialScale {
  const R = Number.isFinite(R_km) && R_km > 0 ? R_km : 30;
  const step = R <= 20 ? 5 : R <= 60 ? 10 : 20;
  const ticks: number[] = [];
  for (let v = 0; v <= R + 1e-9; v += step) ticks.push(v);
  if (Math.abs(ticks[ticks.length - 1] - R) > 1e-6) ticks.push(R);
  return makeScale(scheme, 'access', [0, R], false, scheme === 'dark' ? 205 : 190, ticks, (v) => fmtNumber(v, 0));
}

export const BEYOND_R_HEX = STATUS_COLORS.critical;
export const beyondRColor = (alpha = 215): Rgba => hexToRgba(STATUS_COLORS.critical, alpha);

/* ------------------------------------------------------------------------------------------------ gölge fiyat */

/** "Güzel" tik adımı (1, 2, 2,5, 5 × 10^k). */
function niceStep(span: number, target = 4): number {
  const raw = span / Math.max(1, target);
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const m = raw / p;
  const nice = m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10;
  return nice * p;
}

/** Değerlerin [q, 1−q] yüzdelikleri (uç değerler rampayı ezmesin). */
export function robustDomain(values: ArrayLike<number>, q = 0.02): [number, number] {
  const v: number[] = [];
  for (let i = 0; i < values.length; i++) {
    const x = values[i];
    if (Number.isFinite(x)) v.push(x);
  }
  if (!v.length) return [0, 1];
  v.sort((a, b) => a - b);
  const lo = v[Math.floor(q * (v.length - 1))];
  const hi = v[Math.ceil((1 - q) * (v.length - 1))];
  return hi > lo ? [lo, hi] : [v[0], v[v.length - 1] > v[0] ? v[v.length - 1] : v[0] + 1];
}

/** Marjinal hizmet maliyeti (TL/kişi) — lejant "bin TL/kişi". Alan çözümden (yüzdelik), tikler güzel sayılar. */
export function shadowScale(scheme: Scheme, domain: [number, number]): SequentialScale {
  const step = niceStep(domain[1] - domain[0], 4);
  const ticks: number[] = [];
  for (let v = Math.ceil(domain[0] / step) * step; v <= domain[1] + 1e-9; v += step) ticks.push(v);
  return makeScale(scheme, 'shadow', domain, false, scheme === 'dark' ? 215 : 200, ticks, (v) => fmtNumber(v / 1000, step >= 1000 ? 0 : 1));
}

/* ------------------------------------------------------------------------------------------------ kilitler, seçim */

export const LOCK_OPEN_HEX = STATUS_COLORS.good;
export const LOCK_CLOSED_HEX = STATUS_COLORS.critical;

/** Mürekkep tonları deck.gl için (seçim çerçevesi, halkalar, çizgiler). */
export function ink(scheme: Scheme, which: 'primary' | 'secondary' | 'muted', alpha = 255): Rgba {
  const c = CHROME[scheme];
  return hexToRgba(which === 'primary' ? c.inkPrimary : which === 'secondary' ? c.inkSecondary : c.inkMuted, alpha);
}

/** Harita işaretlerinin yüzey rengindeki halkası (açık temada beyaz, koyuda koyu yüzey). */
export function surfaceRing(scheme: Scheme, alpha = 255): Rgba {
  return hexToRgba(CHROME[scheme].markRing, alpha);
}
