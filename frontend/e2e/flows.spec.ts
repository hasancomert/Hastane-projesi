/**
 * Faz 5 bütünleştirme: ana akışlar uçtan uca, gerçek sunucuya karşı, gerçek harita tıklamalarıyla.
 *
 *   açılış (meta + varsayılan sonuç önbellekten) → harita hastaneleri ve hizmet bölgelerini çizer
 *   → hastaneye / hücreye tıkla → kartlar
 *   → parametre değiştir → gecikmeli canlı çözüm, ilerleme (L, U, fark) → yeni sonuç; sayfa donmaz
 *   → harita araç çubuğuyla kilit (zorunlu / yasak) → yeniden çözüm
 *   → senaryo değiştir · ızgara 2/3/5 · tema · 1920×1080 · telefon
 *   → sunucu TAMAMEN kapalı + sayfa yeniden yüklenir → gömülü varsayılan çözüm + "çevrimdışı mod" bandı;
 *     sunucu dönünce kendiliğinden yeniden bağlanır.
 *
 * Her testte: konsolda hata yok, 127.0.0.1 dışına istek yok.
 * Belge ekran görüntüleri: E2E_SCREENSHOT_DIR=../docs/screenshots npx playwright test flows
 */
import { expect, test, type Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { cellCenter, expectClean, mapPoint, shot, waitForMapIdle, watchPage, type HastaneWindow } from './helpers.ts';

async function ready(page: Page, url = '/?e2e') {
  await page.goto(url);
  await expect(page.getByTestId('result-summary')).toContainText('hastane', { timeout: 30_000 });
}

/**
 * Güncel problemin sonucu gelene dek bekle: iş bitti (idle) ve sonuç eski değil. Aynı kilit daha önce çözüldüyse
 * sonuç önbellekten gelir ("Sonuç: önbellekten"), yoksa canlı.
 */
async function waitSolved(page: Page) {
  const prog = page.getByTestId('solve-progress');
  await expect(prog).toHaveAttribute('data-state', 'idle', { timeout: 60_000 });
  await expect(page.getByTestId('stale-badge')).toHaveCount(0);
  await expect(prog).toContainText(/Sonuç: (canlı çözüm|önbellekten)/);
}

/** Lejantı aç (alçak haritada — 1280×720 — kapalı başlar ki Ankara'yı örtmesin). */
async function openLegend(page: Page) {
  const btn = page.getByTestId('map-legend-open');
  if (await btn.isVisible()) await btn.click();
  await expect(page.getByTestId('map-legend')).toBeVisible();
}

/** Haritadaki hastanelerden lejant, araç çubuğu, katman düğmesi ya da ölçek/atıf altında kalanlar (H3). */
async function coveredHospitals(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const st = (window as unknown as HastaneWindow).__hastane.store.getState();
    const api = (window as unknown as { __hastaneMap: { map: { project(p: [number, number]): { x: number; y: number } }; container: HTMLElement } }).__hastaneMap;
    const r = api.container.getBoundingClientRect();
    const out: string[] = [];
    for (const h of st.result!.hospitals) {
      const p = api.map.project([h.lon, h.lat]);
      const el = document.elementFromPoint(r.left + p.x, r.top + p.y);
      if (!el || !el.classList.contains('maplibregl-canvas')) out.push(`${h.cell_id} (${h.district}) altında: ${el?.className ?? 'dışarıda'}`);
    }
    return out;
  });
}

async function openGroup(page: Page, id: string) {
  const control = page.locator(`[data-group="${id}"] button.mantine-Accordion-control`);
  if ((await control.getAttribute('aria-expanded')) !== 'true') await control.click();
  await expect(control).toHaveAttribute('aria-expanded', 'true');
}

