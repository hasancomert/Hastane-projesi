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
const VERSION = '__SW_VERSION__';
const CACHE = `hastane-${VERSION}`;
const CORE = __SW_CORE__;
const OPTIONAL = __SW_OPTIONAL__;

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
