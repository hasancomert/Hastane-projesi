/**
 * Panel testleri (Faz 5): parametre paneli (kaydırıcı/sayı kutusu, bilgi kutusu, alternatif çipleri, değişiklik
 * işareti, sıfırlama), senaryo kartları, canlı çözüm ilerlemesi → yeni sonuç, KPI'lar ve sertifika, hastane ve hücre
 * kartları, kilit listesi. Gerçek sunucuya karşı; 127.0.0.1 dışına istek ve konsol hatası olmamalı.
 *
 *   E2E_SCREENSHOT_DIR=/tmp/ekran npm run test:e2e -- panels
 */
import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';

const LOCAL_HOSTS = new Set(['127.0.0.1', 'localhost']);
const SHOT_DIR = process.env.E2E_SCREENSHOT_DIR;

interface Watch {
  consoleErrors: string[];
  external: string[];
}

async function watchPage(page: Page): Promise<Watch> {
  const w: Watch = { consoleErrors: [], external: [] };
  page.on('console', (m) => {
    if (m.type() === 'error') w.consoleErrors.push(m.text());
  });
  page.on('pageerror', (e) => w.consoleErrors.push(`pageerror: ${e.message}`));
  const isExternal = (url: string) => {
    const u = new URL(url);
    return (u.protocol === 'http:' || u.protocol === 'https:' || u.protocol === 'ws:' || u.protocol === 'wss:') && !LOCAL_HOSTS.has(u.hostname);
  };
  await page.context().route('**/*', (route) => {
    const url = route.request().url();
    if (isExternal(url)) {
      w.external.push(url);
      return route.abort('blockedbyclient');
    }
    return route.continue();
  });
  return w;
}

async function shot(page: Page, name: string) {
  if (!SHOT_DIR) return;
  await page.screenshot({ path: path.join(SHOT_DIR, name), fullPage: false });
}

async function shotEl(page: Page, testId: string, name: string) {
  if (!SHOT_DIR) return;
  await page.getByTestId(testId).first().screenshot({ path: path.join(SHOT_DIR, name) });
}

async function ready(page: Page, url = '/?e2e') {
  await page.goto(url);
  await expect(page.getByTestId('result-summary')).toContainText('hastane', { timeout: 30_000 });
  await expect(page.getByTestId('param-groups')).toBeVisible();
}

function expectClean(w: Watch) {
  expect(w.consoleErrors, 'konsol hataları').toEqual([]);
  expect(w.external, '127.0.0.1 dışına istek').toEqual([]);
}

/** Akordeon grubunu aç (zaten açıksa dokunma). */
async function openGroup(page: Page, id: string) {
  const item = page.locator(`[data-group="${id}"]`);
  const control = item.locator('button.mantine-Accordion-control');
  if ((await control.getAttribute('aria-expanded')) !== 'true') await control.click();
  await expect(control).toHaveAttribute('aria-expanded', 'true');
  return item;
}

const param = (page: Page, key: string) => page.locator(`[data-param-key="${key}"]`).first();

/** Açılır kutunun solma geçişi bitsin (SwiftShader'da çerçeveler yavaş). */
async function popoverSettled(page: Page) {
  await page.waitForFunction(() => {
    const d = document.querySelector('.mantine-Popover-dropdown');
    return !!d && getComputedStyle(d).opacity === '1';
  });
}

test('panel ilk açılış: senaryolar, problem, A–I grupları, KPI, sertifika, sınırlar', async ({ page }) => {
  const w = await watchPage(page);
  await ready(page);
  const pp = page.getByTestId('parameter-panel');
  await expect(pp.getByTestId('scenario-picker').getByRole('radio')).toHaveCount(6);
  await expect(pp.getByRole('radio', { name: /Varsayılan/ })).toHaveAttribute('aria-checked', 'true');
  for (const t of ['A. Talep', 'B.', 'C.', 'D.', 'E.', 'F.', 'G.', 'H.', 'I.']) await expect(pp.getByTestId('param-groups')).toContainText(t);
  // durum süzgeci sayıları (69 parametre)
  await expect(pp.locator('.pp-status-filter')).toContainText('varsayım');
  const rp = page.getByTestId('result-panel');
  await expect(rp.getByTestId('kpi-hospitals')).toContainText('89');
  await expect(rp.getByTestId('kpi-cost')).toContainText('milyar TL');
  await expect(rp.getByTestId('kpi-cost')).toContainText('Erişim');
  await expect(rp.getByTestId('certificate')).toContainText('Alt sınır L');
  await expect(rp.getByTestId('certificate')).toContainText('%0,06');
  await expect(rp.getByTestId('bounds-block')).toContainText('634');
  await expect(rp.getByTestId('solve-progress')).toContainText('Sonuç: önbellekten');
  await page.waitForTimeout(400);
  await shot(page, 'panels_1280.png');
  await shotEl(page, 'result-panel', 'panels_result_panel.png');
  expectClean(w);
});