/** Lejant / araç çubuğu altında kalmayan, üst sınırdaki (kart açıklaması zengin) bir hastanenin ekran noktası. */
async function visibleHospital(page: Page): Promise<{ index: number; x: number; y: number; cellId: string }> {
  const hs = await page.evaluate(() => {
    const r = (window as unknown as HastaneWindow).__hastane.store.getState().result!;
    return r.hospitals.map((h, i) => ({ i, lon: h.lon, lat: h.lat, cellId: h.cell_id, upper: h.size.at_bound === 'ust', district: h.district }));
  });
  // merkez ilçelerde hastaneler sık: tek başına duran bir kırsal hastane daha kesin tıklanır
  const order = [...hs].sort((a, b) => Number(b.upper) - Number(a.upper) || Number(a.district === 'Çankaya') - Number(b.district === 'Çankaya'));
  for (const h of order) {
    const p = await mapPoint(page, h.lon, h.lat);
    if (!p.onCanvas) continue;
    const near = hs.some((o) => o.i !== h.i && Math.abs(o.lon - h.lon) < 0.06 && Math.abs(o.lat - h.lat) < 0.05);
    if (near) continue;
    return { index: h.i, x: p.x, y: p.y, cellId: h.cellId };
  }
  throw new Error('görünür hastane bulunamadı');
}

/** Hastanesi olmayan, nüfuslu bir aday hücrenin merkezi (haritada görünür). */
async function visibleCandidateCell(page: Page, avoid: string[] = []): Promise<{ cellId: string; x: number; y: number }> {
  const cands = await page.evaluate((skip) => {
    const s = (window as unknown as HastaneWindow).__hastane.store.getState();
    const r = s.result!;
    const open = new Set(r.hospitals.map((h) => h.cell_id));
    const cand = new Set(r.layers.candidates.cell_id);
    const g = s.grids[s.problem.gridKm];
    return g.features
      .filter((f) => cand.has(f.properties.id) && !open.has(f.properties.id) && !skip.includes(f.properties.id) && f.properties.pop > 3000)
      .slice(0, 400)
      .map((f) => ({ id: f.properties.id, coords: f.geometry.coordinates }));
  }, avoid);
  for (const c of cands) {
    const [lon, lat] = cellCenter(c.coords);
    const p = await mapPoint(page, lon, lat);
    if (p.onCanvas) return { cellId: c.id, x: p.x, y: p.y };
  }
  throw new Error('görünür aday hücre bulunamadı');
}

/* ------------------------------------------------------------------------------------------------ açılış */

