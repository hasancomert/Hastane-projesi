# Arayüz (Faz 5) — yapı, durum, veri akışı

Tek sayfalık arayüz: React 19 + TypeScript 6 + Vite 8, MapLibre GL 6 + deck.gl 9 (MapLibreOverlay, `interleaved`),
Mantine 9, zustand 5; Faz 6'da ECharts 6 ve KaTeX. Derleme `backend/static/`'e yazılır ve FastAPI aynı kökenden sunar
(sunum dizüstünde Node gerekmez). Sunucu sözleşmesi: [`API.md`](API.md). **Hiçbir istek 127.0.0.1 dışına çıkmaz**
(yazı tipleri, altlık harita, glifler yerel; e2e testi denetler).

## Çalıştırma

```bash
cd frontend
npm install --before=2026-10-09T00:00:00Z   # sürümler package.json'da sabit; geçişli bağımlılıklar da ≥ 2 hafta eski
# geliştirme: önce sunucu (ayrı terminal)
cd .. && .venv/bin/python -m backend.run --no-browser --port 8000
cd frontend && npm run dev                  # http://127.0.0.1:5173 — /api ve /basemap 8000'e aktarılır
npm run typecheck                           # tsc -b
npm test                                    # birim testleri (vitest; biçimlendiriciler)
npm run build                               # → ../backend/static (.gitkeep korunur)
npm run test:e2e                            # Playwright: derlenmiş sayfa sunucudan; sunucu yoksa kendisi başlatır
E2E_SCREENSHOT_DIR=/tmp/ekran npm run test:e2e   # ekran görüntüleriyle
E2E_SCREENSHOT_DIR=../docs/screenshots npx playwright test flows   # belge görüntüleri (faz5_*.png)
```

`npm run build` ayrıca çevrimdışı yedeği üretir (`build/offline.ts`): `backend/static/offline/*.json` ve
`backend/static/sw.js` (aşağıda "Çevrimdışı"). `/api/meta` kopyası için `.venv` Python'u çağrılır (başka yol:
`HASTANE_PYTHON`); bulunamazsa derleme uyarıyla sürer.

Sunucu başka bir portta ise `HASTANE_BACKEND=http://127.0.0.1:8010 npm run dev`, e2e için `E2E_PORT=8010`.

## Klasörler