test('bilgi kutusu: kaynak, durum, gerekçe; klavyeyle açılır', async ({ page }) => {
  const w = await watchPage(page);
  await ready(page);
  // A grubu açık: kaynaklı/hesap parametre
  const beds = param(page, 'demand.beds_per_1000');
  await beds.getByRole('button', { name: /anlamı, kaynağı ve gerekçesi/ }).click();
  const dlg = page.getByRole('dialog', { name: /1000 kişiye düşen/ });
  await expect(dlg).toContainText('Sağlık İstatistikleri Yıllığı 2025');
  await expect(dlg).toContainText('Nasıl hesaplandı');
  await expect(dlg).toContainText('Kaynakçada');
  await expect(dlg).toContainText('Kaydırıcı aralığı');
  await expect(dlg).toBeVisible();
  await popoverSettled(page);
  await shot(page, 'panels_info_source.png');
  await page.keyboard.press('Escape');
  await expect(dlg).toBeHidden();
  // varsayım: gerekçe görünür; klavyeyle aç
  await openGroup(page, 'E');
  const fixed = param(page, 'cost.fixed_per_hospital');
  await expect(fixed).toContainText('varsayım');
  await fixed.getByRole('button', { name: /anlamı, kaynağı ve gerekçesi/ }).focus();
  await page.keyboard.press('Enter');
  const dlg2 = page.getByRole('dialog', { name: /sabit açılış maliyeti/ });
  await expect(dlg2).toContainText('Varsayımın gerekçesi');
  await expect(dlg2).toBeVisible();
  await popoverSettled(page);
  await shot(page, 'panels_info_varsayim.png');
  await page.keyboard.press('Escape');
  expectClean(w);
});

test('parametre değişince ilerleme görünür ve yeni sonuç gelir (3 km, canlı)', async ({ page }) => {
  const w = await watchPage(page);
  await ready(page);
  const before = await page.getByTestId('result-summary').textContent();
  await openGroup(page, 'E');
  const fixed = param(page, 'cost.fixed_per_hospital');
  const input = fixed.getByRole('textbox');
  await expect(input).toHaveValue('250');
  await expect(fixed).toContainText('milyon TL');
  // önbellekte olmayan bir değer → canlı çözüm
  const v = 300 + Math.floor(Math.random() * 90);
  await input.fill(String(v));
  await input.press('Enter');
  await expect(fixed).toHaveAttribute('data-changed', 'true');
  await expect(page.getByTestId('changed-params')).toContainText('1 parametre değişti');
  const prog = page.getByTestId('solve-progress');
  await expect(prog).toHaveAttribute('data-state', /debounce|submitting|queued|running/, { timeout: 5_000 });
  await expect(prog).toContainText('İptal');
  // canlı L/U/fark görünür
  await expect(prog).toContainText('Bulunan U', { timeout: 20_000 });
  await expect(page.getByTestId('stale-badge')).toBeVisible();
  await shot(page, 'panels_progress.png');
  await shotEl(page, 'solve-progress', 'panels_progress_box.png');
  await expect(prog).toContainText('Sonuç: canlı çözüm', { timeout: 60_000 });
  await expect(page.getByTestId('stale-badge')).toHaveCount(0);
  const after = await page.getByTestId('result-summary').textContent();
  expect(after).not.toEqual(before);
  await expect(page.getByTestId('certificate')).toContainText('fark');
  await shot(page, 'panels_new_result.png');
  // sıfırla → varsayılan önbellekten
  await page.getByRole('button', { name: 'Tümünü sıfırla' }).click();
  await expect(fixed.getByRole('textbox')).toHaveValue('250');
  await expect(page.getByTestId('result-summary')).toContainText('89 hastane', { timeout: 30_000 });
  expectClean(w);
});