test('açılış 1280×720: meta + varsayılan sonuç, harita, lejant, rozetler; ilk açılış süresi', async ({ page }) => {
  const w = await watchPage(page);
  // sayfa içi zaman damgaları (Playwright'ın yoklama aralığı ölçüme girmesin; istek yönlendirmesinin payı girer)
  await page.addInitScript(() => {
    const t: { result?: number; idle?: number } = {};
    (window as unknown as { __t: typeof t }).__t = t;
    const obs = new MutationObserver(() => {
      const r = document.querySelector('[data-testid=result-summary]');
      if (!t.result && r && /hastane/.test(r.textContent ?? '')) t.result = performance.now();
      const m = document.querySelector('[data-testid=map]');
      if (t.result && !t.idle && m?.getAttribute('data-map-state') === 'idle') t.idle = performance.now();
    });
    document.addEventListener('DOMContentLoaded', () => obs.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true }));
  });
  await page.goto('/?e2e');
  await expect(page).toHaveTitle('Ankara Hastane Yerleşimi — Optimizasyon');
  await expect(page.getByTestId('result-summary')).toContainText('89 hastane', { timeout: 30_000 });
  await waitForMapIdle(page);
  const { tResult, tIdle } = await page.evaluate(() => {
    const t = (window as unknown as { __t: { result?: number; idle?: number } }).__t;
    return { tResult: Math.round(t.result ?? -1), tIdle: Math.round(t.idle ?? -1) };
  });
  const nav = await page.evaluate(() => {
    const n = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming;
    const fcp = performance.getEntriesByName('first-contentful-paint')[0];
    return { dcl: Math.round(n.domContentLoadedEventEnd), load: Math.round(n.loadEventEnd), fcp: fcp ? Math.round(fcp.startTime) : null };
  });
  console.log(`[ölçüm] ilk açılış: FCP ${nav.fcp} ms · DOMContentLoaded ${nav.dcl} ms · load ${nav.load} ms · sonuç görünür ${tResult} ms · harita boşta ${tIdle} ms`);

  await expect(page.getByTestId('connection-badge')).toHaveText('canlı çözüm');
  await expect(page.getByTestId('header-summary')).toContainText('milyar TL');
  await expect(page.getByTestId('result-status-badge')).toContainText('kanıtlı fark %0,06');
  await expect(page.getByTestId('map')).toHaveAttribute('data-basemap', 'pmtiles');
  await expect(page.getByTestId('solve-progress')).toContainText('önbellek');
  // optimallik etiketi her yerde aynı (H1): başlık, sonuç özeti, sonuç rozeti, sertifika rozeti
  await expect(page.getByTestId('header-summary')).toHaveText(/^Optimum \(kanıtlı fark %0,06\): 89 hastane/);
  await expect(page.getByTestId('result-summary')).toHaveText(/^Optimum \(kanıtlı fark %0,06\): 89 hastane/);
  await expect(page.getByTestId('certificate-badge')).toContainText('kanıtlı fark %0,06');
  // alçak haritada (1280×720) lejant kapalı başlar ve hiçbir hastane bir denetimin altında kalmaz (H3)
  await expect(page.getByTestId('map-legend-open')).toBeVisible();
  expect(await coveredHospitals(page)).toEqual([]);
  // 89 hastane haritada: deck.gl hastane katmanı veriyle
  const nDeck = await page.evaluate(() => {
    const api = (window as unknown as { __hastaneMapDebug: { overlay: { _deck?: { props: { layers: { id: string; props: { data: unknown[] } }[] } } } } }).__hastaneMapDebug;
    const ls = api.overlay._deck?.props.layers.flat(3) ?? [];
    const h = ls.find((l) => l && l.id === 'hastaneler');
    return h ? (h.props.data as unknown[]).length : -1;
  });
  expect(nDeck).toBe(89);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await shot(page, 'faz5_1280x720.png');
  // lejant açılınca harita lejantın üstüne yeniden sığar: yine hiçbir hastane örtülmez
  await openLegend(page);
  await expect(page.getByTestId('map-legend')).toContainText('Hastaneler (89)');
  await page.waitForTimeout(800); // yeniden sığdırma canlandırması (450 ms)
  await waitForMapIdle(page);
  expect(await coveredHospitals(page)).toEqual([]);
  expectClean(w);
});

/* ------------------------------------------------------------------------------------------------ kartlar */

test('haritada hastaneye tıkla → hastane kartı; hücreye tıkla → hücre kartı; Esc kapatır', async ({ page }) => {
  const w = await watchPage(page);
  await ready(page);
  await waitForMapIdle(page);
  const h = await visibleHospital(page);
  await page.mouse.move(h.x, h.y);
  await page.mouse.click(h.x, h.y);
  const card = page.getByTestId('hospital-card');
  await expect(card).toBeVisible({ timeout: 10_000 });
  await expect(card).toContainText(`Hastane ${h.index + 1}`);
  await expect(card).toContainText('Yatak');
  await expect(card).toContainText('Doluluk');
  await expect(card).toContainText('Neden bu boyut?');
  await expect(card).toContainText('1 ek yatağın değeri');
  await expect(card.getByRole('button', { name: /Bu hücreyi kilitle|Hastane zorunlu/ }).first()).toBeVisible();
  const sel = await page.evaluate(() => (window as unknown as HastaneWindow).__hastane.store.getState().selection);
  expect(sel).toEqual({ kind: 'hospital', index: h.index, cellId: h.cellId });
  await waitForMapIdle(page);
  if (process.env.E2E_SCREENSHOT_DIR) {
    // belge görüntüsü: kartın tamamına yakını görünsün (1920×1080; harita Ankara'ya yeniden sığar)
    await page.setViewportSize({ width: 1920, height: 1080 });
    await expect(page.getByTestId('hospital-card')).toBeVisible();
    await page.mouse.move(5, 5);
    await waitForMapIdle(page);
    await page.waitForTimeout(400);
    await shot(page, 'faz5_hastane_karti.png');
    await page.setViewportSize({ width: 1280, height: 720 });
    await waitForMapIdle(page);
  }

  const c = await visibleCandidateCell(page);
  await page.mouse.click(c.x, c.y);
  const cell = page.getByTestId('cell-card');
  await expect(cell).toBeVisible({ timeout: 10_000 });
  await expect(cell).toContainText(c.cellId);
  await expect(cell).toContainText('aday');
  await expect(cell).toContainText('Hizmet aldığı hastane');
  await expect(page.getByTestId('hospital-card')).toHaveCount(0);

  await page.getByTestId('map').locator('canvas').focus();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('cell-card')).toHaveCount(0);
  expectClean(w);
});

