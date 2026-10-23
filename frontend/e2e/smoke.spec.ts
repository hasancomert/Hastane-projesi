/**
 * Duman testi (Faz 5 iskeleti): derlenmiş sayfa sunucudan açılır, harita çizilir, ilk sonuç gelir; konsolda hata
 * yok ve 127.0.0.1 dışına HİÇBİR istek çıkmaz (çevrimdışı sunum şartı).
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
  page.on('request', (r) => {
    if (isExternal(r.url())) w.external.push(r.url());
  });
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

async function waitForMapIdle(page: Page) {
  const map = page.getByTestId('map');
  await expect(map).toHaveAttribute('data-map-state', 'idle', { timeout: 45_000 });
  // katmanlar eklendikten sonraki çizimin de bitmesi için kısa bekleme + yeniden boşta denetimi
  await page.waitForTimeout(500);
  await expect(map).toHaveAttribute('data-map-state', 'idle');
}

async function shot(page: Page, name: string) {
  if (!SHOT_DIR) return;
  await page.screenshot({ path: path.join(SHOT_DIR, name), fullPage: false });
}

test('sayfa açılır: başlık, canlı bağlantı, ilk sonuç, harita; hata ve dış istek yok', async ({ page }) => {
  const w = await watchPage(page);
  const t0 = Date.now();
  await page.goto('/');
  await expect(page).toHaveTitle('Ankara Hastane Yerleşimi — Optimizasyon');
  await expect(page.locator('html')).toHaveAttribute('lang', 'tr');
  await expect(page.getByTestId('connection-badge')).toHaveText('canlı çözüm');
  await expect(page.getByTestId('result-summary')).toContainText('hastane');
  const firstResultMs = Date.now() - t0;
  await waitForMapIdle(page);
  await expect(page.getByTestId('map')).toHaveAttribute('data-basemap', 'pmtiles');
  await expect(page.getByTestId('header-summary')).toContainText('milyar TL');
  console.log(`ilk sonuç ${firstResultMs} ms, harita boşta ${Date.now() - t0} ms`);
  await shot(page, 'scaffold.png');

  expect(w.consoleErrors, 'konsol hataları').toEqual([]);
  expect(w.external, '127.0.0.1 dışına istek').toEqual([]);
});

test('ızgara 5 km seçilince otomatik yeniden çözülür', async ({ page }) => {
  const w = await watchPage(page);
  await page.goto('/');
  await expect(page.getByTestId('result-summary')).toContainText('hastane');
  // Mantine SegmentedControl: girdi görsel olarak gizli, etikete tıklanır
  await page.locator('label', { hasText: /^5 km$/ }).click();
  // varsayılan parametrelerle 5 km sonucu önbellekte (kanıtlı optimum, 69 hastane)
  await expect(page.getByTestId('result-summary')).toContainText('69 hastane', { timeout: 30_000 });
  await waitForMapIdle(page);
  expect(w.consoleErrors, 'konsol hataları').toEqual([]);
  expect(w.external, '127.0.0.1 dışına istek').toEqual([]);
});

test('ekran boyutları: 1920×1080 ve telefon', async ({ page }) => {
  const w = await watchPage(page);
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto('/');
  await expect(page.getByTestId('result-summary')).toContainText('hastane');
  await waitForMapIdle(page);
  await shot(page, 'scaffold_1920.png');
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('.app')).toHaveAttribute('data-layout', 'narrow');
  await waitForMapIdle(page);
  await shot(page, 'scaffold_mobile.png');
  // yatay kaydırma olmamalı
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  expect(w.consoleErrors, 'konsol hataları').toEqual([]);
  expect(w.external, '127.0.0.1 dışına istek').toEqual([]);
});

/** Depoya test kancası (main.tsx, ?e2e). */
async function setParam(page: Page, key: string, value: unknown) {
  await page.evaluate(
    ([k, v]) => {
      const w = window as unknown as { __hastane: { store: { getState(): { setParam(k: string, v: unknown): void } } } };
      w.__hastane.store.getState().setParam(k as string, v);
    },
    [key, value] as const,
  );
}