| Yol | İçerik |
|---|---|
| `src/api/` | `types.ts` (sonuç JSON'unun tipleri, `layers` dahil), `client.ts` (her uç nokta), `sse.ts` (fetch tabanlı SSE: `Last-Event-ID` ile yeniden bağlanma, takılma tespiti, iptal), `jobs.ts` (`runJob` / `solve` / `evaluate` / `bounds` → `JobOutcome`), `offline.ts` (sağlık yoklaması, yedek sağlayıcılar, Faz 8 kancası) |
| `src/store/` | `appStore.ts` (zustand deposu + seçiciler), `solver.ts` (gecikmeli otomatik çözüm, kaydırıcı sürüklenirken bekletme, "en son istek kazanır", iptal), `bootstrap.ts` (açılış, çevrimdışı/çevrimiçi geçişi), `urlState.ts` (problem adresin `#` kısmında), `prefs.ts` (tema/katman tercihi, localStorage try/catch) |
| `src/format/` | Türkçe sayı, para (milyon/milyar TL), yüzde (`%95,0`), gap, km, kişi, yatak, süre biçimleri + testleri |
| `src/theme/` | Mantine teması (Inter, projeksiyon boyutları), `palette.ts` (renk körlüğüne güvenli sınıf paleti — harita ve lejant ortak; durum renkleri; rampalar), `global.css` (düzen) |
| `src/map/` | `MapView.tsx`, `basemap.ts` (PMTiles stili + düz yedek), `setup.ts` (işçi adresi, pmtiles protokolü), `gridIndex.ts`, `mapApi.ts` (tur/dışa aktarım için harita erişimi), `layers/*.ts` (katman başına bir dosya; `index.ts` çizim sırası `LAYER_ORDER`), `MapLegend.tsx` (lejant + katman paneli), `MapToolbar.tsx` (Seç · Hastane zorunlu · Hastane yasak · 3B · sığdır · PNG), `MapTooltip.tsx`, `MapContextMenu.tsx` (sağ tık kilit menüsü), `mapUiStore.ts` (haritaya özgü küçük depo: araç, imleç, menü, lejant, eğim, yakınlaştırma — imleç hareketi sayfanın geri kalanını yeniden çizdirmez), `useShownSolution.ts` (son istek olursuzsa son olurlu çözüm soluk), `exportPng.ts` (yalnız dışa aktarımda yüklenir) |
| `src/panels/` | `ParameterPanel` (+ `ParamControl`, `ParamInfo`, `paramUtils`), `ScenarioPicker`, `ResultPanel` (seçili hastane/hücre kartı panelin başında), `SolveProgress`, `HospitalCard`, `CellCard`, `LockList`, `BoundsBlock` ("Kaç hastane? En az / en fazla"), `optimality.ts` (optimallik etiketi: başlık, özet ve rozetler tek kural) |
| `src/ui/` | ortak küçük bileşenler: `InfoPopover`, `Kpi`, `StackedBar`, `Sparkline`, `costParts` (maliyet kırılımı parçaları; renkleri `palette.ts`te) |
| `build/` | `offline.ts` (Vite eklentisi: gömülü yedek JSON'ları + servis çalışanı), `sw-template.js` |
| `src/charts/`, `src/info/` | grafik ve bilgi sekmeleri (Faz 6 yer tutucuları; ECharts/KaTeX yalnız tembel yüklenir) |
| `src/components/` | `ErrorBoundary` (beyaz ekran yok), rozetler, tema düğmesi |
| `e2e/` | Playwright: `smoke.spec.ts` (iskelet), `panels.spec.ts` (paneller), `flows.spec.ts` (Faz 5 ana akışları, gerçek harita tıklamalarıyla; sunucu kapalı senaryosu; hiçbir hastane denetim altında değil), `review.spec.ts` (Faz 5 incelemesinin düzeltmeleri: sınır kartı, adres durumu, kaydırıcı sürükleme, eksi değer, klavye), `helpers.ts` |

## Depo (zustand) — `useApp`

| Alan | Anlamı |
|---|---|
| `connection` | `mode` (`connecting` · `online` · `offline`), `health`, `note`, `fallbackSource` |
| `meta`, `boundaries`, `grids[km]` | `/api/meta`, `/api/boundaries`, `/api/grid/{km}` (bir kez yüklenir) |
| `problem` | `gridKm`, `scenarioId`, `overrides` (senaryo + kullanıcı değişiklikleri; `grid.cell_km` hariç), `locks {open, closed}`, `allowOutsideRange` |
| `job` | çalışan/bekleyen iş: `phase` (`debounce` · `submitting` · `queued` · `running` · `cancelling`), `progress` (son olay), `trace` (tüm olaylar — dal-sınır grafiği), `runS`, `position` |
| `result`, `resultInfo` | gösterilen çözüm ve kaynağı (`live` · `cache` · `precomputed` · `fallback`), `stopped` (iptal → "en iyi bulunan"), `problemKey` (eskilik), `liveTrace` |
| `evaluation` | Faz 7 manuel mod sonucu |
| `error` | son hata (422 ayrıntıları `items`) |
| `selection`, `hoveredHospital` | `{kind:'hospital', index, cellId}` ya da `{kind:'cell', gridIndex, cellId}`; üzerine gelinen hastane |
| `layers` | katman görünürlüğü (`LAYER_DEFS`) |
| `colorScheme`, `ui` | tema; sekmeler, çekmeceler, akordeon, otomatik çözüm, sunum modu/turu (Faz 6–7) |

Eylemler: `setParam`, `resetParam`, `resetAllParams`, `applyScenario`, `setGridKm`, `setMode`, `setLock`, `clearLocks`,
`select`, `hoverHospital`, `setLayerVisible`, `toggleLayer`, `setColorScheme`, `setUi` … Seçiciler: `paramValue`,
`changedKeys`, `problemKey`, `selectOkResult`, `selectResultIsStale`, `selectCanSolve`, `selectGrid`,
`selectSelectedHospital`. Ağ işleri depoda değil: `solveNow()`, `cancelSolve()`, `scheduleSolve()` (store/solver.ts).

## Veri akışı

1. `main.tsx` → `startAppEffects()`: `/api/health` (2,5 sn) → çevrimiçiyse `/api/meta` → sınırlar + ızgara → ilk çözüm
   (varsayılan parametreler önbellekten ≈70 ms; ölçülen ilk sonuç ≈1,3 sn, harita boşta ≈3–4 sn, SwiftShader'la).
2. Kullanıcı bir şeyi değiştirir (`setParam`, senaryo, ızgara, kilit) → `problemKey` değişir → 600 ms sonra
   (kesikli seçimler — ızgara, senaryo, amaç modu, nüfus yılı — beklemeden; kaydırıcı sürüklenirken sayaç
   bekletilir, bırakınca tek istek gider: `holdAutoSolve`/`releaseAutoSolve`)
   `POST /api/solve` (`client_id` sekme boyunca sabit, `replace: true`) → önbellekteyse 200 + sonuç, değilse SSE:
   `progress` olayları `job.trace`'e, son `result` olayı `result`'a. Yalnız beklenen işin sonucu gösterilir; yerini
   yeni isteğe bırakan iş izlenmez. İptal edilen işin `durduruldu` sonucu "en iyi bulunan çözüm" diye gösterilir.
3. Harita ve paneller depoya seçicilerle abonedir; `MapView` her değişiklikte `buildDeckLayers(ctx)` ile deck.gl
   katmanlarını yeniden kurar (aynı `id` → verimli güncelleme).
4. Çevrimdışı: sağlık yanıt vermezse (açılışta, bir istek ağ hatası verince ya da çevrimiçiyken 10 sn'lik nabız art
   arda iki kez yanıtsız kalınca) yedek sağlayıcılar sırayla denenir — önce `sunucu-onceden` (HTTP ayakta ama çözücü
   yanıt vermiyorsa: `/api/precomputed/*`, istenen ızgara/senaryo), sonra `gomulu` (sunucu TAMAMEN kapalıyken:
   derlemeye gömülü `/offline/*.json`, yalnız 3 km varsayılan). Başlığın altında tam genişlik "Çevrimdışı mod —
   canlı çözüm kapalı" bandı neyin gösterildiğini söyler; parametreler salt okunur, kilit araçları kapalı. Sağlık
   5 sn'de bir yoklanır; sunucu dönünce çevrimiçine geçilir ve problem yeniden çözülür (önbellekten anında).

## Çevrimdışı (sunucu tamamen kapalı)

Sunucu kapalıyken `/api/*` de kapalıdır; sayfanın kendisi de ancak önbellekten açılabilir. Bu yüzden:

| Parça | Ne | Nerede |
|---|---|---|
| Gömülü yedek | `default_3km.json` (önceden çözülmüş varsayılan, katmanlar ve dual değerlerle), `grid_3km.json`, `boundaries.json`, `meta.json` + `sources.json` (derleme anındaki `/api/meta`, `/api/sources`), `manifest.json` — ≈2,0 MB ham, gzip'le ≈0,4 MB | `backend/static/offline/` (`build/offline.ts`) |
| Servis çalışanı | ilk ziyarette (sayfa `load` olduktan sonra, arka planda) `index.html`, `assets/*`, `offline/*` ve ilçe adlarının glifleri (Noto Sans Medium 0–255, 256–511) önbelleğe alınır; gezinti önce ağdan, ağ yoksa önbellekten; `/api/*` ve PMTiles hiç işlenmez | `backend/static/sw.js` (`build/sw-template.js`'ten; sürüm = içerik özeti) |
| Kayıt | yalnız derlemede, `load`'dan sonra; `?nosw` ile kapatılır | `src/main.tsx` |
| Sağlayıcı | `bundledProvider` ('gomulu'), `loadFallback` sonucu ve ızgarayı aynı ızgaradan birleştirir | `src/api/offline.ts` |
| Problem eşitleme | yedek sonuç gösterilirken problem (ızgara, senaryo) yedeğinkine eşitlenir → sonuç "eski" sayılmaz | `src/store/bootstrap.ts` |

Sunucu kapalıyken altlık harita (PMTiles) yoktur: düz arka plan + il/ilçe sınırları ve adları çizilir. Sınama:
`e2e/flows.spec.ts` "sunucu tamamen kapalı" (kendi sunucusunu 8019'da ayrı önbellek diziniyle açar, servis çalışanı
kurulunca kapatır, sayfayı yeniden yükler, sonra sunucuyu yeniden açıp sayfanın kendiliğinden bağlandığını denetler).
Diğer testlerde servis çalışanı `playwright.config.ts`'te engellidir (`serviceWorkers: 'block'`).

## Kurallar

- Arayüz dili Türkçe; sayılar yalnız `src/format` ile (`1.234.567,8`, `%95,0`, `381,31 milyar TL`); geçersiz değer "—".
- Renkler yalnız `src/theme/palette.ts`'ten; hastane sınıfı rengi harita, lejant ve grafiklerde aynı.
- Her parametre birimini ve durumunu (kaynaklı · hesap · varsayım · model seçimi) gösterir; kaynak/gerekçe tooltip'te.
- ECharts ve KaTeX yalnız `React.lazy` ile yüklenen bileşenlerden içe aktarılır (ilk açılış < 3 sn).
- Her bölüm `ErrorBoundary` içinde; harita altlığı yüklenemezse düz arka plan + sınırlar (`data-basemap="fallback"`).
- Test kancaları: `data-testid` (`map`, `result-summary`, `header-summary`, `connection-badge`, `solve-progress`
  (`data-state`), `result-status-badge`, `stale-badge`, `parameter-panel`, `param-groups`, `result-panel`,
  `hospital-card`, `cell-card`, `lock-list`, `map-legend`, `map-legend-open`, `offline-banner`, `certificate`,
  `bounds-block`), haritada `data-map-state` (`loading` · `idle` · `error`), `data-basemap` (`pmtiles` ·
  `fallback`), `data-tool` (`select` · `lockOpen` · `lockClosed`); `?e2e` adres parametresi depoyu
  `window.__hastane.store`, haritayı `window.__hastaneMap` (MapApi) ve `window.__hastaneMapDebug` (deck.gl ölçümleri)
  olarak açar.
- `MapApi` (`getMapApi()` / `onMapApi()`): `fitAnkara`, `flyTo`, `flyToHospital`, `flyToCell`, `toPng`, `exportPng`,
  `downloadPng`, `whenIdle`, `basemap`, `setTilt`, `setTool`, `map`, `container` — Faz 6 sunum turu bunları kullanır.
- **Optimallik etiketi tek kuraldan** (`panels/optimality.ts`): başlık cümlesi, sonuç özeti, sonuç rozeti ve sertifika
  rozeti aynı etiketi kullanır. Alt sınır var ve kanıtlı fark ≤ arayüzün hedef farkı (`solver.mip_gap`, varsayılan
  %1) → "Optimum (kanıtlı fark %g)"; fark daha büyükse "En iyi bulunan çözüm (fark %g)"; iptal edilen iş
  "Durduruldu — en iyi bulunan çözüm"; alt sınır yoksa "En iyi bulunan çözüm (alt sınır yok)". Sunucunun
  `message_tr` başı ("Optimum:" / "En iyi bulunan çözüm:") istemcide bu etiketle değiştirilir; motorun kendi durumu
  (`sure_doldu` …) ipucunda aynen durur.
- **Eski sonuç:** parametre değişip yeni çözüm beklenirken (ya da istek hata verdiyse) başlık soluk, italik ve
  "(önceki sonuç)" ile başlar; optimallik rozeti gizlenir, yerine "önceki sonuç" rozeti çıkar.
- **Sınır kartı** (`BoundsBlock`): |J| ve kapasite alt sınırı ⌈talep / en büyük izinli hastane⌉ GÖSTERİLEN çözümün
  ayarlarından (`result.instance`) hesaplanır; kapsama (LSCP) ve p_min varsayılan parametreler dışında soluk +
  "varsayılan parametrelerle"; "Bu ayarlarla hesapla" `POST /api/bounds` işini (≈20 sn, iptal edilebilir) başlatır,
  yeni çözüm başlayınca kendiliğinden iptal olur (tek çözücü işçisi).
- **Adres durumu** (`store/urlState.ts`): `#izgara=5&senaryo=…&p.<anahtar>=<JSON>&zorunlu=…&yasak=…&aralik=1`;
  yalnız seçili senaryodan farklı değerler yazılır; açılışta meta yüklenince okunur (bilinmeyen anahtar/hücre yok
  sayılır), çevrimdışıyken yazılmaz. `history.replaceState` (geçmiş şişmez).
- **Harita sığdırma** (H3): Ankara üstteki denetimlerin ARASINA sığdırılır (`overlayPadding`: katman düğmesi, araç
  çubuğu, lejant, ölçek/atıf; lejant için alttan ya da soldan boşluk, Ankara'yı büyük gösteren seçilir). Lejant en
  çok haritanın %35'i, uzunsa kendi içinde kayar; harita 650 px'ten alçaksa (1280×720) lejant kapalı başlar; açılıp
  kapanınca harita yeniden sığar. Telefonda harita yüksekliği Ankara'nın en-boy oranıyla sınırlı, kaydırma sınırı
  altlık kesitine yakın (gri şerit yok), yakınlaştırma düğmeleri yok (iki parmak).
- **Renkler** (H6): hücre katmanlarının rampaları ayrı ve hastane sınıf renklerinden farklı tonlarda — nüfus
  menekşe, erişim kehribar (R dışı kırmızı durum rengi), gölge fiyat kızıl; hizmet bölgesi açık tint (%22 / koyu
  %30); hastanelerde yüzey renkli halka + 1,5 px mürekkep konturu.
- **Okunabilirlik** (H4/H5): 1280 px'te kök 16 px → xs 14 px (ikincil), sm 16 px (gövde), rozetler 12–15 px;
  `dimmed` ikincil mürekkep (#52514e / #c3c2b7), renkli metin açık temada 9. ton, dolu rozetlerde `autoContrast`.
  İnceleme betiğiyle (1920×1080, tüm görünür metin) WCAG AA dışı metin: açık 167 → 0, koyu 145 → 0 (bu ayarlarda
  modele girmeyen parametreler %90 opaklıkla soluk, yine ≥ 4,5:1). 1280 px'te en küçük metin atıf satırı (12 px).
- **Klavye** (M6): sayfanın ilk sekme durağı "Haritaya geç · Sonuçlara geç · Parametrelere geç" atlama bağlantıları;
  harita tuvalinde ve giriş kutularında görünür odak halkası; Esc sayfanın her yerinden (açık iletişim kutusu ya da
  yazı alanı yoksa) menü → araç → seçim sırasıyla kapatır.
- **PNG dışa aktarımı**: haritanın üstüne ayrı bir başlık şeridi (senaryo, ızgara, değişiklik/kilit sayısı, tek
  cümlelik sonuç, tarih); yakınlaştırma düğmeleri gizlenir ama ölçek çubuğu ve atıf yerinde kalır.

## Ölçümler (geliştirme kabı, başsız Chromium + SwiftShader yazılım WebGL, 2026-10-23, inceleme düzeltmelerinden sonra)

Kap yüklüyken ölçüldü (yük ortalaması ≈3); aynı betikle inceleme öncesi ölçümler parantez içinde.

| Ölçüm | Değer |
|---|---|
| İlk açılış, 1280×720, derlenmiş sayfa (5 temiz bağlam, önbellek kapalı) | FCP 0,34–0,72 sn · sonuç görünür 1,83–2,29 sn (1,07–1,91) · harita boşta 4,3–4,9 sn · 35 istek, 2,16 MB |
| İlk açılış, e2e içinde (istek yönlendirmesiyle) | sonuç görünür 2,14 sn · harita boşta 5,0 sn |
| Canlı çözüm sırasında (3 km) | tıklama → durum 0,20 sn · olay döngüsü gecikmesi en çok 0,21 sn · en uzun görev 0,20 sn |
| Izgara değişimi (sonuç + harita boşta) | 2 km 2,5 sn · 5 km 2,1 sn · 3 km 1,6 sn |
| Sunucu kapalı, yeniden yükleme → gömülü sonuç | 1,8 sn |
| "Bu ayarlarla hesapla" (sınır kartı, 3 km, Sadece genel hastane) | 21 sn (p_min 20 sn'de kanıtlanamadı: 27–49) |
| Derleme (`backend/static`) | JS 2,84 MB ham / 0,80 MB gzip (maplibre 0,29; deck 0,27; mantine 0,11; react 0,06; uygulama 0,06) · CSS 0,05 MB gzip · toplam 5,9 MB; servis çalışanı 21 dosya önbelleğe alır (yalnız latin + latin-ext Inter) |

Sunum dizüstünde (gerçek GPU) ölçülmedi — Faz 8.

Belge ekran görüntüleri (`docs/screenshots/`, `flows.spec.ts` üretir): `faz5_1280x720.png`, `faz5_1920x1080.png`,
`faz5_karanlik_1920x1080.png`, `faz5_mobil.png` (390×844), `faz5_hastane_karti.png`, `faz5_cozum_suruyor.png`,
`faz5_cevrimdisi.png` (sunucu tamamen kapalı).

## Faz 5 durumu

**Çalışanlar.** Tek sayfa uçtan uca gerçek sunucuya karşı çalışıyor:

- Harita (MapLibre + deck.gl, yerel Protomaps altlığı): il/ilçe sınırları ve adları (altlıktaki ilçe adı tekrarı
  kaldırıldı), nüfus yoğunluğu (log) ve 3B sütunlar, aday hücreler ve kilitler, sınıf renkli ve yatakla ölçekli
  hastaneler, hizmet bölgeleri, hasta akışları, erişim ısı haritası (R dışı vurgulu), R halkaları, gölge fiyatlar;
  lejant, katman paneli, ipuçları, sağ tık kilit menüsü, 3B eğim, başlıklı PNG dışa aktarımı.
- Parametre paneli: 69 parametre A–I gruplarında; kaydırıcı (tam genişlik) + birimli sayı kutusu, durum rozeti
  (kaynaklı · hesap · varsayım · model seçimi), kaynak/gerekçe kutusu, varsayılana dön, hazır değer çipleri, arama
  ve durum süzgeci; altı hazır senaryo; 2/3/5 km ızgara, nüfus yılı, amaç modu.
- Canlı çözüm: gecikmeli (kaydırıcı bırakılınca tek istek), kesikli seçimlerde hemen; ilerleme, canlı L/U/fark,
  İptal ("durduruldu — en iyi bulunan"); sayfa çözüm sırasında donmuyor.
- Sonuç paneli: tutarlı optimallik etiketi, KPI'lar, maliyet kırılımı, sertifika, "Neden bu çözüm?" kısa açıklamaları,
  senaryoya göre güncellenen sınır kartı (+ isteğe bağlı canlı hesap); hastane ve hücre kartları.
- Adres çubuğunda problem durumu (yeniden yüklemede korunur, paylaşılabilir); açık/koyu tema; 1280×720, 1920×1080
  ve telefon düzenleri; klavye ile gezinme; çevrimdışı mod (sunucu tamamen kapalıyken gömülü 3 km varsayılan çözüm).
- Testler: 28 Playwright testi (yerel istek dışı istek yok, konsolda hata yok), 28 birim testi.

**Sonraki fazlara kalanlar.**

- **Faz 6 (açıklama katmanı):** U eğrisi (`precomputed/p_sweep_3km.json` çevrimdışı yedeğe de eklenmeli), Pareto,
  tornado, dal-sınır ve maliyet kırılımı grafikleri; `explain` hesapları (N − 1 / N + 1, "bu hastane kapatılırsa" —
  düğmeler hazır, sunucu 501 döndürüyor); Model (KaTeX), Veri, Sınırlılıklar, Kaynakça (`id="kaynak-<id>"`
  çapaları) ve Hocanın Listesi sekmeleri (✓ işareti içerik gelince eklenecek); sunum turu (`MapApi.fitAnkara`
  artık denetimlerin arasına sığdırıyor).
- **Faz 7 (gelişmiş etkileşim):** manuel mod (`/api/evaluate`, "optimumdan %X pahalı"), senaryo karşılaştırma,
  CSV/GeoJSON dışa aktarımı, sunum modu (tam ekran, büyük yazı).
- **Faz 8 (çevrimdışı ve QA):** sunum dizüstünde (Windows, gerçek GPU) kare hızı ve ilk açılış ölçümü, `start.bat`,
  `prepare_presentation.py`, altlık haritanın (47 MB) çevrimdışı önbelleği, diğer ızgara/senaryoların çevrimdışı
  yedeği, Bölüm 10 kabul listesinin tek tek denetimi, README.
