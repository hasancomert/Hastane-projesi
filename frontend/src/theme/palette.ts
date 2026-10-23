/**
 * Renk sistemi — harita, lejantlar ve grafikler AYNI tabloları kullanır (renk varlığı izler, sırayı değil).
 *
 * Hastane sınıfı paleti (kategorik, 4 sınıf): renk körlüğüne güvenli; haritada tüm çiftler yan yana görünebildiği
 * için "tüm çiftler" ölçütüyle doğrulandı (dataviz validate_palette.js --pairs all, 2026-10-23):
 *   açık  #2a78d6 #1baf7a #eb6834 #9a3c8e → CVD ΔE en kötü 9,2 (≥ 8), normal görüş en kötü 21,0 (≥ 15)
 *   koyu  #3987e5 #199e70 #d95926 #b45aa8 → CVD ΔE en kötü 9,1, normal görüş en kötü 18,2, hepsi ≥ 3:1 kontrast
 * Açık temada yeşil (#1baf7a) yüzeye 3:1'in altında → işaretlerin yüzey renginde 1,5–2 px halkası olmalı ve sınıf
 * her zaman etiketle de verilmeli (lejant / kart). Sınıf rengi metne uygulanmaz; metin mürekkep tonlarını kullanır.
 */
import type { ClassCode, ParamStatus, SolveStatus } from '../api/types.ts';

export type Scheme = 'light' | 'dark';
export type Rgba = [number, number, number, number];

export const CLASS_ORDER: readonly ClassCode[] = ['IV-C', 'V-B', 'V-C', 'V-D'];

/** Kısa Türkçe sınıf adı (meta.classes[].name ile aynı; meta yüklenmeden de kullanılabilsin). */
export const CLASS_NAME_TR: Record<ClassCode, string> = {
  'IV-C': 'küçük',
  'V-B': 'orta',
  'V-C': 'büyük',
  'V-D': 'şehir hastanesi',
};

export const CLASS_COLORS: Record<Scheme, Record<ClassCode, string>> = {
  light: { 'IV-C': '#2a78d6', 'V-B': '#1baf7a', 'V-C': '#eb6834', 'V-D': '#9a3c8e' },
  dark: { 'IV-C': '#3987e5', 'V-B': '#199e70', 'V-C': '#d95926', 'V-D': '#b45aa8' },
};

export function classColor(code: ClassCode, scheme: Scheme): string {
  return CLASS_COLORS[scheme][code];
}

/** deck.gl için [r, g, b, a] (0–255). */
export function classColorRgba(code: ClassCode, scheme: Scheme, alpha = 255): Rgba {
  return hexToRgba(CLASS_COLORS[scheme][code], alpha);
}

/**
 * Maliyet kırılımı (yığılmış çubuk, hastane kartı, sonuç paneli) — kategorik, 8 parça, açık/koyu ayrı doğrulandı
 * (panel oluşturucu, dataviz doğrulayıcısı). Her çubuğun yanında değer tablosu var; renk tek başına anlam taşımaz.
 */
export const COST_COLORS: Record<Scheme, Record<'arsa' | 'yapi' | 'kdv' | 'altyapi' | 'donanim' | 'sabit' | 'isletme' | 'erisim', string>> = {
  light: {
    arsa: '#e34948',
    yapi: '#2a78d6',
    kdv: '#eb6834',
    altyapi: '#1baf7a',
    donanim: '#eda100',
    sabit: '#e87ba4',
    isletme: '#6250d6',
    erisim: '#008300',
  },
  dark: {
    arsa: '#e66767',
    yapi: '#3987e5',
    kdv: '#d95926',
    altyapi: '#199e70',
    donanim: '#c98500',
    sabit: '#d55181',
    isletme: '#9085e9',
    erisim: '#008300',
  },
};

/* ------------------------------------------------------------------------------------------------ yüzeyler, mürekkep */

export interface Chrome {
  surface: string;
  page: string;
  inkPrimary: string;
  inkSecondary: string;
  inkMuted: string;
  grid: string;
  axis: string;
  border: string;
  /** harita işaretlerinin halka rengi (yüzeyle aynı) */
  markRing: string;
}

