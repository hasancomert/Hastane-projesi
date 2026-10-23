/**
 * Uçtan uca testlerin ortak yardımcıları (flows.spec.ts): konsol/dış istek izleme, harita boşta bekleme, haritada
 * coğrafi noktaya tıklama, depo kancası (`?e2e` → window.__hastane.store, window.__hastaneMap).
 */
import { expect, type Page } from '@playwright/test';
import path from 'node:path';

const LOCAL_HOSTS = new Set(['127.0.0.1', 'localhost']);
export const SHOT_DIR = process.env.E2E_SCREENSHOT_DIR;

export interface Watch {
  consoleErrors: string[];
  consoleWarnings: string[];
  external: string[];
}

/** WebGL sürücüsünün (SwiftShader) performans iletileri, luma.gl hata ayıklama satırları ve Playwright'ın servis çalışanı engeli kodumuzdan değil. */
const DRIVER_NOISE = /GL Driver Message|GPU stall due to ReadPixels|luma\.gl: WebGL uniform block|Service Worker registration blocked by Playwright/;

export async function watchPage(page: Page): Promise<Watch> {
  const w: Watch = { consoleErrors: [], consoleWarnings: [], external: [] };
  page.on('console', (m) => {
    if (DRIVER_NOISE.test(m.text())) return;
    if (m.type() === 'error') w.consoleErrors.push(m.text());
    if (m.type() === 'warning') w.consoleWarnings.push(m.text());
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

export function expectClean(w: Watch, allow: RegExp | null = null) {
  // uyarılar başarısızlık değildir ama rapora yazılır (ör. sunucu kapalıyken "Altlık harita yüklenemedi")
  const warns = [...new Set(w.consoleWarnings.map((m) => m.slice(0, 160)))];
  if (warns.length) console.log(`[konsol uyarıları] ${warns.join(' | ')}`);
  const errs = allow ? w.consoleErrors.filter((e) => !allow.test(e)) : w.consoleErrors;
  expect(errs, 'konsol hataları').toEqual([]);
  expect(w.external, '127.0.0.1 dışına istek').toEqual([]);
}

export async function waitForMapIdle(page: Page, timeout = 60_000) {
  const map = page.getByTestId('map');
  await expect(map).toHaveAttribute('data-map-state', 'idle', { timeout });
  // katman değişikliğinden sonraki çizim de bitsin
  await page.waitForTimeout(500);
  await expect(map).toHaveAttribute('data-map-state', 'idle', { timeout });
}

export async function shot(page: Page, name: string) {
  if (!SHOT_DIR) return;
  await page.screenshot({ path: path.join(SHOT_DIR, name), fullPage: false });
}

/* ------------------------------------------------------------------------------------------------ depo kancası */

export interface HospitalLite {
  cell_id: string;
  lon: number;
  lat: number;
  district: string;
  beds: number;
  size: { at_bound: string | null };
}

export interface StoreState {
  result: { ok: boolean; hospitals: HospitalLite[]; kpis: { n_hospitals: number }; costs: { toplam: number; sabit: number }; layers: { candidates: { cell_id: string[] } } } | null;
  resultInfo: { source: string; stopped: boolean } | null;
  problem: { gridKm: number; scenarioId: string; locks: { open: string[]; closed: string[] } };
  connection: { mode: string };
  selection: { kind: string; index?: number; cellId: string } | null;
  grids: Record<number, { features: { geometry: { coordinates: number[][][] }; properties: { id: string; pop: number; water: number } }[] }>;
  colorScheme: string;
  setParam(k: string, v: unknown): void;
  setLayerVisible(id: string, v: boolean): void;
}

/** `?e2e` adres parametresiyle açılan kancalar (main.tsx, MapView.tsx). */
export interface HastaneWindow {
  __hastane: { store: { getState(): StoreState } };
}

/** Haritada [lon, lat] noktasının sayfa koordinatı ve o noktada haritanın tuvali mi var (lejant vb. örtmüyor). */
export async function mapPoint(page: Page, lon: number, lat: number): Promise<{ x: number; y: number; onCanvas: boolean }> {
  return page.evaluate(
    ([lo, la]) => {
      const api = (window as unknown as { __hastaneMap: { map: { project(p: [number, number]): { x: number; y: number } }; container: HTMLElement } }).__hastaneMap;
      const p = api.map.project([lo, la]);
      const r = api.container.getBoundingClientRect();
      const x = r.left + p.x;
      const y = r.top + p.y;
      const el = document.elementFromPoint(x, y);
      return { x, y, onCanvas: !!el && el.classList.contains('maplibregl-canvas') };
    },
    [lon, lat] as const,
  );
}

/** Izgara hücresinin merkezi (GeoJSON çokgen köşelerinin ortalaması). */
export function cellCenter(coords: number[][][]): [number, number] {
  const ring = coords[0].slice(0, -1);
  const lon = ring.reduce((s, c) => s + c[0], 0) / ring.length;
  const lat = ring.reduce((s, c) => s + c[1], 0) / ring.length;
  return [lon, lat];
}
