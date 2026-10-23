/**
 * Türkçe sayı biçimleri (şartname 2: "1.234.567,8"). Hepsi Intl.NumberFormat('tr-TR') üzerine kuruludur;
 * eksi işareti tipografik "−" (U+2212), yüzde işareti Türkçedeki gibi sayının önündedir ("%95,0").
 * Geçersiz değer (null, undefined, NaN, ±∞) için "—" döner — arayüz hiçbir zaman "NaN" göstermemeli.
 */

export const DASH = '—';
const MINUS = '−';

const cache = new Map<string, Intl.NumberFormat>();
function nf(opts: Intl.NumberFormatOptions): Intl.NumberFormat {
  const key = JSON.stringify(opts);
  let f = cache.get(key);
  if (!f) {
    f = new Intl.NumberFormat('tr-TR', opts);
    cache.set(key, f);
  }
  return f;
}

const ok = (x: number | null | undefined): x is number => typeof x === 'number' && Number.isFinite(x);
const minus = (s: string) => s.replace('-', MINUS);

/** Genel sayı: en çok `maxDigits` ondalık (varsayılan 1), sondaki sıfırlar atılır. 1234567.8 → "1.234.567,8". */
export function fmtNumber(x: number | null | undefined, maxDigits = 1, minDigits = 0): string {
  if (!ok(x)) return DASH;
  return minus(nf({ maximumFractionDigits: maxDigits, minimumFractionDigits: minDigits }).format(x));
}

/** Sabit ondalıklı sayı: fmtFixed(3.2048, 2) → "3,20". */
export function fmtFixed(x: number | null | undefined, digits = 1): string {
  return fmtNumber(x, digits, digits);
}

/** Tamsayı (yuvarlanır): 5910320 → "5.910.320". */
export function fmtInt(x: number | null | undefined): string {
  if (!ok(x)) return DASH;
  return minus(nf({ maximumFractionDigits: 0 }).format(Math.round(x)));
}

/** İşaretli sayı (farklar için): +1,5 / −1,5 / 0. */
export function fmtSigned(x: number | null | undefined, maxDigits = 1): string {
  if (!ok(x)) return DASH;
  return minus(nf({ maximumFractionDigits: maxDigits, signDisplay: 'exceptZero' }).format(x));
}

/* ------------------------------------------------------------------------------------------------ para */

export type MoneyUnit = 'TL' | 'bin TL' | 'milyon TL' | 'milyar TL' | 'trilyon TL';

export interface MoneyParts {
  /** biçimlenmiş sayı ("381,31") */
  value: string;
  unit: MoneyUnit;
  /** birim çarpanı (1e9 → milyar) */
  scale: number;
}

const MONEY_SCALES: [number, MoneyUnit][] = [
  [1e12, 'trilyon TL'],
  [1e9, 'milyar TL'],
  [1e6, 'milyon TL'],
];

/**
 * Para birimini büyüklüğe göre seçer: ≥ 1 milyar → "milyar TL", ≥ 1 milyon → "milyon TL", altı → "TL".
 * `digits` ondalık sayısı (varsayılan: milyar/trilyonda 2, milyonda 1, TL'de 0). `unit` ile birim sabitlenebilir
 * (aynı tablodaki sayılar aynı birimde olsun).
 */
export function moneyParts(tl: number, opts: { digits?: number; unit?: MoneyUnit } = {}): MoneyParts {
  let scale = 1;
  let unit: MoneyUnit = 'TL';
  if (opts.unit) {
    unit = opts.unit;
    scale = unit === 'trilyon TL' ? 1e12 : unit === 'milyar TL' ? 1e9 : unit === 'milyon TL' ? 1e6 : unit === 'bin TL' ? 1e3 : 1;
  } else {
    for (const [s, u] of MONEY_SCALES) {
      if (Math.abs(tl) >= s) {
        scale = s;
        unit = u;
        break;
      }
    }
  }
  const digits = opts.digits ?? (scale >= 1e9 ? 2 : scale >= 1e6 ? 1 : 0);
  return { value: fmtNumber(tl / scale, digits), unit, scale };
}

/** 381314289391.98 → "381,31 milyar TL"; 250000000 → "250 milyon TL"; 48750 → "48.750 TL". */
export function fmtMoney(tl: number | null | undefined, opts: { digits?: number; unit?: MoneyUnit } = {}): string {
  if (!ok(tl)) return DASH;
  const p = moneyParts(tl, opts);
  return `${p.value} ${p.unit}`;
}

/** İşaretli para farkı: "+8,02 milyar TL" / "−20 milyon TL". */
export function fmtMoneyDelta(tl: number | null | undefined, opts: { digits?: number; unit?: MoneyUnit } = {}): string {
  if (!ok(tl)) return DASH;
  const p = moneyParts(Math.abs(tl), opts);
  const sign = tl > 0 ? '+' : tl < 0 ? MINUS : '';
  return `${sign}${p.value} ${p.unit}`;
}

/** Her zaman milyar TL: fmtBillion(381314289391.98) → "381,31 milyar TL". */
export function fmtBillion(tl: number | null | undefined, digits = 2): string {
  return fmtMoney(tl, { unit: 'milyar TL', digits });
}

