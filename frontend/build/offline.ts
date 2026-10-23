/**
 * Çevrimdışı yedek — derleme eklentisi (şartname 5 "Sağlamlık", 10 "sunucu kapalıyken varsayılan çözüm görünüyor";
 * docs/API.md §8 notu).
 *
 * Sunucu TAMAMEN kapalıyken `/api/precomputed/*` de kapalıdır. Bu yüzden derleme iki şey üretir:
 *
 *   1. `backend/static/offline/*.json` — varsayılan 3 km çözümü (`precomputed/default_3km.json`), 3 km ızgarası
 *      (`data/processed/grid/grid_3km_cells.geojson`), il/ilçe sınırları (`data/processed/web/*.geojson`) ve
 *      `/api/meta` + `/api/sources`'un derleme anındaki kopyası (parametre paneli çevrimdışı da açıklamalarıyla
 *      görünsün). Hepsi küçültülmüş JSON; sunucu bunları gzip'le verir (≈0,45 MB aktarım).
 *   2. `backend/static/sw.js` — servis çalışanı (kaynak: build/sw-template.js). İlk ziyarette arka planda sayfa
 *      kabuğunu (index.html, assets/*), yukarıdaki JSON'ları ve ilçe adlarının glif dosyalarını önbelleğe alır.
 *      Sunucu kapanıp sayfa yeniden yüklenince sayfa önbellekten açılır, `/api/health` yanıt vermez,
 *      `api/offline.ts`'teki 'gomulu' sağlayıcı bu dosyaları okur → "Çevrimdışı mod — canlı çözüm kapalı".
 *
 * `/api/*` ve PMTiles (Range istekleri) servis çalışanından geçmez: canlı çözüm ve altlık her zaman ağdan.
 *
 * Geliştirme sunucusunda (`npm run dev`) aynı JSON'lar `/offline/*.json` adresinden bellekten verilir (servis
 * çalışanı yalnız derlemede kaydedilir).
 *
 * `/api/meta` kopyası için Python gerekir (`.venv`; başka yol: HASTANE_PYTHON). Bulunamazsa derleme bir uyarıyla
 * sürer ve çevrimdışı modda parametre paneli boş kalır (çözüm ve harita yine görünür).
 */
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Plugin } from 'vite';

const here = path.dirname(fileURLToPath(import.meta.url));

/** Çevrimdışı yedeğin ızgarası ve senaryosu (arayüzdeki 'gomulu' sağlayıcı aynı değerleri bekler). */
export const OFFLINE_GRID_KM = 3;
export const OFFLINE_SCENARIO = 'varsayilan';
export const OFFLINE_DIR = 'offline';

/** İlçe adları (fallback stilinde de) bu glif aralıklarıyla çizilir: Latin + Türkçe (Ş, Ğ, İ → 256–511). */
const OPTIONAL_GLYPHS = ['/basemap/fonts/Noto%20Sans%20Medium/0-255.pbf', '/basemap/fonts/Noto%20Sans%20Medium/256-511.pbf'];

/** Önbelleğe alınmayan derleme dosyaları (servis çalışanının kendisi, git yer tutucusu). */
const SKIP = new Set(['sw.js', '.gitkeep']);

interface Logger {
  info(msg: string): void;
  warn(msg: string): void;
}

const minifyJson = (file: string) => JSON.stringify(JSON.parse(readFileSync(file, 'utf8')));

function pythonExe(repoRoot: string): string {
  if (process.env.HASTANE_PYTHON) return process.env.HASTANE_PYTHON;
  const venv = process.platform === 'win32' ? path.join(repoRoot, '.venv', 'Scripts', 'python.exe') : path.join(repoRoot, '.venv', 'bin', 'python');
  return existsSync(venv) ? venv : process.platform === 'win32' ? 'python' : 'python3';
}

const META_SCRIPT = [
  'import json, sys',
  'from backend.app import meta, tasks',
  'tasks.load_all()',
  'out = {"meta": meta.build_meta(tasks.REGISTRY), "sources": meta.build_sources()}',
  'sys.stdout.write(json.dumps(out, ensure_ascii=False, separators=(",", ":")))',
].join('\n');

/** /api/meta ve /api/sources'un derleme anındaki kopyası (sunucunun kendi kodu: backend/app/meta.py). */
function metaSnapshot(repoRoot: string, log: Logger): { meta: string; sources: string } | null {
  const py = pythonExe(repoRoot);
  try {
    const out = execFileSync(py, ['-c', META_SCRIPT], {
      cwd: repoRoot,
      encoding: 'utf8',
      maxBuffer: 64 * 1024 * 1024,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
      stdio: ['ignore', 'pipe', 'pipe'],
      timeout: 60_000,
    });
    const j = JSON.parse(out) as { meta: unknown; sources: unknown };
    return { meta: JSON.stringify(j.meta), sources: JSON.stringify(j.sources) };
  } catch (ex) {
    log.warn(`[çevrimdışı] /api/meta kopyası üretilemedi (${py}): ${ex instanceof Error ? ex.message.split('\n')[0] : String(ex)}`);
    return null;
  }
}