/* ------------------------------------------------------------------------------------------------ canlı çözüm */

test('parametre değişince gecikmeli canlı çözüm: ilerleme, yeni sonuç, sayfa donmaz', async ({ page }) => {
  const w = await watchPage(page);
  // donma ölçümü: uzun görevler (PerformanceObserver) ve olay döngüsü gecikmesi (100 ms'lik zamanlayıcının kayması)
  await page.addInitScript(() => {
    const lt: number[] = [];
    const lag = { max: 0, over200: 0 };
    const w = window as unknown as { __longtasks: number[]; __lag: typeof lag };
    w.__longtasks = lt;
    w.__lag = lag;
    try {
      new PerformanceObserver((list) => list.getEntries().forEach((e) => lt.push(e.duration))).observe({ type: 'longtask', buffered: true });
    } catch {
      /* desteklenmiyor */
    }
    let last = performance.now();
    setInterval(() => {
      const now = performance.now();
      const d = now - last - 100;
      last = now;
      if (d > lag.max) lag.max = d;
      if (d > 200) lag.over200++;
    }, 100);
  });
  await ready(page);
  await waitForMapIdle(page);
  const before = await page.evaluate(() => {
    const r = (window as unknown as HastaneWindow).__hastane.store.getState().result!;
    return { n: r.kpis.n_hospitals, total: r.costs.toplam };
  });
  const header0 = await page.getByTestId('header-summary').innerText();
  // Hedef fark %0,2: çözüm süre sınırına (15 sn) yakın sürer, ilerleme ekranda görülebilir. Zaman değerine milyonda
  // birlik rastgele oynama: önbellek anahtarı her koşuda yeni olsun (sonuç önbellekten değil, canlı gelsin).
  await page.evaluate(() => {
    const s = (window as unknown as { __hastane: { store: { getState(): { setParam(k: string, v: unknown): void; meta: { parameters: Record<string, { value: number }> } } } } }).__hastane.store.getState();
    const k = 'access.value_of_time_tl_per_h';
    s.setParam(k, s.meta.parameters[k].value * (1 + Math.random() * 1e-6));
    s.setParam('solver.mip_gap', 0.002);
  });
  await openGroup(page, 'E');
  const input = page.locator('[data-param-key="cost.fixed_per_hospital"]').first().getByRole('textbox');
  const value = 420 + Math.floor(Math.random() * 60);
  await input.fill(String(value));
  await input.press('Enter');
  await page.evaluate(() => {
    const w = window as unknown as { __longtasks: number[]; __lag: { max: number; over200: number } };
    w.__longtasks.length = 0;
    w.__lag.max = 0;
    w.__lag.over200 = 0;
  });
  const prog = page.getByTestId('solve-progress');
  await expect(prog).toHaveAttribute('data-state', /debounce|submitting|queued|running/, { timeout: 5_000 });
  await expect(page.getByTestId('stale-badge')).toBeVisible();
  // ilerleme: alt sınır L ve bulunan U sayı olarak görünür
  await expect(prog).toContainText(/Alt sınır L\s*[\d.,]+ milyar TL/, { timeout: 30_000 });
  await page.mouse.move(5, 5);
  await expect(prog).toContainText(/Geçen ([4-9],\d|1\d) sn/, { timeout: 15_000 });
  await shot(page, 'faz5_cozum_suruyor.png');
  await expect(prog).toHaveAttribute('data-state', /running|cancelling/); // görüntü çözüm sürerken alındı
  // çözüm sürerken arayüz yanıt verir: akordeon grubu tıklamayla hemen açılır (sayfa içinde ölçülür; Playwright'ın
  // kaydırma/kararlılık beklemesi dahil değil)
  const clickMs = await page.evaluate(async () => {
    const btn = document.querySelector('[data-group="G"] button.mantine-Accordion-control') as HTMLButtonElement;
    btn.scrollIntoView({ block: 'center' });
    await new Promise((r) => requestAnimationFrame(() => r(null)));
    const t0 = performance.now();
    btn.click();
    // zamanlayıcıyla yokla (rAF değil: yazılım WebGL'de kare üretimi yavaş, ana iş parçacığı değil)
    while (btn.getAttribute('aria-expanded') !== 'true' && performance.now() - t0 < 10_000) await new Promise((r) => setTimeout(r, 0));
    return Math.round(performance.now() - t0);
  });
  await expect(prog).toHaveAttribute('data-state', /running|cancelling|submitting|queued/); // ölçüm çözüm sürerken
  await expect(prog).toContainText('Sonuç: canlı çözüm', { timeout: 60_000 });
  const m = await page.evaluate(() => {
    const w = window as unknown as { __longtasks: number[]; __lag: { max: number; over200: number } };
    return { lt: w.__longtasks.slice(), lag: { ...w.__lag } };
  });
  console.log(
    `[ölçüm] canlı çözüm sırasında: tıklama → durum ${clickMs} ms; olay döngüsü en büyük gecikme ${Math.round(m.lag.max)} ms (>200 ms: ${m.lag.over200} kez); uzun görevler ${m.lt.length}, en uzun ${Math.round(Math.max(0, ...m.lt))} ms (yazılım WebGL)`,
  );
  expect(clickMs).toBeLessThan(1000);
  expect(m.lag.max).toBeLessThan(1000);
  await expect(page.getByTestId('stale-badge')).toHaveCount(0);
  const after = await page.evaluate(() => {
    const s = (window as unknown as HastaneWindow).__hastane.store.getState();
    return { n: s.result!.kpis.n_hospitals, total: s.result!.costs.toplam, source: s.resultInfo!.source };
  });
  expect(after.source).toBe('live');
  expect(after.total).toBeGreaterThan(before.total); // sabit maliyet 250 → 420+ milyon TL
  expect(after.n).toBeLessThanOrEqual(before.n);
  await expect(page.getByTestId('header-summary')).not.toHaveText(header0);
  await waitForMapIdle(page);
  await openLegend(page);
  await expect(page.getByTestId('map-legend')).toContainText(`Hastaneler (${after.n})`);
  // varsayılana dön: önbellekten anında
  await page.getByRole('button', { name: 'Tümünü sıfırla' }).first().click();
  await expect(page.getByTestId('result-summary')).toContainText('89 hastane', { timeout: 30_000 });
  expectClean(w);
});