test('parametre değişince canlı çözüm SSE ile ilerler ve sonuç gelir (5 km)', async ({ page }) => {
  const w = await watchPage(page);
  await page.goto('/?e2e');
  await expect(page.getByTestId('result-summary')).toContainText('hastane');
  await page.locator('label', { hasText: /^5 km$/ }).click();
  await expect(page.getByTestId('result-summary')).toContainText('69 hastane', { timeout: 30_000 });
  // önbellekte olmayan bir değer: canlı çözüm
  await setParam(page, 'cost.fixed_per_hospital', 311_000_000 + Math.floor(Math.random() * 1000) * 1000);
  await expect(page.getByTestId('solve-progress')).toContainText(/Değişiklik algılandı|gönderiliyor|Çözülüyor|Başlangıç|Alt sınır|İyileştirme|Kuyrukta/, { timeout: 10_000 });
  await expect(page.getByTestId('solve-progress')).toContainText('Sonuç: canlı çözüm', { timeout: 60_000 });
  await expect(page.getByTestId('result-summary')).toContainText('hastane');
  await waitForMapIdle(page);
  await shot(page, 'scaffold_live.png');
  expect(w.consoleErrors, 'konsol hataları').toEqual([]);
  expect(w.external, '127.0.0.1 dışına istek').toEqual([]);
});

test('çalışan çözüm iptal edilince en iyi bulunan çözüm "durduruldu" olarak gösterilir (3 km)', async ({ page }) => {
  const w = await watchPage(page);
  await page.goto('/?e2e');
  await expect(page.getByTestId('result-summary')).toContainText('hastane');
  // hedef fark %0,15: çözüm süre sınırına yakın sürer (hızlı biten çözümde İptal'e basmaya zaman kalmazdı)
  await setParam(page, 'solver.mip_gap', 0.0015);
  await setParam(page, 'cost.fixed_per_hospital', 333_000_000 + Math.floor(Math.random() * 1000) * 1000);
  const cancel = page.getByTestId('solve-progress').getByRole('button', { name: 'İptal' });
  // alt sınır görününce (motor çalışıyor) iptal et
  await expect(page.getByTestId('solve-progress')).toContainText('Alt sınır', { timeout: 30_000 });
  await cancel.click();
  await expect(page.getByTestId('result-panel')).toContainText('durduruldu', { timeout: 30_000 });
  expect(w.consoleErrors, 'konsol hataları').toEqual([]);
  expect(w.external, '127.0.0.1 dışına istek').toEqual([]);
});

test('sağlık yanıt vermezse çevrimdışı moda geçer ve önceden çözülmüş sonucu gösterir', async ({ page }) => {
  const w = await watchPage(page);
  await page.route('**/api/health', (route) => route.abort('connectionrefused'));
  await page.goto('/');
  await expect(page.getByTestId('connection-badge')).toHaveText('çevrimdışı mod');
  await expect(page.getByTestId('offline-banner')).toBeVisible();
  await expect(page.getByTestId('result-summary')).toContainText('89 hastane');
  await waitForMapIdle(page);
  // kesilen sağlık isteklerinin konsol iletileri beklenen durumdur
  expect(w.consoleErrors.filter((e) => !/api\/health|ERR_CONNECTION_REFUSED|Failed to load resource/.test(e)), 'konsol hataları').toEqual([]);
  expect(w.external, '127.0.0.1 dışına istek').toEqual([]);
});

test('altlık harita yüklenemezse düz arka plan + sınırlar (beyaz ekran yok)', async ({ page }) => {
  const w = await watchPage(page);
  await page.route('**/basemap/ankara.pmtiles', (route) => route.abort('failed'));
  await page.goto('/');
  await expect(page.getByTestId('map')).toHaveAttribute('data-basemap', 'fallback', { timeout: 30_000 });
  await waitForMapIdle(page);
  await expect(page.getByTestId('result-summary')).toContainText('hastane');
  await shot(page, 'scaffold_basemap_fallback.png');
  expect(w.consoleErrors.filter((e) => !/Failed to load resource|ERR_FAILED/.test(e)), 'konsol hataları').toEqual([]);
  expect(w.external, '127.0.0.1 dışına istek').toEqual([]);
});
