/**
 * "Sayfada nerede?" atlamaları — Hocanın Listesi, sunum turu ve açıklama paneli AYNI atlamaları kullanır.
 *
 *   import { jumpTo } from '../info/jumps.ts';
 *   jumpTo('olculer_harita');                       // ya da içe aktarmadan:
 *   window.dispatchEvent(new CustomEvent('hastane:jump', { detail: { id: 'olculer_harita' } }));
 *
 * Bir atlama sırayla: (1) harita katmanlarını ayarlar (hücre rengi tek seçimli grup + bağımsız katmanlar, ölçü
 * çizgileri `layers.measures`; görünüm belirleyen atlamada listede olmayan aday/akış/halka/3B/ölçü katmanları kapanır), (2) bilgi/grafik sekmesini ve Veri alt bölümünü seçer, (3) hedefe kaydırır —
 * harita (sayfanın başı, gerekirse Ankara'ya sığdırma), sonuç panelindeki bir kart (`data-testid`; dar ekranda sonuç
 * çekmecesi açılır), grafik sekmeleri ya da bilgi sekmesindeki bir çapa (`id`; sekme tembel yüklenirken beklenir) —
 * ve (4) hedefi kısa bir halkayla vurgular. Bitince `hastane:jumped` olayı yayınlanır ({ id }).
 */
import { CELL_LAYERS, setCellLayer, type CellLayer } from '../map/MapLegend.tsx';
import { getMapApi } from '../map/mapApi.ts';
import { useApp, type ChartTabId, type InfoTabId, type LayerId } from '../store/appStore.ts';
import { useInfo, type VeriSection } from './infoStore.ts';

export type JumpTargetKind = 'map' | 'result' | 'charts' | 'info';

export interface JumpSpec {
  /** düğme metni */
  label: string;
  /** düğmenin ipucu: nereye gider, ne açılır */
  hint: string;
  target: JumpTargetKind;
  /** hücre rengi grubu (tek seçim) */
  cell?: CellLayer | 'none';
  /** açılacak / kapatılacak bağımsız katmanlar */
  on?: LayerId[];
  off?: LayerId[];
  /** haritayı Ankara'ya sığdır */
  fit?: boolean;
  infoTab?: InfoTabId;
  veriSection?: VeriSection;
  /** bilgi sekmesindeki çapa (element id) */
  anchor?: string;
  chartTab?: ChartTabId;
  /** sonuç panelindeki hedef (data-testid) */
  testId?: string;
}

