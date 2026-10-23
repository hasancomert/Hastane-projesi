/**
 * Harita PNG dışa aktarımı (şartname 6.5 "harita PNG"; Faz 7 dışa aktarım düğmeleri de bunu kullanır).
 *
 * html-to-image harita kapsayıcısını (MapLibre tuvali + lejant gibi HTML katmanları) tek resme çevirir. Tuval
 * `preserveDrawingBuffer: true` ile kurulduğu için son kare okunabilir (deck.gl katmanları aynı tuvalde,
 * interleaved). Araç çubuğu, katman paneli, ipucu ve menü `data-export="skip"` ile dışarıda kalır. Yazı tipleri
 * yerel dosyalardan gömülür — hiçbir istek 127.0.0.1 dışına çıkmaz.
 */
import { toPng } from 'html-to-image';

export interface ExportOptions {
  /** varsayılan 2 (projeksiyon ve rapor için keskin) */
  pixelRatio?: number;
  /** lejantı dahil et (varsayılan true) */
  legend?: boolean;
  /** arka plan (tuval saydam alanları için); varsayılan sayfa rengi */
  backgroundColor?: string;
  /** haritanın üstüne ayrı şerit olarak yazılan başlık ve özet (senaryo, ızgara, KPI'lar) — PNG tek başına anlaşılsın */
  caption?: { title: string; lines: string[] } | null;
}

/**
 * Başlık şeridi: haritanın ÜSTÜNE ayrı bir şerit olarak çizilir (haritanın kuzeyini örtmez). Yazı tipi sayfanınki
 * (Inter, yerel); satırlar resim genişliğine göre kaydırılır.
 */
async function withCaption(png: string, caption: NonNullable<ExportOptions['caption']>, ratio: number, bg: string): Promise<string> {
  const img = new Image();
  img.src = png;
  await img.decode();
  const cs = getComputedStyle(document.body);
  const ink = cs.color || '#0b0b0b';
  const font = cs.fontFamily || 'sans-serif';
  const pad = 14 * ratio;
  const titlePx = 17 * ratio;
  const linePx = 14 * ratio;
  const measure = document.createElement('canvas').getContext('2d')!;
  const wrap = (text: string, px: number, weight: number): string[] => {
    measure.font = `${weight} ${px}px ${font}`;
    const out: string[] = [];
    let cur = '';
    for (const w of text.split(/\s+/)) {
      const next = cur ? `${cur} ${w}` : w;
      if (cur && measure.measureText(next).width > img.width - 2 * pad) {
        out.push(cur);
        cur = w;
      } else cur = next;
    }
    if (cur) out.push(cur);
    return out;
  };
  const rows: { text: string; px: number; weight: number }[] = [
    ...wrap(caption.title, titlePx, 700).map((t) => ({ text: t, px: titlePx, weight: 700 })),
    ...caption.lines.flatMap((l) => wrap(l, linePx, 400).map((t) => ({ text: t, px: linePx, weight: 400 }))),
  ];
  const lh = 1.38;
  const bandH = Math.ceil(pad * 2 + rows.reduce((h, r) => h + r.px * lh, 0));
  const canvas = document.createElement('canvas');
  canvas.width = img.width;
  canvas.height = img.height + bandH;
  const ctx = canvas.getContext('2d')!;
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = ink;
  ctx.textBaseline = 'top';
  let y = pad;
  for (const r of rows) {
    ctx.font = `${r.weight} ${r.px}px ${font}`;
    ctx.fillText(r.text, pad, y + (r.px * (lh - 1)) / 2);
    y += r.px * lh;
  }
  ctx.drawImage(img, 0, bandH);
  return canvas.toDataURL('image/png');
}

export async function exportElementPng(el: HTMLElement, opts: ExportOptions = {}): Promise<string> {
  const legend = opts.legend ?? true;
  const bg = opts.backgroundColor ?? getComputedStyle(document.body).backgroundColor ?? '#ffffff';
  const ratio = opts.pixelRatio ?? 2;
  // yakınlaştırma düğmeleri gizlenir ama yerleri korunur: ölçek çubuğu ve atıf ekrandaki yerinde kalsın
  // (süzgeçle çıkarılınca sağ alt denetim kutusu küçülüp ölçek çubuğu haritanın ortasına kayıyordu)
  const hidden = [...el.querySelectorAll<HTMLElement>('.maplibregl-ctrl-group')];
  const prev = hidden.map((h) => h.style.visibility);
  hidden.forEach((h) => (h.style.visibility = 'hidden'));
  let png: string;
  try {
    png = await render(el, bg, legend, ratio);
  } finally {
    hidden.forEach((h, i) => (h.style.visibility = prev[i]));
  }
  return opts.caption ? withCaption(png, opts.caption, ratio, bg) : png;
}

function render(el: HTMLElement, bg: string, legend: boolean, ratio: number): Promise<string> {
  return toPng(el, {
    pixelRatio: ratio,
    backgroundColor: bg,
    cacheBust: false,
    filter: (node: HTMLElement) => {
      if (!(node instanceof HTMLElement)) return true;
      if (node.dataset?.export === 'skip') return false;
      if (!legend && node.classList?.contains('map-legend')) return false;
      return true;
    },
  });
}

/** data: URL'yi dosya olarak indir. */
export function downloadDataUrl(dataUrl: string, filename: string): void {
  const a = document.createElement('a');
  a.href = dataUrl;
  a.download = filename;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** Dosya adı: ankara-hastane-haritasi-3km-2026-10-23-1530.png */
export function mapPngFilename(gridKm: number): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `ankara-hastane-haritasi-${gridKm}km-${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}.png`;
}