test('alternatif çipi değeri uygular; tanım kümesi dışı değer gönderilmez', async ({ page }) => {
  const w = await watchPage(page);
  await ready(page);
  const beds = param(page, 'demand.beds_per_1000');
  await beds.getByRole('button', { name: /^2024: 31,3/ }).click();
  await expect(beds.getByRole('textbox')).toHaveValue('3,13');
  await expect(beds).toHaveAttribute('data-changed', 'true');
  // tanım kümesi dışı (0) → hata, gönderilmez
  const input = beds.getByRole('textbox');
  await input.fill('0');
  await input.press('Enter');
  await expect(beds.getByRole('alert')).toContainText('tanım kümesi');
  // kaydırıcı aralığı dışı (6) → izin sorulur
  await input.fill('6');
  await input.press('Enter');
  await expect(beds).toContainText('Kaydırıcı aralığının');
  await expect(beds.getByRole('button', { name: /İzin ver ve uygula/ })).toBeVisible();
  await beds.scrollIntoViewIfNeeded();
  await shot(page, 'panels_param_validation.png');
  await beds.getByRole('button', { name: /varsayılana dön/ }).click();
  await expect(beds.getByRole('textbox')).toHaveValue('3,15');
  expectClean(w);
});

test('senaryo kartı: Düşük bütçe → bütçe modu ve bütçe girişi; sonuç önbellekten', async ({ page }) => {
  const w = await watchPage(page);
  await ready(page);
  await page.getByRole('radio', { name: /Düşük bütçe/ }).click();
  await expect(page.getByRole('radio', { name: /Düşük bütçe/ })).toHaveAttribute('aria-checked', 'true');
  const quick = page.getByTestId('problem-quick');
  await expect(quick.locator('[data-param-key="solver.budget_tl"]')).toBeVisible();
  await expect(quick.locator('[data-param-key="solver.budget_tl"]').getByRole('textbox')).toHaveValue('346');
  await expect(page.getByTestId('result-summary')).toContainText('hastane', { timeout: 30_000 });
  await expect(page.getByTestId('solve-progress')).toContainText('Sonuç:', { timeout: 30_000 });
  await expect(page.getByTestId('certificate')).toContainText('bütçe modunda');
  await shot(page, 'panels_scenario_budget.png');
  await page.getByRole('radio', { name: /Varsayılan/ }).click();
  await expect(page.getByTestId('result-summary')).toContainText('89 hastane', { timeout: 30_000 });
  expectClean(w);
});

type Store = { getState(): Record<string, unknown> & { select(s: unknown): void; result: { hospitals: { cell_id: string; size: { at_bound: string | null } }[]; layers: { candidates: { cell_id: string[] } } } } };