export const JUMPS = {
  nufus_karti: { label: 'Nüfus kartı', hint: 'Veri → Nüfus 2026: TÜİK toplamı, kalibrasyon ve ilçe tablosu', target: 'info', infoTab: 'veri', veriSection: 'nufus', anchor: 'veri-nufus' },
  nufus_harita: { label: 'Haritada nüfus', hint: 'Harita: nüfus yoğunluğu katmanı (log ölçek)', target: 'map', cell: 'population', on: ['boundaries'], fit: true },
  harita: { label: 'Haritaya git', hint: 'Harita: il ve ilçe sınırları, hastaneler; Ankara’ya sığdırılır', target: 'map', on: ['boundaries', 'hospitals'], fit: true },
  arsa: { label: 'Arsa × hücre', hint: 'Veri → Ankara ölçüleri: hastane arsası ve inşaat alanı ile hücre alanının karşılaştırması', target: 'info', infoTab: 'veri', veriSection: 'olculer', anchor: 'olcu-arazi' },
  hizmet: { label: 'Hizmet bölgeleri', hint: 'Harita: her hücre hizmet aldığı hastanenin renginde', target: 'map', cell: 'serviceAreas', on: ['hospitals', 'boundaries'], fit: true },
  olculer: { label: 'Ölçüler kartı', hint: 'Veri → Ankara ölçüleri: alan, çevre, genişlikler, en uzak iki nokta, kaynak karşılaştırması', target: 'info', infoTab: 'veri', veriSection: 'olculer', anchor: 'veri-olculer' },
  olculer_harita: { label: 'Haritada çiz', hint: 'Harita: en uzak iki sınır noktası, Kızılay → en uzak nokta, doğu–batı ve kuzey–güney uç noktaları', target: 'map', cell: 'none', on: ['measures', 'boundaries'], fit: true },
  aday: { label: 'Aday hücreler ve |J|', hint: 'Harita: aday hücreler; sonuç panelinde “Kaç hastane? En az / en fazla” kartı', target: 'result', on: ['candidates', 'boundaries'], testId: 'bounds-block', fit: true },
  sinir_karti: { label: 'En az / en fazla', hint: 'Sonuç panelinde “Kaç hastane? En az / en fazla” kartı', target: 'result', testId: 'bounds-block' },
  mevcut: { label: 'Modelde', hint: 'Model → Kümeler: aday kümesi J yalnız ızgaradan türetilir', target: 'info', infoTab: 'model', anchor: 'model-kumeler' },
  yogunluk: { label: 'Yoğunluk + hastaneler', hint: 'Harita: nüfus yoğunluğu ve hastaneler birlikte', target: 'map', cell: 'population', on: ['hospitals', 'boundaries'], fit: true },
  yogunluk_model: { label: 'Amaçta nüfus ağırlığı', hint: 'Model → Amaç: erişim maliyeti nüfusla ağırlıklı', target: 'info', infoTab: 'model', anchor: 'model-amac' },
  kac_nerede: { label: 'Sonuç', hint: 'Harita: hastaneler ve hizmet bölgeleri; sonuç panelinin özeti', target: 'result', cell: 'serviceAreas', on: ['hospitals', 'boundaries'], testId: 'result-summary', fit: true },
  u_egrisi: { label: 'U eğrisi', hint: 'Grafikler → U eğrisi: hastane sayısı taraması', target: 'charts', chartTab: 'u_egrisi' },
  sertifika: { label: 'Sertifika', hint: 'Sonuç panelinde optimallik sertifikası (alt sınır, bulunan çözüm, fark)', target: 'result', testId: 'certificate' },
  maliyet_grafik: { label: 'Maliyet kırılımı', hint: 'Grafikler → Maliyet kırılımı', target: 'charts', chartTab: 'maliyet' },
  tornado: { label: 'Tornado', hint: 'Grafikler → Tornado: parametre ±%20 duyarlılığı', target: 'charts', chartTab: 'tornado' },
  dal_sinir: { label: 'Dal-sınır grafiği', hint: 'Grafikler → Dal-sınır: çözüm sırasında alt ve üst sınır', target: 'charts', chartTab: 'dal_sinir' },
  erisim_harita: { label: 'Erişim haritası', hint: 'Harita: en yakın hastaneye yol mesafesi; R dışı vurgulu', target: 'map', cell: 'access', on: ['hospitals', 'boundaries'], fit: true },
  model_maliyet: { label: 'Maliyet formülü', hint: 'Model → Maliyet katsayıları: Sabit_k, Değişken_jk, λ, PV', target: 'info', infoTab: 'model', anchor: 'model-maliyet' },
  model_neden: { label: 'Neden çok küçük hastane?', hint: 'Model → Modelin yapısı sonucu nasıl şekillendiriyor', target: 'info', infoTab: 'model', anchor: 'model-neden' },
  model_alt_sinir: { label: 'Alt sınır nereden?', hint: 'Model → Lagrange gevşetmesi ve HiGHS: L ≤ optimum ≤ U', target: 'info', infoTab: 'model', anchor: 'model-alt-sinir' },
  parametreler: { label: 'Parametre tablosu', hint: 'Veri → Parametreler: değer, birim, durum, kaynak', target: 'info', infoTab: 'veri', veriSection: 'parametreler', anchor: 'veri-parametreler' },
  sinirliliklar: { label: 'Sınırlılıklar', hint: 'Sınırlılıklar sekmesi', target: 'info', infoTab: 'sinirliliklar', anchor: 'info-sinirliliklar' },
  sinir_duz_dip: { label: 'Düz dip', hint: 'Sınırlılıklar → U eğrisinin düz dibi', target: 'info', infoTab: 'sinirliliklar', anchor: 'sinir-duz-dip' },
  kaynakca: { label: 'Kaynakça', hint: 'Kaynakça sekmesi', target: 'info', infoTab: 'kaynakca', anchor: 'info-kaynakca' },
} as const satisfies Record<string, JumpSpec>;