/* ------------------------------------------------------------------------------------------------ kilitler */

test('harita araç çubuğuyla kilit: zorunlu hücrede hastane açılır, yasak hücrede kapanır', async ({ page }) => {
  test.setTimeout(150_000);
  const w = await watchPage(page);
  await ready(page);
  await waitForMapIdle(page);
  const c = await visibleCandidateCell(page);
  const toolbar = page.getByTestId('map');
  await toolbar.getByRole('button', { name: 'Hastane zorunlu', exact: true }).click();
  await expect(page.getByTestId('map')).toHaveAttribute('data-tool', 'lockOpen');
  await page.mouse.click(c.x, c.y);
  await expect(page.getByTestId('lock-list')).toContainText(c.cellId);
  await waitSolved(page);
  const opened = await page.evaluate((id) => (window as unknown as HastaneWindow).__hastane.store.getState().result!.hospitals.some((h) => h.cell_id === id), c.cellId);
  expect(opened).toBe(true);

  // yasak: kentteki bir hastane hücresi (komşular talebi karşılar)
  await waitForMapIdle(page);
  const h = await page.evaluate((locked) => {
    const r = (window as unknown as HastaneWindow).__hastane.store.getState().result!;
    const i = r.hospitals.findIndex((x) => x.district === 'Çankaya' && x.cell_id !== locked);
    return { cellId: r.hospitals[i].cell_id, lon: r.hospitals[i].lon, lat: r.hospitals[i].lat };
  }, c.cellId);
  await toolbar.getByRole('button', { name: 'Hastane yasak', exact: true }).click();
  await page.evaluate(([lon, lat]) => (window as unknown as { __hastaneMap: { flyTo(lon: number, lat: number, z?: number): void } }).__hastaneMap.flyTo(lon, lat, 11), [h.lon, h.lat] as const);
  await waitForMapIdle(page);
  const p = await mapPoint(page, h.lon, h.lat);
  await page.mouse.click(p.x, p.y);
  await expect(page.getByTestId('lock-list')).toContainText(h.cellId);
  await waitSolved(page);
  const res = await page.evaluate(
    ([open, closed]) => {
      const r = (window as unknown as HastaneWindow).__hastane.store.getState().result!;
      return { ok: r.ok, hasOpen: r.hospitals.some((x) => x.cell_id === open), hasClosed: r.hospitals.some((x) => x.cell_id === closed) };
    },
    [c.cellId, h.cellId] as const,
  );
  expect(res).toEqual({ ok: true, hasOpen: true, hasClosed: false });
  await page.keyboard.press('Escape'); // araç → Seç
  await expect(page.getByTestId('map')).toHaveAttribute('data-tool', 'select');
  await page.getByTestId('lock-list').getByRole('button', { name: 'Tüm kilitleri kaldır' }).click();
  await expect(page.getByTestId('result-summary')).toContainText('89 hastane', { timeout: 30_000 });
  expectClean(w);
});