export const CHROME: Record<Scheme, Chrome> = {
  light: {
    surface: '#fcfcfb',
    page: '#f6f7f8',
    inkPrimary: '#0b0b0b',
    inkSecondary: '#52514e',
    inkMuted: '#898781',
    grid: '#e1e0d9',
    axis: '#c3c2b7',
    border: 'rgba(11,11,11,0.10)',
    markRing: '#ffffff',
  },
  dark: {
    surface: '#1a1a19',
    page: '#121314',
    inkPrimary: '#f0efec',
    inkSecondary: '#c3c2b7',
    inkMuted: '#898781',
    grid: '#2c2c2a',
    axis: '#383835',
    border: 'rgba(255,255,255,0.10)',
    markRing: '#1a1a19',
  },
};

/* ------------------------------------------------------------------------------------------------ sıralı / ıraksak */

/**
 * Sıralı (büyüklük) rampası: tek ton, açıktan koyuya. Mavi = varsayılan (nüfus yoğunluğu gibi). Hastane sınıfı
 * IV-C de mavidir; nüfus katmanı hastaneyle birlikte çizilirken düşük opaklık ya da NEUTRAL rampa tercih edilmeli.
 */
export const SEQ_BLUE = ['#cde2fb', '#b7d3f6', '#9ec5f4', '#86b6ef', '#6da7ec', '#5598e7', '#3987e5', '#2a78d6', '#256abf', '#1c5cab', '#184f95', '#104281', '#0d366b'] as const;

/**
 * Harita hücre katmanlarının tek tonlu sıralı rampaları (açıktan koyuya, OKLCH L 0,96 → 0,31 tekdüze; tek ton,
 * gökkuşağı yok). Üç katman aynı anda gösterilmez ama geçişte birbirinden ve hastane sınıf renklerinden (turuncu
 * 41°, su yeşili 162°, mavi 256°, mor 333°) ayrılsın diye tonlar sınıf tonlarının ARASINDAN seçildi:
 *   nüfus yoğunluğu → menekşe (298°) · erişim mesafesi → kehribar (78°; uzak = koyu = kötü, R dışı ayrıca kırmızı
 *   durum rengi) · gölge fiyat → kızıl (14°; pahalı = koyu). Koyu temada rampa ters çevrilir (mapColors.ts).
 * Önceki tek mavi rampa (SEQ_BLUE) "küçük" sınıfın mavisiyle aynıydı: hastaneler zeminde kayboluyordu.
 */
export const SEQ_VIOLET = ['#f3efff', '#dbcffb', '#c3aff3', '#ac90e8', '#9671d9', '#7f53c7', '#6935b1', '#521d91', '#3d0076'] as const;
export const SEQ_AMBER = ['#fbf0e0', '#e9d3b2', '#d7b785', '#c49b59', '#af7f29', '#996500', '#824c00', '#673600', '#4f1f00'] as const;
export const SEQ_CRIMSON = ['#ffebed', '#fdc7ca', '#f4a2a9', '#e77f8a', '#d55b6c', '#c03651', '#a80038', '#870025', '#6b0010'] as const;

/** Nötr sıralı rampa (gri tonlar; arka plan büyüklükleri için). */
export const SEQ_NEUTRAL = ['#f0efec', '#e1e0d9', '#c3c2b7', '#a8a69c', '#898781', '#6d6b65', '#52514e', '#383835', '#1f1f1d'] as const;

/** Iraksak (işaretli fark): mavi ↔ gri orta ↔ kırmızı. */
export const DIVERGING = {
  negative: ['#104281', '#2a78d6', '#86b6ef'] as const,
  mid: { light: '#f0efec', dark: '#383835' } as const,
  positive: ['#f2a7a6', '#e34948', '#a52a2a'] as const,
};

/** Durum renkleri (yalnız durum için; seri rengi olarak kullanılmaz; her zaman simge + etiketle). */
export const STATUS_COLORS = {
  good: '#0ca30c',
  warning: '#fab219',
  serious: '#ec835a',
  critical: '#d03b3b',
} as const;
export type StatusTone = keyof typeof STATUS_COLORS | 'neutral';