export type JumpId = keyof typeof JUMPS;

/** Hocanın 9 maddesi (şartname 1) ve her birinin sayfadaki yeri. Metinleri (canlı sayılarla) facts.ts üretir. */
export const HOCA_ITEMS: readonly { n: number; title: string; jumps: readonly JumpId[] }[] = [
  { n: 1, title: 'Ankara nüfusu 2026', jumps: ['nufus_karti', 'nufus_harita'] },
  { n: 2, title: 'Ankara haritasını çiz', jumps: ['harita'] },
  { n: 3, title: 'Hastane alanlarına bak: (a) inşaat/arsa alanı, (b) hizmet bölgesi', jumps: ['arsa', 'hizmet'] },
  { n: 4, title: 'Ankara’nın toplam mesafesi', jumps: ['olculer', 'olculer_harita'] },
  { n: 5, title: 'Izgaraya kaç hastane yerleşebilir', jumps: ['aday'] },
  { n: 6, title: 'Mevcut hastaneleri yok say (sıfırdan planlama)', jumps: ['mevcut'] },
  { n: 7, title: 'Nüfus yoğunluğu önemli', jumps: ['yogunluk', 'yogunluk_model'] },
  { n: 8, title: 'Kaç hastane olmalı ve nerede olmalı', jumps: ['kac_nerede', 'u_egrisi', 'model_neden'] },
  { n: 9, title: 'Hastanelerin açılış maliyeti var', jumps: ['maliyet_grafik', 'model_maliyet'] },
];

/** Harita atlamalarının, listelenmedikçe kapattığı isteğe bağlı üst katmanlar. */
const OPTIONAL_OVERLAYS: readonly LayerId[] = ['candidates', 'flows', 'rings', 'population3d', 'measures'];

/* ------------------------------------------------------------------------------------------------ kaydırma + vurgu */

const FLASH_CLASS = 'info-flash';

/** Öğeyi kısa bir halkayla vurgula (info.css `.info-flash`). */
export function flash(el: Element | null | undefined, ms = 2200): void {
  if (!el) return;
  el.classList.remove(FLASH_CLASS);
  // animasyonu yeniden başlat
  void (el as HTMLElement).offsetWidth;
  el.classList.add(FLASH_CLASS);
  window.setTimeout(() => el.classList.remove(FLASH_CLASS), ms);
}

function headerOffset(): number {
  const h = document.querySelector<HTMLElement>('.app-header');
  return (h?.offsetHeight ?? 0) + 10;
}

/** Sayfa (pencere) kaydırması: yapışkan başlığın altına. */
export function scrollPageTo(el: Element, block: 'start' | 'center' = 'start', behavior: ScrollBehavior = 'smooth'): void {
  const r = el.getBoundingClientRect();
  const top =
    block === 'start' ? window.scrollY + r.top - headerOffset() : window.scrollY + r.top - Math.max(headerOffset(), (window.innerHeight - r.height) / 2);
  window.scrollTo({ top: Math.max(0, top), behavior });
}

const sleep = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms));

/** Kaydırma kuşağı: yeni bir atlama/kaydırma başlayınca öncekinin konum bekçisi durur. */
let scrollGen = 0;

/** Pencere kaydırması durana dek bekle (art arda üç yoklamada aynı konum) ya da en çok `maxMs`. */
async function waitScrollEnd(maxMs = 1600): Promise<void> {
  const t0 = performance.now();
  let last = window.scrollY;
  let same = 0;
  while (performance.now() - t0 < maxMs) {
    await sleep(60);
    const y = window.scrollY;
    same = Math.abs(y - last) < 0.5 ? same + 1 : 0;
    last = y;
    if (same >= 3) return;
  }
}

/**
 * Öğeyi başlığın altına getir ve orada kaldığını doğrula: yazı tipleri (KaTeX) ya da tembel içerik yüklenince düzen
 * kayabilir — hedef yerinde değilse en çok iki kez daha kaydırılır (sayfanın sonundaysa daha fazla kaydırılamaz).
 */