/** Kişi başı ya da birim maliyet gibi TL değerleri, tam sayı: "64.517 TL". */
export function fmtTl(tl: number | null | undefined, digits = 0): string {
  if (!ok(tl)) return DASH;
  return `${fmtNumber(tl, digits)} TL`;
}

/* ------------------------------------------------------------------------------------------------ yüzde */

/** Pay (0–1) → yüzde: 0.95 → "%95,0". */
export function fmtPercent(frac: number | null | undefined, digits = 1): string {
  if (!ok(frac)) return DASH;
  return minus(nf({ style: 'percent', minimumFractionDigits: digits, maximumFractionDigits: digits }).format(frac));
}

/** Pay (0–1) → yüzde, sondaki sıfırlar atılır: 0.5 → "%50", 0.125 → "%12,5". */
export function fmtPercentShort(frac: number | null | undefined, maxDigits = 1): string {
  if (!ok(frac)) return DASH;
  return minus(nf({ style: 'percent', maximumFractionDigits: maxDigits }).format(frac));
}

/**
 * Optimallik farkı (gap, 0–1): 0 → "%0", < 0,0001 → "< %0,01", diğerleri 2 ondalık ("%0,06").
 * null → "—" (alt sınır yok).
 */
export function fmtGap(gap: number | null | undefined): string {
  if (!ok(gap)) return DASH;
  if (gap <= 0) return '%0';
  if (gap < 0.0001) return '< %0,01';
  return fmtPercent(gap, 2);
}

/* ------------------------------------------------------------------------------------------------ mesafe, alan, kişi, yatak */

/** km: 3.2048 → "3,2 km". */
export function fmtKm(km: number | null | undefined, digits = 1): string {
  if (!ok(km)) return DASH;
  return `${fmtNumber(km, digits)} km`;
}

/** km²: 25647.883 → "25.647,9 km²". */
export function fmtKm2(km2: number | null | undefined, digits = 1): string {
  if (!ok(km2)) return DASH;
  return `${fmtNumber(km2, digits)} km²`;
}

/** Kişi: 16856.98 → "16.857 kişi". */
export function fmtPeople(n: number | null | undefined): string {
  if (!ok(n)) return DASH;
  return `${fmtInt(n)} kişi`;
}

/** Yatak (model yatak sayısını sürekli tutar; gösterimde tamsayı): 53.0995 → "53 yatak". */
export function fmtBeds(n: number | null | undefined, digits = 0): string {
  if (!ok(n)) return DASH;
  return `${digits ? fmtNumber(n, digits) : fmtInt(n)} yatak`;
}

/** Büyük sayıların kısa yazımı (eksen etiketleri): 5910320 → "5,9 milyon", 18617 → "18,6 bin". */
export function fmtCompact(x: number | null | undefined, digits = 1): string {
  if (!ok(x)) return DASH;
  const a = Math.abs(x);
  if (a >= 1e12) return `${fmtNumber(x / 1e12, digits)} trilyon`;
  if (a >= 1e9) return `${fmtNumber(x / 1e9, digits)} milyar`;
  if (a >= 1e6) return `${fmtNumber(x / 1e6, digits)} milyon`;
  if (a >= 1e4) return `${fmtNumber(x / 1e3, digits)} bin`;
  return fmtNumber(x, digits);
}

/* ------------------------------------------------------------------------------------------------ süre */

/** Saniye: 4.143 → "4,1 sn"; 125 → "2 dk 5 sn"; 3600 → "1 sa 0 dk". */
export function fmtDuration(s: number | null | undefined): string {
  if (!ok(s)) return DASH;
  if (s < 10) return `${fmtNumber(s, 1)} sn`;
  if (s < 60) return `${fmtInt(s)} sn`;
  if (s < 3600) {
    const m = Math.floor(s / 60);
    return `${m} dk ${Math.round(s - m * 60)} sn`;
  }
  const h = Math.floor(s / 3600);
  return `${h} sa ${Math.round((s - h * 3600) / 60)} dk`;
}

/* ------------------------------------------------------------------------------------------------ parametre değeri */

/**
 * Parametre değerinin kısa gösterimi (panel, tablo, tooltip): evet/hayır, seçenekler olduğu gibi, sayılar birime
 * göre (TL içeren birimlerde para ölçeği, "oran" birimlerinde yüzde). Kaydırıcı yanındaki sayı kutusu ham değeri
 * gösterir; bu fonksiyon okunabilir özet içindir.
 */
export function fmtParamValue(value: unknown, unit = ''): string {
  if (value === null || value === undefined) return DASH;
  if (typeof value === 'boolean') return value ? 'evet' : 'hayır';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return `${value.length} nokta`;
  if (typeof value !== 'number' || !Number.isFinite(value)) return DASH;
  const u = unit.toLowerCase();
  if (u.startsWith('yıl') && Number.isInteger(value) && value >= 1900 && value <= 2100) return String(value);
  if (u.startsWith('tl') && Math.abs(value) >= 1e6) return fmtMoney(value);
  if (/(^|\s)oran(\s|$)/.test(u) && !u.includes('boyutsuz')) return fmtPercentShort(value, 2);
  return fmtNumber(value, Math.abs(value) >= 100 ? 0 : Math.abs(value) >= 1 ? 2 : 4);
}

/* ------------------------------------------------------------------------------------------------ tarih */

/** ISO tarih → "21.10.2026". */
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('tr-TR', { day: '2-digit', month: '2-digit', year: 'numeric' });
}
