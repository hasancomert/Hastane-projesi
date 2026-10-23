/**
 * Faz 5 inceleme düzeltmelerinin uçtan uca sınaması (gerçek sunucu, derlenmiş sayfa):
 *   H2  sınır kartı senaryoyla güncellenir (|J|, kapasite alt sınırı bu çözümün ayarlarından)
 *   M4  parametre değişince başlık "(önceki sonuç)" ile soluk, optimallik rozeti gizli
 *   M5  problem adresin # kısmında: yeniden yüklemede senaryo + değişiklik korunur
 *   M3  kaydırıcı sürüklenirken otomatik çözüm başlamaz; bırakınca TEK istek
 *   L2  eksi değer sessizce artıya dönmez: hata yazılır, istek gitmez
 *   M6  klavye: ilk sekme durağı "Haritaya geç" atlama bağlantısı; Esc sayfanın her yerinden seçimi kaldırır
 */
import { expect, test, type Page } from '@playwright/test';

import { expectClean, waitForMapIdle, watchPage, type HastaneWindow } from './helpers.ts';

async function ready(page: Page, url = '/?e2e') {
  await page.goto(url);
  await expect(page.getByTestId('result-summary')).toContainText('hastane', { timeout: 30_000 });
}

async function idleResult(page: Page) {
  await expect(page.getByTestId('solve-progress')).toHaveAttribute('data-state', 'idle', { timeout: 60_000 });
  await expect(page.getByTestId('stale-badge')).toHaveCount(0);
}

async function openGroup(page: Page, id: string) {
  const control = page.locator(`[data-group="${id}"] button.mantine-Accordion-control`);
  if ((await control.getAttribute('aria-expanded')) !== 'true') await control.click();
  await expect(control).toHaveAttribute('aria-expanded', 'true');
}

function countSolves(page: Page): { n: number } {
  const c = { n: 0 };
  page.on('request', (r) => {
    if (r.method() === 'POST' && new URL(r.url()).pathname === '/api/solve') c.n++;
  });
  return c;
}

test('sınır kartı senaryoyla güncellenir; adres durumu yeniden yüklemede korunur', async ({ page }) => {
  const w = await watchPage(page);
  await ready(page);
  await idleResult(page);
  // varsayılan: önceden hesaplı kart, kapasite ⌈18.617,5 / 4.000⌉ = 5
  await expect(page.getByTestId('bound-capacity')).toHaveText('5');
  await expect(page.getByTestId('bound-max')).toHaveText('634');
  await expect(page.getByTestId('bounds-block')).toHaveAttribute('data-source', 'default');
  expect(await page.evaluate(() => location.hash)).toBe('');

  await page.locator('[data-scenario="sadece_genel"]').click();
  await expect.poll(() => page.evaluate(() => (window as unknown as HastaneWindow).__hastane.store.getState().problem.scenarioId)).toBe('sadece_genel');
  await idleResult(page);
  // şehir hastanesi kapalı: en büyük hastane 999 yatak → ⌈18.617,5 / 999⌉ = 19
  await expect(page.getByTestId('bound-capacity')).toHaveText('19');
  await expect(page.getByTestId('bounds-block')).toHaveAttribute('data-source', 'other');
  await expect(page.getByTestId('bound-pmin')).toHaveAttribute('data-dim', 'true');
  await expect(page.getByTestId('bounds-run')).toBeEnabled();
  expect(await page.evaluate(() => location.hash)).toBe('#senaryo=sadece_genel');

  // bir parametre daha → adres; yeniden yükle → aynı problem
  await page.evaluate(() => (window as unknown as { __hastane: { store: { getState(): { setParam(k: string, v: unknown): void } } } }).__hastane.store.getState().setParam('cost.fixed_per_hospital', 300_000_000));
  await expect.poll(() => page.evaluate(() => location.hash)).toContain('p.cost.fixed_per_hospital=300000000');
  await idleResult(page);
  await page.reload();
  await expect(page.getByTestId('result-summary')).toContainText('hastane', { timeout: 30_000 });
  const p = await page.evaluate(() => {
    const s = (window as unknown as HastaneWindow).__hastane.store.getState() as unknown as { problem: { scenarioId: string; overrides: Record<string, unknown> } };
    return { sc: s.problem.scenarioId, fixed: s.problem.overrides['cost.fixed_per_hospital'] };
  });
  expect(p).toEqual({ sc: 'sadece_genel', fixed: 300_000_000 });
  await expect(page.locator('[data-scenario="sadece_genel"]')).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByTestId('changed-params')).toContainText('1 parametre değişti');
  await idleResult(page);
  await expect(page.getByTestId('bound-capacity')).toHaveText('19');

  // varsayılana dön → adres boş
  await page.locator('[data-scenario="varsayilan"]').click();
  await expect.poll(() => page.evaluate(() => location.hash)).toBe('');
  await expect(page.getByTestId('result-summary')).toContainText('89 hastane', { timeout: 30_000 });
  expectClean(w);
});