/* ------------------------------------------------------------------------------------------------ senaryo, ızgara */

test('senaryo değişimi ve ızgara 2 / 5 / 3 km: sonuç ve harita güncellenir', async ({ page }) => {
  test.setTimeout(150_000);
  const w = await watchPage(page);
  await ready(page);
  await page.locator('[data-scenario="erisim_oncelikli"]').click();
  await expect(page.getByTestId('result-summary')).toContainText('92 hastane', { timeout: 30_000 });
  await expect(page.getByTestId('header-summary')).toContainText('92 hastane');
  await page.locator('[data-scenario="varsayilan"]').click();
  await expect(page.getByTestId('result-summary')).toContainText('89 hastane', { timeout: 30_000 });
  await openLegend(page);

  for (const [km, n] of [
    [2, 110],
    [5, 69],
    [3, 89],
  ] as const) {
    const t0 = Date.now();
    await page.locator('label', { hasText: new RegExp(`^${km} km$`) }).click();
    await expect(page.getByTestId('result-summary')).toContainText(`${n} hastane`, { timeout: 60_000 });
    await waitForMapIdle(page);
    const cells = await page.evaluate((k) => (window as unknown as HastaneWindow).__hastane.store.getState().grids[k]?.features.length ?? 0, km);
    console.log(`[ölçüm] ızgara ${km} km: ${cells} hücre, sonuç + harita ${Date.now() - t0} ms`);
    await expect(page.getByTestId('map-legend')).toContainText(`Hastaneler (${n})`);
  }
  expectClean(w);
});

/* ------------------------------------------------------------------------------------------------ ekranlar */