/**
 * Durum tonlarının Mantine renk adları (rozet ve uyarı kutuları). Hex yerine tema rengi: Mantine'in açık varyantı
 * (açık zemin + 9. ton yazı; koyu temada koyu zemin + 0. ton yazı) her iki temada AA kontrastı verir. Hex renkle
 * açık varyant yazıyı rengin kendisiyle yazar (koyu temada yeşil #0ca30c 4,1:1 kalıyordu).
 */
export const STATUS_MANTINE: Record<StatusTone, string> = {
  good: 'teal',
  warning: 'yellow',
  serious: 'orange',
  critical: 'red',
  neutral: 'gray',
};

/** Çözüm durumunun tonu ve kısa Türkçe etiketi (rozetler için; uzun açıklama result.status_tr). */
export const SOLVE_STATUS_UI: Record<SolveStatus, { tone: StatusTone; label: string }> = {
  optimal: { tone: 'good', label: 'optimal (hedef fark içinde)' },
  sure_doldu: { tone: 'warning', label: 'süre doldu' },
  gap_ulasilamadi: { tone: 'warning', label: 'hedef farka ulaşılamadı' },
  durduruldu: { tone: 'serious', label: 'durduruldu' },
  sezgisel: { tone: 'serious', label: 'sezgisel çözüm' },
  olurlu_degil: { tone: 'critical', label: 'olurlu değil' },
  cozum_yok: { tone: 'critical', label: 'çözüm bulunamadı' },
  hata: { tone: 'critical', label: 'hata' },
};

/** Parametre durumu rozetleri (Mantine renk adları). */
export const PARAM_STATUS_UI: Record<ParamStatus, { color: string; label: string; hint: string }> = {
  kaynakli: { color: 'teal', label: 'kaynaklı', hint: 'Değer resmî ya da yayımlanmış bir kaynaktan alındı.' },
  hesap: { color: 'blue', label: 'hesap', hint: 'Değer kaynaklı sayılardan hesaplandı (türetme gösterilir).' },
  varsayim: { color: 'orange', label: 'varsayım', hint: 'Kaynağı yok; gerekçeli varsayım. Duyarlılıkta denenmeli.' },
  model_secimi: { color: 'grape', label: 'model seçimi', hint: 'Modelleme tercihi; gerekçesi gösterilir.' },
};

/* ------------------------------------------------------------------------------------------------ yardımcılar */

export function hexToRgba(hex: string, alpha = 255): Rgba {
  const h = hex.replace('#', '');
  const v = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16), alpha];
}

/**
 * Rampadan sürekli renk ölçeği. `log` → log10 ölçek (nüfus yoğunluğu, şartname 6.2). Değer alan dışındaysa uca
 * kırpılır; geçersiz değer → `nodata`.
 */
export function rampScale(ramp: readonly string[], domain: [number, number], opts: { log?: boolean; alpha?: number; nodata?: Rgba } = {}) {
  const cols = ramp.map((c) => hexToRgba(c, opts.alpha ?? 255));
  const t = (x: number) => (opts.log ? Math.log10(Math.max(x, 1e-9)) : x);
  const lo = t(domain[0]);
  const hi = t(domain[1]);
  const nodata = opts.nodata ?? ([0, 0, 0, 0] as Rgba);
  return (x: number | null | undefined): Rgba => {
    if (x === null || x === undefined || !Number.isFinite(x)) return nodata;
    const u = hi === lo ? 0 : Math.min(1, Math.max(0, (t(x) - lo) / (hi - lo)));
    const f = u * (cols.length - 1);
    const i = Math.min(cols.length - 2, Math.floor(f));
    const w = f - i;
    const a = cols[i];
    const b = cols[i + 1];
    return [a[0] + (b[0] - a[0]) * w, a[1] + (b[1] - a[1]) * w, a[2] + (b[2] - a[2]) * w, a[3] + (b[3] - a[3]) * w] as Rgba;
  };
}