/** Çevrimdışı JSON dosyaları: ad → içerik. */
export function offlineFiles(repoRoot: string, log: Logger): Record<string, string> {
  const files: Record<string, string> = {};
  files[`default_${OFFLINE_GRID_KM}km.json`] = minifyJson(path.join(repoRoot, 'precomputed', `default_${OFFLINE_GRID_KM}km.json`));
  files[`grid_${OFFLINE_GRID_KM}km.json`] = minifyJson(path.join(repoRoot, 'data', 'processed', 'grid', `grid_${OFFLINE_GRID_KM}km_cells.geojson`));
  const web = path.join(repoRoot, 'data', 'processed', 'web');
  files['boundaries.json'] = `{"il":${minifyJson(path.join(web, 'ankara_il.geojson'))},"ilceler":${minifyJson(path.join(web, 'ankara_ilceler.geojson'))}}`;
  const snap = metaSnapshot(repoRoot, log);
  if (snap) {
    files['meta.json'] = snap.meta;
    files['sources.json'] = snap.sources;
  }
  const sizes = Object.fromEntries(Object.entries(files).map(([k, v]) => [k, Buffer.byteLength(v)]));
  files['manifest.json'] = JSON.stringify({
    description_tr: 'Sunucu kapalıyken gösterilen gömülü yedek (Faz 5). Derleme anındaki kopya.',
    created_at: new Date().toISOString(),
    grid_km: OFFLINE_GRID_KM,
    scenario: OFFLINE_SCENARIO,
    has_meta: !!snap,
    bytes: sizes,
  });
  return files;
}

function walk(dir: string, base = dir): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p, base));
    else out.push(path.relative(base, p).split(path.sep).join('/'));
  }
  return out;
}

/** Servis çalışanını derleme çıktısından üretir (önbellek listesi + içerik özetli sürüm). */
function writeServiceWorker(outDir: string, log: Logger): void {
  // Türkçe arayüz yalnız Inter'in latin + latin-ext alt kümelerini yükler (unicode-range); diğer alt kümeler
  // (kiril, yunan, vietnam) önbelleğe alınmaz
  const UNUSED_FONT = /^assets\/inter-(cyrillic|greek|vietnamese)/;
  const rel = walk(outDir)
    .filter((f) => !SKIP.has(f) && !UNUSED_FONT.test(f))
    .sort();
  const hash = createHash('sha256');
  for (const f of rel) hash.update(f).update(readFileSync(path.join(outDir, f)));
  const version = hash.digest('hex').slice(0, 12);
  const core = rel.map((f) => `/${f}`);
  const template = readFileSync(path.join(here, 'sw-template.js'), 'utf8');
  const sw = template
    .replace('__SW_VERSION__', version)
    .replace('__SW_CORE__', JSON.stringify(core))
    .replace('__SW_OPTIONAL__', JSON.stringify(OPTIONAL_GLYPHS));
  writeFileSync(path.join(outDir, 'sw.js'), sw);
  const bytes = rel.reduce((s, f) => s + statSync(path.join(outDir, f)).size, 0);
  log.info(`[çevrimdışı] sw.js: ${core.length} dosya önbelleğe alınacak (${(bytes / 1024 / 1024).toFixed(2)} MB ham), sürüm ${version}`);
}

export function offlineBundle(opts: { repoRoot: string; outDir: string }): Plugin {
  let cache: Record<string, string> | null = null;
  const logger = (ctx?: { warn(m: string): void }): Logger => ({
    info: (m) => console.log(m),
    warn: (m) => (ctx ? ctx.warn(m) : console.warn(m)),
  });
  return {
    name: 'hastane-offline-bundle',
    // geliştirme: /offline/*.json bellekten (derleme gerekmeden çevrimdışı modu denemek için)
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const m = /^\/offline\/([a-z0-9_]+\.json)(?:\?.*)?$/.exec(req.url ?? '');
        if (!m) return next();
        cache ??= offlineFiles(opts.repoRoot, logger());
        const body = cache[m[1]];
        if (body === undefined) return next();
        res.setHeader('content-type', 'application/json; charset=utf-8');
        res.setHeader('cache-control', 'no-cache');
        res.end(body);
      });
    },
    closeBundle: {
      order: 'post',
      handler() {
        if (!existsSync(opts.outDir)) return;
        const log = logger(this);
        const files = offlineFiles(opts.repoRoot, log);
        const dir = path.join(opts.outDir, OFFLINE_DIR);
        mkdirSync(dir, { recursive: true });
        let total = 0;
        for (const [name, body] of Object.entries(files)) {
          writeFileSync(path.join(dir, name), body);
          total += Buffer.byteLength(body);
        }
        log.info(`[çevrimdışı] ${Object.keys(files).length} dosya → ${path.relative(process.cwd(), dir)} (${(total / 1024 / 1024).toFixed(2)} MB ham)`);
        writeServiceWorker(opts.outDir, log);
      },
    },
  };
}