test('parametre değişince başlık "(önceki sonuç)"; kaydırıcı sürüklenirken çözüm başlamaz, bırakınca tek istek', async ({ page }) => {
  const w = await watchPage(page);
  await ready(page);
  await idleResult(page);
  await waitForMapIdle(page);
  await openGroup(page, 'E');
  const row = page.locator('[data-param-key="cost.fixed_per_hospital"]').first();
  const thumb = row.locator('.mantine-Slider-thumb');
  await page.waitForTimeout(500); // akordeon açılma geçişi bitsin (konum sabitlensin)
  await thumb.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  const box = (await thumb.boundingBox())!;
  const track = (await row.locator('.mantine-Slider-root').boundingBox())!;
  const solves = countSolves(page);
  // başlığın "(önceki sonuç)" durumunu sayfa içinde kaydet (sonuç önbellekten gelirse durum çok kısa sürer)
  await page.evaluate(() => {
    const el = document.querySelector('[data-testid="header-summary"]')!;
    const seen = { stale: false, prefix: false, badgeHidden: false };
    (window as unknown as { __seen: typeof seen }).__seen = seen;
    new MutationObserver(() => {
      if (el.getAttribute('data-stale') === 'true') seen.stale = true;
      if ((el.textContent ?? '').startsWith('(önceki sonuç)')) seen.prefix = true;
      if (seen.stale && !document.querySelector('[data-testid="result-status-badge"]')) seen.badgeHidden = true;
    }).observe(el, { attributes: true, childList: true, subtree: true, characterData: true });
  });
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  // 1,6 sn boyunca sürükle (gecikme 600 ms): bu sürede hiçbir çözüm isteği gitmemeli
  const x0 = box.x + box.width / 2;
  const target = track.x + track.width * (0.35 + Math.random() * 0.1);
  for (let i = 1; i <= 16; i++) {
    await page.mouse.move(x0 + ((target - x0) * i) / 16, box.y + box.height / 2);
    await page.waitForTimeout(100);
  }
  expect(solves.n, 'sürükleme sırasında çözüm isteği').toBe(0);
  await page.mouse.up();
  // kaydırıcı gerçekten taşındı (değer 250 milyon TL'den farklı)
  await expect(row.getByRole('textbox')).not.toHaveValue('250');
  await expect.poll(() => solves.n, { timeout: 5_000 }).toBe(1);
  await idleResult(page);
  expect(solves.n).toBe(1);
  // bırakınca yeni sonuç gelene dek: başlık soluk + "(önceki sonuç)", optimallik rozeti gizli (M4)
  const seen = await page.evaluate(() => (window as unknown as { __seen: { stale: boolean; prefix: boolean; badgeHidden: boolean } }).__seen);
  expect(seen).toEqual({ stale: true, prefix: true, badgeHidden: true });
  await expect(page.getByTestId('header-summary')).not.toHaveAttribute('data-stale', 'true');
  await expect(page.getByTestId('result-status-badge')).toBeVisible();
  await page.getByRole('button', { name: 'Tümünü sıfırla' }).first().click();
  await expect(page.getByTestId('result-summary')).toContainText('89 hastane', { timeout: 30_000 });
  expectClean(w);
});

test('eksi değer reddedilir (istek gitmez); klavye: atlama bağlantısı ve Esc', async ({ page }) => {
  const w = await watchPage(page);
  await ready(page);
  await idleResult(page);
  // ilk sekme durağı (sayfa yeni açıldı): "Haritaya geç" → odak harita tuvalinde
  await page.keyboard.press('Tab');
  const skip = page.getByRole('button', { name: 'Haritaya geç' });
  await expect(skip).toBeFocused();
  await expect(skip).toBeVisible();
  await page.keyboard.press('Enter');
  await expect.poll(() => page.evaluate(() => document.activeElement?.classList.contains('maplibregl-canvas') ?? false)).toBe(true);

  await openGroup(page, 'E');
  const row = page.locator('[data-param-key="cost.fixed_per_hospital"]').first();
  const solves = countSolves(page);
  const input = row.getByRole('textbox');
  await input.fill('-5');
  await input.press('Enter');
  await expect(row.getByRole('alert')).toContainText('tanım kümesi');
  await page.waitForTimeout(1500);
  expect(solves.n).toBe(0);
  const ov = await page.evaluate(() => ((window as unknown as HastaneWindow).__hastane.store.getState() as unknown as { problem: { overrides: Record<string, unknown> } }).problem.overrides);
  expect(ov['cost.fixed_per_hospital']).toBeUndefined();
  await input.press('Escape');

  // Esc sayfanın herhangi bir yerinden seçimi kaldırır (odak haritada değilken de)
  await page.evaluate(() => {
    const s = (window as unknown as HastaneWindow).__hastane.store.getState() as unknown as { result: { hospitals: { cell_id: string }[] }; select(x: unknown): void };
    s.select({ kind: 'hospital', index: 0, cellId: s.result.hospitals[0].cell_id });
  });
  await expect(page.getByTestId('hospital-card')).toBeVisible();
  await page.getByRole('heading', { name: 'Ankara Hastane Yerleşimi' }).click();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('hospital-card')).toHaveCount(0);
  expectClean(w);
});