test('1920×1080 açık ve koyu tema (tema düğmesi)', async ({ page }) => {
  const w = await watchPage(page);
  await page.setViewportSize({ width: 1920, height: 1080 });
  await ready(page);
  await waitForMapIdle(page);
  await page.waitForTimeout(400);
  await shot(page, 'faz5_1920x1080.png');
  await page.getByRole('button', { name: 'Koyu temaya geç' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-mantine-color-scheme', 'dark');
  await page.mouse.move(700, 600);
  await waitForMapIdle(page);
  await expect(page.getByTestId('map-legend')).toContainText('Hastaneler (89)');
  await page.mouse.move(5, 300);
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  await page.waitForTimeout(600);
  await shot(page, 'faz5_karanlik_1920x1080.png');
  await page.getByRole('button', { name: 'Açık temaya geç' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-mantine-color-scheme', 'light');
  expectClean(w);
});

test.describe('telefon', () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  test('390×844: dar düzen, yatay kaydırma yok, çekmeceler', async ({ page }) => {
    const w = await watchPage(page);
    await page.goto('/?e2e');
    await expect(page.locator('.app')).toHaveAttribute('data-layout', 'narrow');
    await expect(page.getByTestId('header-summary')).toContainText('hastane', { timeout: 30_000 });
    await waitForMapIdle(page);
    await expect(page.getByTestId('map-legend-open')).toBeVisible(); // dar haritada lejant kapalı başlar
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await shot(page, 'faz5_mobil.png');
    // başlığın altındaki tek satırlık özet (M9) ve hiçbir hastane denetim altında değil
    await expect(page.getByTestId('mobile-summary')).toContainText('89 hastane');
    expect(await coveredHospitals(page)).toEqual([]);
    await page.getByRole('button', { name: 'Sonuç', exact: true }).click();
    await expect(page.getByRole('dialog').getByTestId('result-summary')).toContainText('89 hastane');
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: 'Parametreler', exact: true }).click();
    await expect(page.getByRole('dialog').getByTestId('parameter-panel')).toBeVisible();
    expectClean(w);
  });
});

/* ------------------------------------------------------------------------------------------------ sunucu kapalı */

const REPO = path.resolve(process.cwd(), '..');
const PYTHON = process.platform === 'win32' ? path.join(REPO, '.venv', 'Scripts', 'python.exe') : path.join(REPO, '.venv', 'bin', 'python');

async function startServer(port: number, cacheDir: string): Promise<ChildProcess> {
  const proc = spawn(PYTHON, ['-m', 'backend.run', '--no-browser', '--port', String(port)], {
    cwd: REPO,
    env: { ...process.env, HASTANE_CACHE_DIR: cacheDir },
    stdio: 'ignore',
  });
  for (let i = 0; i < 120; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/api/health`);
      if (r.ok) return proc;
    } catch {
      /* henüz açılmadı */
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  proc.kill('SIGKILL');
  throw new Error('sunucu açılmadı');
}

async function stopServer(proc: ChildProcess): Promise<void> {
  if (proc.exitCode !== null) return;
  const exited = new Promise<void>((r) => proc.once('exit', () => r()));
  proc.kill('SIGINT');
  const t = setTimeout(() => proc.kill('SIGKILL'), 20_000);
  await exited;
  clearTimeout(t);
}

test.describe('sunucu tamamen kapalı', () => {
  // servis çalışanı bu testte açık (diğer testlerde playwright.config.ts'te engelli)
  test.use({ serviceWorkers: 'allow' });

  test('sayfa yeniden yüklenince gömülü varsayılan çözüm + çevrimdışı bandı; sunucu dönünce yeniden bağlanır', async ({ page }) => {
    test.setTimeout(180_000);
    const port = Number(process.env.E2E_OFFLINE_PORT ?? 8019);
    const base = `http://127.0.0.1:${port}`;
    const cacheDir = mkdtempSync(path.join(tmpdir(), 'hastane-e2e-cache-'));
    let proc: ChildProcess | null = await startServer(port, cacheDir);
    try {
      const w = await watchPage(page);
      await page.goto(`${base}/?e2e`);
      await expect(page.getByTestId('result-summary')).toContainText('89 hastane', { timeout: 30_000 });
      // servis çalışanı kuruldu, sayfa denetiminde, gömülü dosyalar önbellekte
      const sw = await page.evaluate(async () => {
        const reg = await navigator.serviceWorker.ready;
        for (let i = 0; i < 50 && !navigator.serviceWorker.controller; i++) await new Promise((r) => setTimeout(r, 100));
        const keys = await caches.keys();
        const cache = await caches.open(keys.find((k) => k.startsWith('hastane-'))!);
        const urls = (await cache.keys()).map((r) => new URL(r.url).pathname);
        return { active: !!reg.active, controlled: !!navigator.serviceWorker.controller, n: urls.length, offline: urls.filter((u) => u.startsWith('/offline/')).sort() };
      });
      expect(sw.active && sw.controlled).toBe(true);
      expect(sw.offline).toEqual(expect.arrayContaining(['/offline/default_3km.json', '/offline/grid_3km.json', '/offline/boundaries.json', '/offline/meta.json']));
      console.log(`[ölçüm] servis çalışanı önbelleği: ${sw.n} dosya`);

      await stopServer(proc);
      proc = null;
      await expect.poll(async () => fetch(`${base}/api/health`).then(() => 'açık', () => 'kapalı')).toBe('kapalı');

      // önceki sayfanın kapanması yazılım WebGL'de birkaç saniye sürer; yeni sayfa ölçümü ondan sonra
      await page.goto('about:blank');
      const t0 = Date.now();
      await page.goto(`${base}/?e2e`);
      await expect(page.getByTestId('offline-banner')).toBeVisible({ timeout: 30_000 });
      await expect(page.getByTestId('offline-banner')).toContainText('Çevrimdışı mod — canlı çözüm kapalı');
      await expect(page.getByTestId('offline-banner')).toContainText('gömülü varsayılan çözüm');
      await expect(page.getByTestId('connection-badge')).toHaveText('çevrimdışı mod');
      await expect(page.getByTestId('result-summary')).toContainText('89 hastane', { timeout: 30_000 });
      const tOffline = Date.now() - t0;
      await expect(page.getByTestId('stale-badge')).toHaveCount(0);
      await expect(page.getByTestId('solve-progress')).toContainText('çevrimdışı yedek');
      await expect(page.getByTestId('parameter-panel')).toContainText('Ankara nüfusu'); // meta kopyası
      await expect(page.locator('[data-param-key="demand.beds_per_1000"]').first().getByRole('textbox')).toBeDisabled();
      await expect(page.getByTestId('map')).toHaveAttribute('data-basemap', 'fallback', { timeout: 30_000 });
      await waitForMapIdle(page);
      console.log(`[ölçüm] sunucu kapalı, yeniden yükleme → gömülü sonuç ${tOffline} ms`);
      await page.waitForTimeout(1500); // ilçe adlarının belirme geçişi
      await shot(page, 'faz5_cevrimdisi.png');
      await openLegend(page);
      await expect(page.getByTestId('map-legend')).toContainText('Hastaneler (89)');

      // sunucu dönünce sayfa kendiliğinden çevrimiçine geçer (5 sn'de bir yoklama)
      proc = await startServer(port, cacheDir);
      await expect(page.getByTestId('connection-badge')).toHaveText('canlı çözüm', { timeout: 30_000 });
      await expect(page.getByTestId('offline-banner')).toHaveCount(0);
      await expect(page.getByTestId('solve-progress')).toContainText('önbellek', { timeout: 30_000 });
      // sunucu kapalıyken bağlantı hataları (ERR_CONNECTION_REFUSED) ve önbellekte olmayan glifler beklenen durumdur
      expectClean(w, /ERR_CONNECTION_REFUSED|ERR_FAILED|Failed to load resource/);
    } finally {
      if (proc) await stopServer(proc);
      rmSync(cacheDir, { recursive: true, force: true });
    }
  });
});