export async function settleScroll(el: Element, block: 'start' | 'center' = 'start'): Promise<void> {
  const gen = ++scrollGen;
  try {
    await Promise.race([document.fonts?.ready, sleep(1500)]);
  } catch {
    /* yazı tipi API'si yok */
  }
  const inPlace = () =>
    block === 'center' ||
    Math.abs(el.getBoundingClientRect().top - headerOffset()) < 24 ||
    window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2;
  for (let i = 0; i < 3; i++) {
    if (gen !== scrollGen) return;
    scrollPageTo(el, block, i === 0 ? 'smooth' : 'auto');
    await waitScrollEnd();
    if (inPlace()) break;
  }
  // yukarıdaki içerik (tembel grafikler, yazı tipleri) sonradan boy değiştirirse kısa bir süre izle ve yerine geri getir;
  // kullanıcı bu arada kendisi kaydırırsa (tekerlek, dokunma, tuş, tıklama) bırak
  const stop = userInputWatch();
  try {
    for (let t = 0; t < 14; t++) {
      await sleep(250);
      if (stop.touched || !el.isConnected || gen !== scrollGen) return;
      if (!inPlace()) scrollPageTo(el, block, 'auto');
    }
  } finally {
    stop.dispose();
  }
}

/** Kullanıcı girdisi izleyici: kaydırma düzeltmeleri kullanıcıyla yarışmasın. */
function userInputWatch(): { touched: boolean; dispose(): void } {
  const w = { touched: false, dispose: () => undefined as void };
  const on = () => {
    w.touched = true;
  };
  const evs = ['wheel', 'touchstart', 'keydown', 'mousedown'] as const;
  for (const e of evs) window.addEventListener(e, on, { passive: true, capture: true });
  w.dispose = () => {
    for (const e of evs) window.removeEventListener(e, on, { capture: true });
  };
  return w;
}

/** Sayfanın başına (harita ve paneller) kaydır; yumuşak kaydırma bitmezse anında. */
async function scrollToTop(): Promise<void> {
  ++scrollGen;
  if (window.scrollY < 2) return;
  window.scrollTo({ top: 0, behavior: 'smooth' });
  await waitScrollEnd();
  if (window.scrollY > 2) window.scrollTo({ top: 0 });
}

function scrollParent(el: HTMLElement): HTMLElement | null {
  let p: HTMLElement | null = el.parentElement;
  while (p && p !== document.body) {
    const cs = getComputedStyle(p);
    if (/(auto|scroll)/.test(cs.overflowY) && p.scrollHeight > p.clientHeight + 4) return p;
    p = p.parentElement;
  }
  return null;
}

/**
 * En yakın kaydırılabilir ata (panelin kaydırma alanı) içinde öğeyi görünür yap; pencereyi kaydırmaz. Panelin içeriği
 * (ör. katman değişince yeniden çizilen kartlar) kısa süre içinde kayarsa yeniden hizalar.
 */
async function scrollWithinContainer(el: HTMLElement): Promise<void> {
  const gen = ++scrollGen;
  const p = scrollParent(el);
  if (!p) {
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    return;
  }
  const align = (behavior: ScrollBehavior) => {
    const pr = p.getBoundingClientRect();
    const er = el.getBoundingClientRect();
    p.scrollTo({ top: p.scrollTop + (er.top - pr.top) - 12, behavior });
  };
  const visible = () => {
    const pr = p.getBoundingClientRect();
    const er = el.getBoundingClientRect();
    return er.top >= pr.top - 2 && er.top <= pr.top + pr.height * 0.5;
  };
  align('smooth');
  const stop = userInputWatch();
  try {
    for (let t = 0; t < 10; t++) {
      await sleep(250);
      if (stop.touched || !el.isConnected || gen !== scrollGen) return;
      if (!visible()) align('auto');
    }
  } finally {
    stop.dispose();
  }
}

function isShown(el: HTMLElement): boolean {
  const r = el.getBoundingClientRect();
  return r.height > 0 && r.width > 0;
}

/**
 * Öğe DOM'da görünür VE yerinde durur olana dek bekle (tembel sekme, veri yükleme, akordeon açılma animasyonu:
 * konumu art arda iki yoklamada aynı olmalı); en çok `timeoutMs`.
 */