test('hastane ve hücre kartları; kilit listesi', async ({ page }) => {
  const w = await watchPage(page);
  // uzun kartlar tek ekranda görünsün diye yüksek pencere
  await page.setViewportSize({ width: 1366, height: 1500 });
  await ready(page);
  // bir üst sınırdaki hastaneyi seç (harita tıklaması yerine depo)
  const idx = await page.evaluate(() => {
    const st = (window as unknown as { __hastane: { store: Store } }).__hastane.store.getState();
    const i = st.result.hospitals.findIndex((h) => h.size.at_bound === 'ust');
    st.select({ kind: 'hospital', index: i, cellId: st.result.hospitals[i].cell_id });
    return i;
  });
  const card = page.getByTestId('hospital-card');
  await expect(card).toBeVisible();
  await expect(card).toContainText(`Hastane ${idx + 1}`);
  await expect(card.getByTestId('size-explanation')).toContainText('eşiğinin altında kalıyor çünkü');
  await expect(card).toContainText('1 ek yatağın değeri');
  await expect(card).toContainText('Bu hastane kapatılırsa?');
  await page.waitForTimeout(400);
  await shot(page, 'panels_hospital_card.png');
  await shotEl(page, 'hospital-card', 'panels_hospital_card_only.png');

  // aday olmayan nüfuslu bir hücre (kartta neden yazmalı)
  await page.evaluate(() => {
    const s = (window as unknown as { __hastane: { store: { getState(): { grids: Record<number, { features: { properties: { id: string; pop: number; water: number } }[] }>; problem: { gridKm: number }; result: { layers: { candidates: { cell_id: string[] } } }; select(x: unknown): void } } } }).__hastane.store.getState();
    const g = s.grids[s.problem.gridKm];
    const cand = new Set(s.result.layers.candidates.cell_id);
    const i = g.features.findIndex((f) => !cand.has(f.properties.id) && f.properties.pop > 0 && f.properties.pop < 200 && f.properties.water < 10);
    s.select({ kind: 'cell', gridIndex: i, cellId: g.features[i].properties.id });
  });
  const cell = page.getByTestId('cell-card');
  await expect(cell).toBeVisible();
  await expect(cell).toContainText('uygun değil');
  await expect(cell).toContainText('Gölge fiyat');
  await shotEl(page, 'cell-card', 'panels_cell_card.png');

  // bir aday hücreye "Hastane zorunlu" → kilit listesinde görünür, çözüm başlar
  const cellId = await page.evaluate(() => {
    const s = (window as unknown as { __hastane: { store: { getState(): { grids: Record<number, { features: { properties: { id: string } }[] }>; problem: { gridKm: number }; result: { hospitals: { cell_id: string }[]; layers: { candidates: { cell_id: string[] } } }; select(x: unknown): void } } } }).__hastane.store.getState();
    const open = new Set(s.result.hospitals.map((h) => h.cell_id));
    const id = s.result.layers.candidates.cell_id.find((c) => !open.has(c))!;
    const g = s.grids[s.problem.gridKm];
    const i = g.features.findIndex((f) => f.properties.id === id);
    s.select({ kind: 'cell', gridIndex: i, cellId: id });
    return id;
  });
  await expect(cell).toContainText('aday');
  await cell.getByRole('button', { name: 'Hastane zorunlu' }).click();
  await expect(page.getByTestId('lock-list')).toContainText(cellId);
  await expect(page.getByTestId('solve-progress')).toHaveAttribute('data-state', /debounce|submitting|queued|running/, { timeout: 5_000 });
  await page.getByTestId('lock-list').scrollIntoViewIfNeeded();
  await shotEl(page, 'lock-list', 'panels_lock_list.png');
  await expect(page.getByTestId('solve-progress')).toContainText('Sonuç:', { timeout: 60_000 });
  // kilitli hücrede hastane açılmış olmalı
  await expect(cell).toContainText('açık hastane', { timeout: 10_000 });
  await page.getByTestId('lock-list').getByRole('button', { name: 'Tüm kilitleri kaldır' }).click();
  await expect(page.getByTestId('lock-list')).not.toContainText(cellId);
  await expect(page.getByTestId('result-summary')).toContainText('89 hastane', { timeout: 30_000 });
  expectClean(w);
});

test('arama ve durum süzgeci', async ({ page }) => {
  const w = await watchPage(page);
  await ready(page);
  const pp = page.getByTestId('parameter-panel');
  await pp.getByRole('textbox', { name: 'Parametre ara' }).fill('dolambac');
  await expect(pp.getByTestId('param-search-results')).toContainText('Dolambaç');
  await pp.getByRole('textbox', { name: 'Parametre ara' }).fill('');
  await pp.locator('.pp-status-filter').getByRole('button', { name: /^varsayım/ }).click();
  await expect(pp.getByTestId('param-search-results')).toContainText('parametre bulundu');
  await expect(pp.getByTestId('param-search-results').locator('[data-param-key]').first()).toContainText('varsayım');
  await shot(page, 'panels_filter_varsayim.png');
  expectClean(w);
});

test('ekranlar: 1920×1080, koyu tema, telefon çekmecesi', async ({ page }) => {
  const w = await watchPage(page);
  await page.setViewportSize({ width: 1920, height: 1080 });
  await ready(page);
  await page.waitForTimeout(600);
  await shot(page, 'panels_1920.png');
  await page.evaluate(() => (window as unknown as { __hastane: { store: { getState(): { setColorScheme(s: string): void } } } }).__hastane.store.getState().setColorScheme('dark'));
  await openGroup(page, 'C');
  await page.waitForTimeout(600);
  await shot(page, 'panels_1920_dark.png');
  await page.evaluate(() => (window as unknown as { __hastane: { store: { getState(): { setColorScheme(s: string): void } } } }).__hastane.store.getState().setColorScheme('light'));
  // telefon: yeni sayfa (dar düzende sonuç paneli çekmecededir)
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/?e2e');
  await expect(page.locator('.app')).toHaveAttribute('data-layout', 'narrow');
  await expect(page.getByTestId('header-summary')).toContainText('hastane', { timeout: 30_000 });
  await page.getByRole('button', { name: 'Parametreler', exact: true }).click();
  await expect(page.getByRole('dialog').getByTestId('parameter-panel')).toBeVisible();
  await page.waitForTimeout(400);
  await shot(page, 'panels_mobile_params.png');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Sonuç', exact: true }).click();
  await expect(page.getByRole('dialog').getByTestId('result-panel')).toBeVisible();
  await expect(page.getByRole('dialog').getByTestId('result-summary')).toContainText('hastane');
  await page.waitForTimeout(400);
  await shot(page, 'panels_mobile_result.png');
  expectClean(w);
});

