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
const VERSION = '3ecf8375f67c';
const CACHE = `hastane-${VERSION}`;
const CORE = ["/assets/BibliographyTab-CpswkvDZ.js","/assets/HocaTab-DE0RrIvo.js","/assets/KaTeX_AMS-Regular-BQhdFMY1.woff2","/assets/KaTeX_AMS-Regular-DMm9YOAa.woff","/assets/KaTeX_AMS-Regular-DRggAlZN.ttf","/assets/KaTeX_Caligraphic-Bold-ATXxdsX0.ttf","/assets/KaTeX_Caligraphic-Bold-BEiXGLvX.woff","/assets/KaTeX_Caligraphic-Bold-Dq_IR9rO.woff2","/assets/KaTeX_Caligraphic-Regular-CTRA-rTL.woff","/assets/KaTeX_Caligraphic-Regular-Di6jR-x-.woff2","/assets/KaTeX_Caligraphic-Regular-wX97UBjC.ttf","/assets/KaTeX_Fraktur-Bold-BdnERNNW.ttf","/assets/KaTeX_Fraktur-Bold-BsDP51OF.woff","/assets/KaTeX_Fraktur-Bold-CL6g_b3V.woff2","/assets/KaTeX_Fraktur-Regular-CB_wures.ttf","/assets/KaTeX_Fraktur-Regular-CTYiF6lA.woff2","/assets/KaTeX_Fraktur-Regular-Dxdc4cR9.woff","/assets/KaTeX_Main-Bold-Cx986IdX.woff2","/assets/KaTeX_Main-Bold-Jm3AIy58.woff","/assets/KaTeX_Main-Bold-waoOVXN0.ttf","/assets/KaTeX_Main-BoldItalic-DxDJ3AOS.woff2","/assets/KaTeX_Main-BoldItalic-DzxPMmG6.ttf","/assets/KaTeX_Main-BoldItalic-SpSLRI95.woff","/assets/KaTeX_Main-Italic-3WenGoN9.ttf","/assets/KaTeX_Main-Italic-BMLOBm91.woff","/assets/KaTeX_Main-Italic-NWA7e6Wa.woff2","/assets/KaTeX_Main-Regular-B22Nviop.woff2","/assets/KaTeX_Main-Regular-Dr94JaBh.woff","/assets/KaTeX_Main-Regular-ypZvNtVU.ttf","/assets/KaTeX_Math-BoldItalic-B3XSjfu4.ttf","/assets/KaTeX_Math-BoldItalic-CZnvNsCZ.woff2","/assets/KaTeX_Math-BoldItalic-iY-2wyZ7.woff","/assets/KaTeX_Math-Italic-DA0__PXp.woff","/assets/KaTeX_Math-Italic-flOr_0UB.ttf","/assets/KaTeX_Math-Italic-t53AETM-.woff2","/assets/KaTeX_SansSerif-Bold-CFMepnvq.ttf","/assets/KaTeX_SansSerif-Bold-D1sUS0GD.woff2","/assets/KaTeX_SansSerif-Bold-DbIhKOiC.woff","/assets/KaTeX_SansSerif-Italic-C3H0VqGB.woff2","/assets/KaTeX_SansSerif-Italic-DN2j7dab.woff","/assets/KaTeX_SansSerif-Italic-YYjJ1zSn.ttf","/assets/KaTeX_SansSerif-Regular-BNo7hRIc.ttf","/assets/KaTeX_SansSerif-Regular-CS6fqUqJ.woff","/assets/KaTeX_SansSerif-Regular-DDBCnlJ7.woff2","/assets/KaTeX_Script-Regular-C5JkGWo-.ttf","/assets/KaTeX_Script-Regular-D3wIWfF6.woff2","/assets/KaTeX_Script-Regular-D5yQViql.woff","/assets/KaTeX_Size1-Regular-C195tn64.woff","/assets/KaTeX_Size1-Regular-Dbsnue_I.ttf","/assets/KaTeX_Size1-Regular-mCD8mA8B.woff2","/assets/KaTeX_Size2-Regular-B7gKUWhC.ttf","/assets/KaTeX_Size2-Regular-Dy4dx90m.woff2","/assets/KaTeX_Size2-Regular-oD1tc_U0.woff","/assets/KaTeX_Size3-Regular-CTq5MqoE.woff","/assets/KaTeX_Size3-Regular-DgpXs0kz.ttf","/assets/KaTeX_Size4-Regular-BF-4gkZK.woff","/assets/KaTeX_Size4-Regular-DWFBv043.ttf","/assets/KaTeX_Size4-Regular-Dl5lxZxV.woff2","/assets/KaTeX_Typewriter-Regular-C0xS9mPB.woff","/assets/KaTeX_Typewriter-Regular-CO6r4hn1.woff2","/assets/KaTeX_Typewriter-Regular-D3Ib7_Hf.ttf","/assets/LimitationsTab-CAz15KQe.js","/assets/ModelTab-bmn28_fe.js","/assets/VeriTab-Cfc9I6f1.js","/assets/deck-CsSBKwiE.js","/assets/exportPng-Dap9ruBR.js","/assets/facts-DmsfoWFT.js","/assets/index-DS8vabDE.css","/assets/index-b9pR2TFe.js","/assets/inter-latin-ext-wght-normal-DO1Apj_S.woff2","/assets/inter-latin-wght-normal-Dx4kXJAl.woff2","/assets/katex-B2rXIh2p.js","/assets/katex-Ngnvt50Q.css","/assets/mantine-BcjSUSIl.js","/assets/mantine-DxJkCdt5.css","/assets/maplibre-B8-mEBLf.css","/assets/maplibre-CXMTXXoK.js","/assets/maplibre-gl-worker-VaXr2Fck.mjs","/assets/react-BFF9PYP6.js","/assets/rolldown-runtime-hePW80VL.js","/assets/useInfoData-C1t044Eo.js","/favicon.svg","/figurler/dal_sinir_3km.png","/figurler/maliyet_3km.png","/figurler/pareto_3km.png","/figurler/tornado_3km.png","/figurler/u_egrisi_3km.png","/index.html","/offline/boundaries.json","/offline/default_3km.json","/offline/grid_3km.json","/offline/manifest.json","/offline/meta.json","/offline/sources.json"];
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