export function waitForElement(find: () => HTMLElement | null, timeoutMs = 6000): Promise<HTMLElement | null> {
  return new Promise((resolve) => {
    const t0 = performance.now();
    let last: number | null = null;
    const tick = () => {
      const el = find();
      if (el && isShown(el)) {
        const y = el.getBoundingClientRect().top + window.scrollY;
        if (last !== null && Math.abs(y - last) < 1) return resolve(el);
        last = y;
      } else last = null;
      if (performance.now() - t0 > timeoutMs) return resolve(el && isShown(el) ? el : null);
      window.setTimeout(tick, 90);
    };
    tick();
  });
}

let jumpSeq = 0;

/** Atlamayı uygula (bkz. dosya başı). Bilinmeyen kimlik sessizce yok sayılır. */
export async function jumpTo(id: JumpId | string): Promise<void> {
  const spec = (JUMPS as Record<string, JumpSpec>)[id];
  if (!spec) return;
  const seq = ++jumpSeq;
  const app = useApp.getState();

  // 1. katmanlar
  if (spec.cell) setCellLayer(spec.cell);
  for (const l of spec.on ?? []) if (!app.layers[l]) app.setLayerVisible(l, true);
  for (const l of spec.off ?? []) if (app.layers[l] && !(CELL_LAYERS as readonly string[]).includes(l)) app.setLayerVisible(l, false);
  // harita görünümü belirleyen atlamalar (cell / on verilmiş) belirlenimci bir görünüm kurar: listede olmayan isteğe bağlı
  // üst katmanlar (aday, akış, halka, 3B, ölçü çizgileri) kapanır — sunum turunda katmanlar birikmesin
  if ((spec.target === 'map' || spec.target === 'result') && (spec.cell || spec.on)) {
    for (const l of OPTIONAL_OVERLAYS) if (app.layers[l] && !spec.on?.includes(l)) app.setLayerVisible(l, false);
  }

  // 2. sekmeler
  if (spec.infoTab) app.setUi({ infoTab: spec.infoTab });
  if (spec.chartTab) app.setUi({ chartTab: spec.chartTab });
  if (spec.veriSection) useInfo.getState().setVeriSection(spec.veriSection);

  // 3. kaydırma + 4. vurgu
  if (spec.target === 'map') {
    flash(document.getElementById('harita'), 1800);
    await scrollToTop();
    if (seq !== jumpSeq) return;
    if (spec.fit) getMapApi()?.fitAnkara({ animate: true });
  } else if (spec.target === 'result') {
    const inPanel = !!document.getElementById('sonuc');
    if (!inPanel) app.setUi({ resultDrawerOpen: true });
    await scrollToTop();
    if (seq !== jumpSeq) return;
    if (spec.fit) getMapApi()?.fitAnkara({ animate: true });
    const el = await waitForElement(() => document.querySelector<HTMLElement>(`[data-testid="${spec.testId}"]`), 4000);
    if (seq !== jumpSeq) return;
    if (el) {
      flash(el);
      await scrollWithinContainer(el);
    }
  } else if (spec.target === 'charts') {
    const el = document.querySelector<HTMLElement>('[data-testid="chart-tabs"]');
    if (el) {
      flash(el, 1800);
      await settleScroll(el, 'start');
    }
  } else {
    // bilgi sekmesi: çapa InfoTabs tarafından (sekme + veri hazır olunca) bulunur
    useInfo.getState().setPendingAnchor(spec.anchor ?? 'info-tabs-top');
  }
  window.dispatchEvent(new CustomEvent('hastane:jumped', { detail: { id } }));
}

/** Veri → Parametreler tablosunda bir parametrenin satırına git (Kaynakça'daki parametre çipleri). */
export function jumpToParam(key: string): void {
  useApp.getState().setUi({ infoTab: 'veri' });
  const info = useInfo.getState();
  info.setVeriSection('parametreler');
  info.setPendingAnchor(`param-${key}`);
}

/** `hastane:jump` olayını dinle (bir kez; InfoTabs takılınca). */
let installed = false;
export function installJumpListener(): void {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  window.addEventListener('hastane:jump', (e) => {
    const id = (e as CustomEvent<{ id?: string }>).detail?.id;
    if (id) void jumpTo(id);
  });
}