test('iptal: "durduruldu — en iyi bulunan" etiketi ve sertifika', async ({ page }) => {
  const w = await watchPage(page);
  await ready(page);
  // hedef fark %0,2: çözüm süre sınırına yakın sürer, İptal'e basmaya zaman kalır (önbellekte de olmaz)
  await page.evaluate(() => {
    const s = (window as unknown as { __hastane: { store: { getState(): { setParam(k: string, v: unknown): void; meta: { parameters: Record<string, { value: number }> } } } } }).__hastane.store.getState();
    // milyonda birlik oynama: önbellek anahtarı her koşuda yeni (önceki koşunun bitmiş sonucu gelmesin)
    s.setParam('access.value_of_time_tl_per_h', s.meta.parameters['access.value_of_time_tl_per_h'].value * (1 + Math.random() * 1e-6));
    s.setParam('solver.mip_gap', 0.0015);
  });
  await openGroup(page, 'E');
  const input = param(page, 'cost.fixed_per_hospital').getByRole('textbox');
  const v = 400 + Math.floor(Math.random() * 90);
  await input.fill(String(v));
  await input.press('Enter');
  const prog = page.getByTestId('solve-progress');
  // İptal'e SON işin (yeni değerle) ilk çözümü gelince basılır: önceki değişikliğin yerini alan işin çözümü ya da
  // henüz gönderilmekte olan iş iptal edilirse "en iyi bulunan" çözüm olmaz (doğru davranış, ama bu testin konusu değil)
  await page.waitForFunction(
    (tl) => {
      const s = (window as unknown as { __hastane: { store: { getState(): { job: { jobId: string | null; problemKey: string; progress: { ub?: number | null } | null } | null } } } }).__hastane.store.getState();
      return !!s.job && !!s.job.jobId && s.job.problemKey.includes(`"cost.fixed_per_hospital",${tl}`) && s.job.progress?.ub != null;
    },
    v * 1_000_000,
    { timeout: 30_000 },
  );
  await expect(prog).toContainText('Bulunan U');
  await prog.getByRole('button', { name: 'İptal' }).click();
  await expect(page.getByTestId('result-panel')).toContainText('durduruldu — en iyi bulunan', { timeout: 30_000 });
  await expect(page.getByTestId('result-summary')).toContainText('Durduruldu');
  await expect(page.getByTestId('certificate')).toContainText('durduruldu');
  await page.waitForTimeout(300);
  await shot(page, 'panels_cancelled.png');
  expectClean(w);
});

test('çevrimdışı: parametreler salt okunur, uyarı ve önceden çözülmüş sonuç', async ({ page }) => {
  const w = await watchPage(page);
  await page.route('**/api/health', (route) => route.abort('connectionrefused'));
  await page.goto('/?e2e');
  await expect(page.getByTestId('connection-badge')).toHaveText('çevrimdışı mod');
  await expect(page.getByTestId('result-summary')).toContainText('89 hastane', { timeout: 30_000 });
  const pp = page.getByTestId('parameter-panel');
  await expect(pp).toContainText('Çevrimdışı mod');
  await expect(param(page, 'demand.beds_per_1000').getByRole('textbox')).toBeDisabled();
  await expect(page.getByTestId('solve-progress')).toContainText('çevrimdışı yedek');
  await page.waitForTimeout(300);
  await shot(page, 'panels_offline.png');
  expect(w.consoleErrors.filter((e) => !/api\/health|ERR_CONNECTION_REFUSED|Failed to load resource/.test(e)), 'konsol hataları').toEqual([]);
  expect(w.external, '127.0.0.1 dışına istek').toEqual([]);
});
