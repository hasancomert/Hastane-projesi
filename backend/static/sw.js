/*
 * Ankara Hastane Yerleşimi — servis çalışanı (çevrimdışı yedek, Faz 5).
 * Derleme sırasında frontend/build/sw-template.js'ten üretilir (frontend/build/offline.ts); elle düzenlemeyin.
 *
 *   - Kurulumda sayfa kabuğu (index.html, assets/*), gömülü yedek JSON'ları (offline/*) ve ilçe adlarının glifleri
 *     önbelleğe alınır.
 *   - Sayfa gezintisi: önce ağ (sunucu açıkken her zaman güncel index.html), ağ yoksa önbellekteki index.html.
 *   - assets/, offline/, glifler: önce önbellek (dosya adları içerik özetli; sürüm değişince önbellek yenilenir).
 *   - /api/* (canlı çözüm, SSE) ve altlık PMTiles (Range istekleri) HİÇ işlenmez: doğrudan ağ.
 */
const VERSION = 'f0175f8311b7';
const CACHE = `hastane-${VERSION}`;
const CORE = ["/assets/deck-CUN8SZ7O.js","/assets/exportPng-Dap9ruBR.js","/assets/index-CpiZpVXS.js","/assets/index-qa3o0qTW.css","/assets/inter-latin-ext-wght-normal-DO1Apj_S.woff2","/assets/inter-latin-wght-normal-Dx4kXJAl.woff2","/assets/mantine-BpkuxVfJ.js","/assets/mantine-DxJkCdt5.css","/assets/maplibre-B8-mEBLf.css","/assets/maplibre-CXMTXXoK.js","/assets/maplibre-gl-worker-VaXr2Fck.mjs","/assets/react-Cy19Odwv.js","/assets/rolldown-runtime-hePW80VL.js","/favicon.svg","/index.html","/offline/boundaries.json","/offline/default_3km.json","/offline/grid_3km.json","/offline/manifest.json","/offline/meta.json","/offline/sources.json"];
const OPTIONAL = ["/basemap/fonts/Noto%20Sans%20Medium/0-255.pbf","/basemap/fonts/Noto%20Sans%20Medium/256-511.pbf"];

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      // assets/* özetli ve 'immutable': tarayıcı önbelleğinden alınabilir; diğerleri sunucudan taze
      await cache.addAll(CORE.map((u) => new Request(u, { cache: u.startsWith('/assets/') ? 'default' : 'reload' })));
      await Promise.all(OPTIONAL.map((u) => cache.add(new Request(u, { cache: 'reload' })).catch(() => undefined)));
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      for (const key of await caches.keys()) {
        if (key.startsWith('hastane-') && key !== CACHE) await caches.delete(key);
      }
      await self.clients.claim();
    })(),
  );
});

async function fromCache(request) {
  const cache = await caches.open(CACHE);
  // sunucu dosyaları 'Vary: Accept-Encoding' ile verir; önbellek eşleşmesinde yok sayılır
  return cache.match(request, { ignoreVary: true });
}

async function networkFirstPage(request) {
  try {
    return await fetch(request);
  } catch (err) {
    const hit = await fromCache('/index.html');
    if (hit) return hit;
    throw err;
  }
}

async function cacheFirst(request) {
  const hit = await fromCache(request);
  return hit || fetch(request);
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  const p = url.pathname;
  if (p.startsWith('/api/')) return;
  if (request.mode === 'navigate') {
    event.respondWith(networkFirstPage(request));
    return;
  }
  if (p.startsWith('/basemap/')) {
    if (p.startsWith('/basemap/fonts/')) event.respondWith(cacheFirst(request));
    return;
  }
  event.respondWith(cacheFirst(request));
});
