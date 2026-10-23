# API — Faz 4 (FastAPI sunucusu)

Bu belge yerel sunucunun uç noktalarını, istek/yanıt biçimlerini ve iş sisteminin davranışını anlatır. Örneklerdeki
sayılar gerçek çalıştırmalardan alınmıştır (kısaltılmış; `…` atlanan alanları gösterir). Para birimi TL (2026 fiyatları),
mesafeler yol km. Makine okunur şema: `GET /openapi.json`. (`/docs` etkileşimli sayfası dosyalarını internetten yüklediği
için çevrimdışı boş açılır; sunumda kullanılmaz.)

## 1. Başlatma ve mimari

```
python -m backend.run [--port 8000] [--no-browser] [--log-level info]
```

- Sunucu **yalnız 127.0.0.1**'e bağlanır (ağdan erişilemez), tek uvicorn işçisi. `--no-browser` verilmezse sunucu hazır
  olunca varsayılan tarayıcı açılır (Faz 8'deki `start.bat` bu komutu çağırır). Ctrl+C kibar kapanıştır.
- **Çözücü işçisi:** sunucu açılırken `spawn` ile **tek, uzun ömürlü bir işçi süreci** başlar. Motorun önbellekleri
  (yapı, model, Lagrange çarpanları, önceki çözümler) bu süreçte yaşar; işler sırayla çalışır. Çözüm hiçbir zaman sunucu
  sürecinde çalışmaz (Lagrange döngüsü GIL'i tutar); sunucu çözüm sırasında da anında yanıt verir.
- **Isınma ve sıcak başlangıç:** işçi "hazır" demeden önce motoru yükler, varsayılan ızgaranın (3 km) yapısını kurar ve
  motorun "aynı yapıdaki önceki çözümler" belleğini `precomputed/` çözümleriyle tohumlar; 5 ve 2 km aynı işi işçi boşken
  yapar (ertelenmiş adımlar; yeni iş en çok bir adım, ≈1,4 sn, bekler). Sunucu açıldıktan sonraki ilk canlı çözüm böylece
  soğuk başlamaz (§10). Bir yapıda isteğin modunda (ör. bütçe) hatırlanan çözüm yoksa o moddaki en yakın önceden
  çözülmüş çözüm de başa eklenir. Tohum yalnız başlangıçtır: açık küme isteğin parametreleriyle yeniden değerlendirilir
  (sonuç tohumdan bağımsız olarak doğrudur, yalnız daha hızlı bulunur). Ayrıca bir
  **yedek HiGHS alt süreci** hazır tutulur (≈68 MB; çekirdek MIP başlarken süreç açılış gecikmesi ödenmez: çözüme
  başlama gecikmesi yedekle 0,10–0,23 sn, yedeksiz Linux'ta ≈0,4 sn, Windows'ta ≈1 sn — Faz 3 ölçümü;
  `backend/model/milp.py set_prespawn`; `HASTANE_PRESPAWN=0` kapatır). Model alt sürece arka planda gönderilir: başlarken ölen ya da
  takılan alt süreç çözümü düşürmez ve sert süre sınırını bozmaz (uyarı yazılır, en iyi çözümle sürülür).
- **Bekçi:** bir iş beklenen süresini aşarsa (iş türüne göre; çözümde 1,1 × süre sınırı + 20 sn, + 30 sn pay) önce
  durdurma istenir: motor durursa en iyi çözüm `cancelled` durumunda ve bir uyarıyla gelir. İş 15 sn içinde durmazsa,
  işçi ölürse ya da kalp atışı 30 sn kesilirse işçi öldürülür ve yeniden başlatılır; o anki iş Türkçe bir iletiyle
  `failed` olur, kuyruktaki işler yeni işçide sürer (`tests/test_phase4_jobs.py`, `tests/test_phase4_watchdog.py`).
  Bütün iş türleri (`solve`, `evaluate`, `bounds`) iptali izler (HiGHS kesme geri çağrısı); iptali izlemeyen bir iş türü
  (`cooperative_cancel=False`) eklenirse işçi iptalde hemen değil, ancak işin bekçi süresi de aşılırsa yeniden başlar.
  Motorun alt süreçleri (çekirdek MIP / tam MILP / yedek HiGHS süreci) ebeveynlerini izler ve onunla birlikte kapanır;
  sunucu sert öldürülse bile işçi ebeveyn bekçisiyle kendini kapatır — sahipsiz süreç kalmaz
  (`tests/test_phase4_server.py`; Linux'ta denetlendi, Windows'ta aynı mekanizma — `multiprocessing.parent_process()`
  süreç tutamağı — dizüstünde Faz 8'de denetlenmeli).
- **İşçiden sunucuya aktarım:** ileti işçide eşzamanlı pickle'lanır ve sonuç JSON'a çevrilebilirlik için denetlenir;
  aktarılamayan sonuç sessizce kaybolmaz, iş Türkçe bir iletiyle `failed` olur ve kuyruk sürer.
- **Önbellek:** `cache/` (git dışı). Aynı istek anında döner (`from_cache: true`). Açılışta `precomputed/default_{2,3,5}km.json`
  ve hazır senaryoların önceden çözülmüş sonuçları (`precomputed/scenario_<id>_<km>km.json`, §3) önbelleğe eklenir:
  varsayılan parametrelerle ya da bir hazır senaryoyla gelen ilk istek uzun süreli çözümü hemen alır.
- **Giriş koruması:** `Host` başlığı yalnız `127.0.0.1` ya da `localhost` olabilir (DNS yeniden bağlamaya karşı; başka
  ad → 400). İstek gövdesi en çok 2 MB (→ 413). Beklenmeyen hata Türkçe JSON 500 olarak döner ve bağlantı açık kalır.
- **Gzip:** `/api/*` JSON yanıtları ve GeoJSON dosyaları `Accept-Encoding: gzip` ile sıkıştırılır; altlık harita
  (PMTiles) sıkıştırılmaz ve Range isteklerine 206 ile yanıt verir.
- **CORS:** yalnız Vite geliştirme sunucusu (`http://localhost:5173`, `http://127.0.0.1:5173`). Derlenmiş arayüz aynı
  kökenden sunulduğu için sunumda CORS gerekmez.

## 2. Uç noktalar

| Yöntem ve yol | Ne döndürür |
|---|---|
| `GET /api/health` | sunucu ve çözücü işçisinin durumu, kuyruk, önbellek |
| `GET /api/meta` | ızgaralar, parametre kaydı (etiket, değer, birim, durum, kaynaklar, gerekçe, aralık, aralık notu, alternatifler, tanım kümesi), A–I grupları, senaryolar, sınıflar, modlar, iş türleri |
| `GET /api/sources` | kaynak listesi (`data/sources/*.yaml`, alan başlıkları `data/SOURCES.md`'den), her kaynağın kullanıldığı parametreler, kaynakça (`data/reference/literatur.yaml`) |
| `GET /api/grid/{km}` | `km` ∈ {2, 3, 5}: hücre GeoJSON'u (ETag, gzip) |
| `GET /api/boundaries` | `{"il": FeatureCollection, "ilceler": FeatureCollection}` (web için sadeleştirilmiş) |
| `GET /api/boundaries/{il\|ilceler}` | tek GeoJSON dosyası (MapLibre kaynağına doğrudan URL olarak verilebilir) |
| `GET /api/measures` | `data/processed/ankara_olculer.json` ("Toplam mesafe" kartı) |
| `GET /api/bounds/{km}` | varsayılan parametrelerle önceden hesaplanmış sınır kartları (`sinir_kartlari.json`) |
| `GET /api/precomputed/{ad}` | yalnız beyaz liste: `default_2km`, `default_3km`, `default_5km`, `p_sweep_3km`, `p_sweep_5km`, `scenarios` (senaryo dizini) ve `scenario_<id>_<km>km` |
| `POST /api/solve` | çözüm işi başlatır (202) ya da önbellekten döner (200) |
| `POST /api/evaluate` | manuel mod: elle seçilen hastaneler için değerlendirme işi |
| `POST /api/bounds` | verilen parametrelerle sınır kartları işi (süre sınırlı) |
| `POST /api/explain` | **taslak (Faz 6)**: N − 1 / N + 1 ve "bu hastane kapatılırsa"; şimdilik 501 (§8) |
| `POST /api/jobs/{tür}` | genel iş gönderimi (kayıtlı her iş türü; Faz 6 türleri dahil) |
| `GET /api/jobs` | son işlerin özeti (`?client_id=…&limit=50`) |
| `GET /api/jobs/{id}` | işin durumu ve (bittiyse) sonucu; `?result=false` sonucu çıkarır |
| `GET /api/jobs/{id}/events` | SSE: ilerleme olayları, kalp atışı, son `result`/`error` olayı; iş bitmiş ve son olay alınmışsa 204 |
| `POST /api/jobs/{id}/cancel` | işi iptal eder |
| `GET /basemap/{yol}` | `data/basemap/` (PMTiles, yazı tipleri, simgeler); Range, ETag |
| `GET /{yol}` | derlenmiş arayüz (`backend/static/`); bilinmeyen yol → `index.html` (SPA) |

## 3. Üst veri

### `GET /api/health`

```json
{"ok": true, "status_tr": "çalışıyor", "api_version": "1", "engine_version": "faz4-414057f58357",
 "solver_ready": true, "seeded": {"solve": 18},
 "worker": {"pid": 2516, "alive": true, "ready": true, "incarnation": 1, "current_job": null,
            "uptime_s": 8.13, "ready_after_s": 1.2, "last_heartbeat_age_s": 0.04},
 "restarts": 0, "restart_log": [], "queue": {"queued": 0, "max_queued": 8, "max_queued_per_client": 4},
 "jobs": {"done": 2}, "job_types": ["bounds", "evaluate", "explain", "solve"],
 "cache": {"entries": 18, …, "max_bytes": 314572800, "hits": 0, "misses": 0, …}}
```

`solver_ready: false` iken işler kabul edilir ve kuyrukta bekler (işçi açılırken ya da yeniden başlarken). Arayüz sunucu
yanıt vermezse `precomputed/` dosyalarıyla çevrimdışı moda geçer (şartname 5).

### `GET /api/meta`

Önemli alanlar:

- `parameters`: kayıttaki 69 anahtarın her biri için
  `{key, label, value, unit, status, status_tr, sources: [{id, title, publisher, url, accessed, kind}], derivation,
  rationale, notes, range, range_note, alternatives, group, domain, allowed, structure}`.
  `allowed` modelin tanım kümesidir (ör. `demand.beds_per_1000` için
  `{"kind": "number", "min": 0, "max": null, "min_inclusive": false, "max_inclusive": true}`); `range` kaydırıcı aralığıdır
  (arayüz tercihi, `range_note`). `structure: true` olan parametreler değişince yapı yeniden kurulur (daha yavaş).
- `groups`: şartname 4.4'teki A–I grupları sırayla (`{id: "A", title: "A. Talep", keys: […]}`); kayıttaki "Nüfus"
  anahtarları A grubundadır (nüfus senaryosu).
- `defaults`: `{grid_km: 3, mode: "toplam", scenario_year: 2025, time_limit_s: 15, gap: 0.01,
  recommended_time_limit_s: {"2": 60, "3": 15, "5": 15}}` (şartname 4.6 hedef süreleri). Süre sınırı verilmeyen
  `POST /api/solve` ızgaranın önerilen süresini kullanır (2 km'de 60 sn).
- `access_rings`: `{R_km: 30, R_crowfly_km: 24.996}` — R yol mesafesidir; haritadaki halka kuş uçuşu yarıçapla
  çizilmeli (30 km'lik daire ≈%20 büyük olurdu). Parametre değişince her sonucun `layers.rings` alanı güncel değeri verir.
- `grids`: `[{km, cells_url, bounds_url, n_cells, n_demand_cells, recommended_time_limit_s, precomputed: {…},
  precomputed_scenarios: {…}}]`.
- `scenarios`: altı hazır senaryo (`data/reference/senaryolar.yaml`): `{id, title, status, overrides, description_tr,
  rationale, derivation, source_keys, mode, grids, precomputed_grids, live_grids}`. Arayüz bir senaryoyu seçince
  `overrides`'ı çözüm isteğine koyar. `grids["3"] = {precomputed: {name, url, status, ub_tl, lb_tl, gap, n_hospitals,
  time_limit_s} | null, live_check: {works_live, status, gap, t_to_1pct_s, time_limit_s, wall_s} | null}`;
  `precomputed_grids`: önceden çözülmüş sonucu olan ızgaralar (aynı istek önbellekten anında döner);
  `live_grids`: önbelleksiz canlı çözümün önerilen süre sınırında olurlu ve fark ≤ %5 bulunduğu ızgaralar
  (ölçüm: `pipeline/34_scenarios.py live`; ortam ve tarih `scenario_live_check`'te).
- `job_types`: `{ad: {description_tr, url, available, unavailable_tr}}` (`explain` şimdilik `available: false`).
- `derived` (λ, bugünkü değer çarpanı, sınıfların sabit/değişken maliyetleri), `classes`, `modes`, `files`.

### Hazır senaryolar

| id | Başlık | Değişiklik | Değerin dayanağı |
|---|---|---|---|
| `varsayilan` | Varsayılan | — | kayıt |
| `dusuk_butce` | Düşük bütçe | bütçe modu, `solver.budget_tl` = 346 milyar TL | olurluluk eşiği ölçümü (λ = 0 ile en düşük açılış maliyeti ≈343 milyar TL) ve canlı çözücü denemeleri; ayrıntı senaryo dosyasındaki `derivation`. Üç ızgarada da önceden çözüldü (2 km'de canlı bütçe sezgiseli bu bütçede olurlu çözüm bulamıyordu; önceden çözümde en düşük açılış maliyetli çözüm başlangıç oldu) |
| `erisim_oncelikli` | Erişim öncelikli | `access.value_of_time_tl_per_h` = 260,3 | kayıttaki alternatif (ortalama saatlik ücret, hesap) |
| `sadece_genel` | Sadece genel hastane | `size.city_enabled` = false | kayıttaki alternatif |
| `sehir_serbest` | Şehir hastanesi serbest | şehir sınıfı 455–4.050 yatak | kayıttaki alternatifler (Yozgat 455; Bilkent/Etlik/Sancaktepe 4.050) |
| `nufus_2030` | 2030 nüfusu | `population.scenario_year` = 2030 | kayıttaki alternatif (TÜİK il projeksiyonu) |

Hangi parametrenin değiştirileceği modelleme tercihidir (`status: model_secimi`); sunucu açılışta her senaryoyu istek
doğrulamasından geçirir. **Önceden çözülmüş senaryolar:** `pipeline/34_scenarios.py solve` her senaryoyu her ızgarada
uzun süreyle çözer (5 ve 3 km 300 sn, 2 km 600 sn; tam sonuç, dual değerler ve harita katmanlarıyla) →
`precomputed/scenario_<id>_<km>km.json`; dizin `precomputed/scenarios.json` (`/api/precomputed/scenarios`). Sunucu
bunları açılışta önbelleğe ekler, işçi de aynı yapıdaki canlı çözümlerin başlangıcı olarak kullanır.

Önceden çözülmüş sonuçlar (amaç milyar TL — bütçe modunda yalnız erişim maliyeti —; fark = alt sınıra göre; hastane
sayısı). `varsayilan` Faz 3'ün uzun çözümleridir (5 km 1.800 sn, 3 km 1.800 sn, 2 km 3.600 sn); diğerleri 2026-10-23,
geliştirme kabında üç ızgara aynı anda çözüldü (5 ve 3 km 300 sn, 2 km 600 sn; 2 km düşük bütçe ayrıca tek başına
1.200 sn):

| Senaryo | 5 km | 3 km | 2 km |
|---|---|---|---|
| `varsayilan` | 402,611 · %0 (kanıtlı) · 69 | 381,314 · %0,062 · 89 | 377,022 · %0,208 · 110 |
| `dusuk_butce` (erişim) | 65,871 · %1,127 · 75 | 35,421 · %1,701 · 89 | 31,690 · **%5,11** · 107 |
| `erisim_oncelikli` | 436,630 · %0,010 · 70 | 406,588 · %0,080 · 92 | 398,903 · %0,252 · 111 |
| `sadece_genel` | 402,611 · %0,010 · 69 | 381,314 · %0,128 · 89 | 377,002 · %0,250 · 109 |
| `sehir_serbest` | 402,611 · %0,009 · 69 | 381,314 · %0,090 · 89 | 377,022 · %0,252 · 110 |
| `nufus_2030` | 411,798 · %0,009 · 68 | 390,655 · %0,153 · 90 | 386,600 · %0,406 · 111 |

2 km düşük bütçe sonucunun farkı büyüktür (%5,11; alt sınır bütçe satırının Lagrange gevşetmesinden, 30,07 milyar TL).
İlk 600 sn'lik çözüm üç ızgara aynı anda çözülürken 37,95 / fark %20,96'da kalmıştı; tek başına 1.200 sn'lik yeniden
çözüm (önceki çözüm ve en düşük açılış maliyetli çözüm başlangıç) 31,69'a indirdi. Daha uzun çözüm
(`pipeline/34_scenarios.py solve --grids 2 --only dusuk_butce --time 2=3600`) farkı daha da daraltabilir; ölçülmedi. Not: 2 km `sadece_genel` çözümü (377,002) varsayılan problem için de olurludur
(şehir hastanesi kullanmıyor) ve varsayılan dosyadakinden (377,022) 20 milyon TL ucuzdur; ikisi de kendi farkı içinde.

## 4. İşler

### Ortak istek alanları (çözüm, değerlendirme, sınır kartları)

| Alan | Tür | Açıklama |
|---|---|---|
| `grid_km` | 2 \| 3 \| 5 | yoksa kayıttaki `grid.cell_km` (3) |
| `scenario_year` | 2025 \| 2030 | `population.scenario_year` |
| `mode` | `toplam` \| `butce` \| `sabit_p` | `solver.objective_mode` |
| `overrides` | nesne | kayıt anahtarı → değer (ör. `{"demand.beds_per_1000": 3.5, "solver.budget_tl": 3.4e11}`) |
| `allow_outside_range` | bool | kaydırıcı aralığı dışındaki değerlere izin ver (uyarıyla; tanım kümesi yine zorunlu) |
| `client_id` | metin | arayüz oturumu ("en son istek kazanır" ve istemci başına kuyruk sınırı için) |
| `replace` | bool | aynı `client_id`'nin aynı türdeki bekleyen ve çalışan işlerini iptal et |
| `use_cache` | bool | önbellekte uygun sonuç varsa hemen döndür (varsayılan true) |

Üst düzey alan ile `overrides` aynı anahtarı farklı değerle verirse 422 (`celiski`). Bilinmeyen alan 422.

### `POST /api/solve`

Ek alanlar: `locks: {"open": [hücre…], "closed": [hücre…]}`, `time_limit` (sn; yoksa `overrides` içinde açıkça
verilen `solver.time_limit_s`, o da yoksa ızgaranın önerilen süresi: 2 km 60 sn, 3 ve 5 km 15 sn), `gap` (yoksa
`solver.mip_gap`), `duals` (atama LP'sinin dual değerleri; varsayılan true).

```http
POST /api/solve
{"grid_km": 3, "mode": "toplam", "overrides": {"demand.safety_margin": 0.05},
 "locks": {"open": ["3km_165_1479"], "closed": []}, "time_limit": 15, "gap": 0.01,
 "client_id": "arayuz-1", "replace": true}
```

Yanıt (202, iş kuyruğa alındı; önbellekten gelirse 200 ve `result` alanı dolu):

```json
{"job_id": "be42d3734ae4412e", "type": "solve", "state": "running", "state_tr": "çalışıyor",
 "client_id": "arayuz-1", "created_at": 1792754152.13, "started_at": 1792754152.13, "finished_at": null,
 "from_cache": false, "cancel_requested": false, "replaced_by": null, "position": null,
 "progress": null, "info": {"grid_km": 3, "mode": "toplam", "scenario_year": 2025, "time_limit_s": 15.0, "gap": 0.01,
 "locks": {"open": 1, "closed": 0}, "params_hash": "…", "overrides": {…}}, "warnings": [],
 "events_url": "/api/jobs/be42d3734ae4412e/events", "cancel_url": "/api/jobs/be42d3734ae4412e/cancel",
 "replaced": ["4f0c…"]}
```

`replaced`: bu istekle iptal edilen eski işler (`replace: true` yalnız `client_id` ile kullanılabilir; yoksa 422).
Sonuç sözlüğü motorun `solve()` çıktısıdır (`backend/model/solve.py`): `status` (`optimal` · `sure_doldu` ·
`gap_ulasilamadi` · `durduruldu` · `sezgisel` · `olurlu_degil` · `cozum_yok` · `hata`), `status_tr`, `message_tr`
(özet cümle, şartname 7.1), `certificate_tr` (şartname 7.2), `bounds {ub_tl, lb_tl, gap, lb_source, lb_scope, …}`,
`hospitals` (her biri `cell_id, cand_pos, grid_index, lon, lat, district, class, class_name, beds, assigned_beds,
population, mean_km, max_km, occupancy, costs {…}, size {…}`), `assignment` (`demand_cell`, `hospital` — `hospitals`
dizisindeki sıra —, `share`, `km`, `population` — yayda taşınan nüfus, hasta akışı yayları için), `costs` (yapı, KDV,
altyapı, donanım, sabit, arsa, işletme, erişim, inşaat, açılış, toplam), `kpis` (… ve `cost_per_capita_tl`), `check`
(bağımsız denetim), `duals` (`demand_cell`, `marginal_cost_tl`, `marginal_cost_per_person_tl`, `bed_value_tl` —
hastane sırasıyla), **`layers`** (§4.1), `trace`, `times`, `seeds` (yeniden değerlendirilen önceki çözümler), `warnings`.
Olursuz ayarlarda iş yine `done` olur; `result.ok = false` ve `message_tr` nedeni ve öneriyi verir.

`hospitals[].size` (şartname 7.5 "neden bu boyut?"): `{class_min_beds, class_max_beds, at_bound: "ust" | "alt" | null,
fixed_tl, per_bed_tl, opening_tl, next_class: {class, beds, opening_tl, per_bed_tl}, prev_class: {…}}` — komşu sınıfın
bu hastaneye en yakın yatak sayısındaki (üst sınıfın en az, alt sınıfın en çok yatağı) açılış maliyeti; sınıf eşiğindeki
maliyet sıçramasını gösterir (ör. 199 yatak IV-C ↔ 200 yatak V-B).

#### 4.1 Harita katmanları (`result.layers`; şartname 6.2, 6.4, 7.4–7.6)

Çözümden türetilir, ek çözüm gerektirmez (`backend/model/layers.py`); `solve`, `evaluate` ve önceden çözülmüş bütün
dosyalarda vardır.

| Alan | Sıra | İçerik |
|---|---|---|
| `rings` | — | `{R_km, R_crowfly_km, factor, note_tr}`: R halkası (yol km ve haritada çizilecek kuş uçuşu yarıçap) |
| `access` | ızgaranın **tüm** hücreleri, `GET /api/grid/{km}` GeoJSON'undaki özellik sırası | `nearest_km` (en yakın AÇIK hastaneye yol km, modelin mesafe tanımıyla; 1 m'ye yuvarlı), `nearest_hospital` (`hospitals` sırası), `beyond_R_cells`, `beyond_R_population` — erişim ısı haritası ve R dışında kalan bölgeler |
| `demand` | modelin talep hücreleri (`duals.demand_cell` ile aynı) | `cell_id`, `grid_index`, `population` (bu senaryonun nüfusu — 2030 ve GHS kaynağında GeoJSON'daki `pop` değil bu kullanılmalı), `demand_beds`, `service_hospital` (talebin en büyük payını alan hastane: hizmet bölgesi rengi), `service_share` (< 1 ise talep bölünmüş) |
| `candidates` | modelin aday hücreleri (`hospitals[].cand_pos` bu sıradaki konum) | `cell_id`, `grid_index`, `origin` (0 aday kuralı, 1 kapsama onarımı, 2 kullanıcı 'aç' kilidi), `open` (0/1), `locked_open`, `locked_closed` (konum listeleri) |

Aday kümesi parametreye bağlıdır (ör. 2030 senaryosunda 3 km'de 646 aday; GeoJSON'daki `aday` işareti yalnız varsayılan
parametrelerin 634 adayını gösterir): aday katmanı için `layers.candidates` kullanılmalı. `access.nearest_km` ile
`assignment.km` farklıdır: kapasite yüzünden bir hücre en yakın hastaneye değil başkasına atanmış olabilir. Boyut:
katmanlar 3 km'de ≈145 KB, 2 km'de ≈275 KB ham JSON (gzip'le ≈33 / ≈55 KB); tüm yanıt §10'da.

### `GET /api/jobs/{id}/events` (SSE)

`text/event-stream`; her olayın `id`'si artan bir sayıdır. Olay türleri:

| Olay | Veri |
|---|---|
| `state` | `{"state": "queued", "position": 0}`, `{"state": "running"}`, iptal istenince `{"state": "running", "cancel_requested": true, "message_tr": …}` |
| `progress` | `{t, phase, phase_tr, kind, lb, ub, gap, …}` — `t` işin başından beri saniye; `lb` yalnız küresel geçerli alt sınırlardan (Lagrange, tam MILP), `ub` denetimden geçmiş en iyi çözümden |
| `heartbeat` | 2 sn olay gelmezse: `{state, position, elapsed_s, run_s, progress, cancel_requested}` |
| `result` | son olay (iş bitti): `{state: "done" \| "cancelled", from_cache, job_id, type, cancel_reason_tr, error, result: {…}}` |
| `error` | son olay (sonuçsuz): `{state: "failed" \| "cancelled", job_id, type, message_tr, detail}` |

Aşamalar (`phase`): `baslangic` → `sezgisel` → `lagrange` → `cekirdek_mip` (LP yuvarlama, çekirdek MIP, pencere araması)
→ [`mip` uzun çözümlerde] → [`lagrange_2`] → `bitti`. Uzun (≥ 300 sn) çözümlerde tam MILP'in kök LP'si sırasında HiGHS
dakikalarca ilerleme bildirmeyebilir; o arada yalnız `heartbeat` olayları (`run_s` ile) gelir. Örnek (5 km, 8 sn süre
sınırı):

```
id: 2
event: state
data: {"state":"running"}

id: 8
event: progress
data: {"phase":"lagrange","phase_tr":"Alt sınır (Lagrange gevşetmesi)","kind":"lagrange","lb":381249806464.1609,"ub":409275402450.88794,"gap":0.06847613078846108,"iter":0,"t":1.697}

id: 15
event: progress
data: {"phase":"cekirdek_mip","phase_tr":"İyileştirme (LP yuvarlama, çekirdek MIP, pencere araması)","kind":"phase","lb":401964332386.8917,"ub":407417962558.4046,"gap":0.01338583634670031,"t":3.066}

id: 20
event: progress
data: {"phase":"bitti","phase_tr":"Bitti","kind":"bitti","lb":401964332386.8917,"ub":403017823718.2169,"gap":0.00261400679901884,"status":"optimal","t":4.143}

id: 21
event: result
data: {"state":"done","from_cache":false,"job_id":"be42d3734ae4412e","type":"solve","result":{…}}
```

Yeniden bağlanan istemci `Last-Event-ID` başlığıyla yalnız sonraki olayları alır (tarayıcı `EventSource` bunu
kendiliğinden gönderir). Bitmiş bir işe bağlanmak geçmişi ve son olayı hemen verir; iş bitmiş ve istemci son olayı
zaten almışsa (`Last-Event-ID` = son olay) yanıt **204**'tür ve `EventSource` yeniden bağlanmayı bırakır. İstemci yine de
`result`/`error` alınca `EventSource`'u kapatmalıdır. Tarayıcıda bağlantı hatası da `error` adıyla gelir: sunucunun
`error` olayının `data` alanı vardır, bağlantı hatasının yoktur.

**Önemli:** yerini yeni bir isteğe bırakan (`replace: true`) ya da iptal edilen iş de `result` olayıyla biter
(`state: "cancelled"`, sonuç motorun o ana dek bulduğu en iyi çözüm, `status: "durduruldu"`). Arayüz yalnız beklediği
işin (`job_id`) `done` sonucunu "optimum" diye göstermelidir. Örnek:

```js
let current = null;                                   // arayüzün beklediği son iş
async function solve(overrides) {
  const r = await fetch("/api/solve", {method: "POST", headers: {"content-type": "application/json"},
    body: JSON.stringify({grid_km: 3, overrides, client_id, replace: true})});
  const job = await r.json();
  if (!r.ok) return showError(job.message_tr);        // 422 (errors[]), 429, 413, 501 …
  current = job.job_id;
  if (job.from_cache) return show(job.result);
  const es = new EventSource(job.events_url);
  es.addEventListener("progress", e => { if (current === job.job_id) updateProgress(JSON.parse(e.data)); });
  es.addEventListener("result", e => {
    const d = JSON.parse(e.data);
    es.close();
    if (d.job_id !== current) return;                 // yerini yeni istek almış eski iş: gösterme
    if (d.state === "done") show(d.result);
    else showStopped(d.result, d.cancel_reason_tr || d.error?.message_tr);   // iptal / bekçi: en iyi çözüm
  });
  es.addEventListener("error", e => {
    es.close();
    if (e.data && JSON.parse(e.data).job_id === current) showError(JSON.parse(e.data).message_tr);
  });
}
```

### `GET /api/jobs/{id}` ve `POST /api/jobs/{id}/cancel`

`GET` iş özetini (`state`, `progress` — son ilerleme olayı —, `position`, `run_s`, `error`) ve bittiyse `result`'u
döndürür. İptal: kuyruktaki iş hemen `cancelled` olur; çalışan işe durdurma iletilir, motor en iyi çözümüyle
`status: "durduruldu"` döner ve iş `cancelled` durumunda sonucuyla biter (ölçümler §10). Değerlendirme ve sınır kartı
işleri de HiGHS'in kesme geri çağrısıyla durur (MIP'in kök LP'si sırasında kesme ancak kök LP bitince etkili olur:
2 km değerlendirmede ≈2,4 sn). İptal edilen ya da durdurulan sonuçlar önbelleğe yazılmaz. Bitmiş işi iptal etmek
etkisizdir (200).

### `POST /api/evaluate` (manuel mod)

```http
POST /api/evaluate
{"grid_km": 5, "hospitals": [{"cell_id": "5km_112_860", "class": "V-C"},
                             {"cell_id": "5km_109_861", "beds": 150},
                             {"cell_id": "5km_112_864"}, …, {"cell_id": "5km_94_899", "class": "IV-C"}],
 "duals": true}
```

(5 km varsayılan çözümün 69 hücresi; ilki V-C, ikincisi 150 yatak, sonuncusu IV-C sabit, diğerleri serbest — aşağıdaki
karşılaştırma bu istekten.)

- Her hastane: `cell_id`; isteğe bağlı `class` (`IV-C`, `V-B`, `V-C`, `V-D`) ve/veya `beds`. Yatak verilip sınıf
  verilmezse sınıf tebliğ eşiklerinden belirlenir (genel sınıflar önce). Verilmeyenleri model seçer (en iyi atama ve
  boyut; sınıf seçimi küçük bir MIP). Aday olmayan hücreler 'aç' kilidiyle aday kümesine eklenir
  (`result.added_candidates`).
- Yanıt sonucu: `status: "degerlendirildi"` (ya da `olurlu_degil` + `message_tr`), `objective_tl`, `hospitals`
  (`class_given`, `beds_given` işaretleriyle; `size` dahil), `assignment`, `costs`, `kpis`, `check`, `duals`, `layers`,
  `method`
  (`motor_evaluate` · `motor_evaluate_sabit_sinif` · `karisik`), ve **`comparison`**: aynı parametrelerle önbellekte
  çözülmüş (kilitsiz) en iyi optimumla karşılaştırma:

```json
"comparison": {"available": true, "diff_tl": 8022746793.75, "pct_vs_best": 0.019927, "pct_vs_lower_bound": 0.019927,
  "reference": {"ub_tl": 402610896144.25, "lb_tl": 402610896144.25, "gap": 0.0, "status": "optimal", "n_hospitals": 69,
                "time_limit_bucket_s": 900.0, "cache_entry": "…"},
  "message_tr": "Bu yerleşimin maliyeti optimumdan %1,99 (8,02 milyar TL) daha pahalı (karşılaştırılan çözüm: 69 hastane, 402,61 milyar TL; alt sınırla farkı %0; kanıtlanmış alt sınıra göre en çok %1,99 pahalı)."}
```

Önbellekte o parametrelerle çözüm yoksa `{"available": false, "message_tr": "… önce optimizasyonu çalıştırın."}`.
Karşılaştırma önbelleğe yazılmaz, her yanıtta güncel önbellekten hesaplanır. Bütçe modunda karşılaştırılan büyüklük
erişim maliyetidir.

### `POST /api/bounds`

Ek alanlar: `locks`, `time_limit` (varsayılan 20 sn; küme kapsama adımlarına %25 + %25, kalanı p_min'e),
`with_pmin` (varsayılan true). Sonuç `backend.model.bounds.bound_cards` biçimindedir (`max_hospitals_grid`,
`capacity`, `coverage_lscp`, `coverage_lscp_k_list`, `p_min {value, lower_bound, proven}`, `labels_tr`). Kanıtlanamayan
değerler "en az L, bulunan U" diye gösterilmelidir. Varsayılan parametrelerin uzun süreli (çoğu kanıtlı) kartları
`GET /api/bounds/{km}`'dedir. İptal her adımın içinde etkilidir (HiGHS kesmesi); `status: "durduruldu"` ve kesilen
adımlar `proven: false` ile döner. Yalnız R içindeki tüm adaylarla (k = 0) yapının kurulması kesilemez (2 km'de birkaç
sn).

## 5. Hatalar

Tüm hata yanıtları `{"message_tr": …}` taşır; doğrulama hataları (422) ayrıca `errors` listesini:

```json
{"message_tr": "Parametreler doğrulanamadı.", "errors": [
  {"loc": ["body", "overrides", "demand.beds_per_100"], "key": "demand.beds_per_100", "type": "bilinmeyen_parametre",
   "message_tr": "Bilinmeyen parametre: 'demand.beds_per_100'. Bunu mu demek istediniz: demand.beds_per_1000, demand.hb.admissions_per_1000, demand.mode?",
   "suggestions": ["demand.beds_per_1000", "demand.hb.admissions_per_1000", "demand.mode"]},
  {"loc": ["body", "overrides", "demand.beds_per_1000"], "key": "demand.beds_per_1000", "type": "aralik_disi", "value": -1,
   "message_tr": "'demand.beds_per_1000' = -1 modelin tanım kümesi dışında; izinli aralık: > 0.",
   "allowed": {"kind": "number", "min": 0, "max": null, "min_inclusive": false, "max_inclusive": true}, "ui_range": [2.0, 5.0]},
  {"loc": ["body", "overrides", "access.alpha"], "key": "access.alpha", "type": "arayuz_araligi_disi", "value": 4,
   "message_tr": "'access.alpha' = 4 arayüz aralığının [1; 3] dışında; aralık dışı değerler için allow_outside_range=true gönderin.",
   "allowed": {"kind": "number", "min": 0, "max": 5, "min_inclusive": false, "max_inclusive": true}, "ui_range": [1.0, 3.0]}]}
```

| `type` | Anlamı |
|---|---|
| `bilinmeyen_parametre` | anahtar kayıtta yok (`suggestions`: benzer anahtarlar) |
| `aralik_disi` | modelin tanım kümesi dışında (`allowed`) |
| `arayuz_araligi_disi` | kaydırıcı aralığı dışında (`ui_range`); `allow_outside_range: true` ile uyarıya döner |
| `tur_hatasi`, `secenek_disi` | yanlış tür / izinli seçenek dışında |
| `tutarsiz_parametre` | anahtarlar birlikte geçersiz (ör. en az yatak > en çok yatak) |
| `celiski` | üst düzey alan ile `overrides` çelişiyor |
| `bilinmeyen_hucre`, `kilit_celiskisi`, `tekrar`, `sinif_kapali` | hücre kimliği ızgarada yok / aynı hücre hem aç hem yasak / aynı hücre iki kez / sınıf kapalı |
| `missing`, `extra_forbidden`, `literal_error`, `greater_than`, … | istek şeması (Türkçe ileti, `field`) |

Parametre ve kilit hataları tek yanıtta birlikte döner. Hata yanıtındaki değerler JSON'a çevrilebilir tutulur
(64 bit dışı tamsayı ve sonsuz değer metin olarak). Diğer kodlar: **400** gövde okunamadı (geçersiz ya da çok derin iç
içe JSON) ya da geçersiz `Host` başlığı, **404** (iş, dosya, ızgara yok), **413** gövde 2 MB'tan büyük, **429** kuyruk
dolu (`Retry-After: 5`; toplam 8, istemci başına 4 bekleyen iş), **501** iş türü henüz kullanılamıyor (`explain`),
**500** beklenmeyen sunucu hatası (`{"message_tr": "Sunucu hatası: <tür>."}`; bağlantı açık kalır). Hepsi Türkçe
`message_tr` taşır.

## 6. Önbellek kuralları

- **Anahtar** = SHA-256(motor sürümü + etkili parametreler + kilitler [+ değerlendirmede hastaneler]). Motor sürümü
  `backend/model/*.py` ile ızgara `.npz` dosyalarının özetidir: motor kodu ya da ızgara verisi değişirse eski kayıtlar
  kullanılmaz ve açılışta silinir. Etkili parametreler `Params.effective_values()`'tır (ızgara, senaryo yılı, mod dahil;
  seçili moda göre kullanılmayan anahtarlar hariç); süre sınırı ve gap anahtara değil değişkeye girer.
- **Değişke** (çözüm): süre kovası (aşağı yuvarlanır: …, 10, 15, 20, 30, 45, 60, … sn), hedef gap, dual değerler.
  Önbellekteki kayıt isteği **karşılar** eğer en az aynı çabayla (süre kovası ≥, hedef gap ≤) çözülmüşse ya da durumu
  `optimal` ve ulaşılan gap istenen gap'ten küçükse; dual değerler istenmişse kayıtta olmalı. Birden çok kayıt uyarsa
  ulaşılan gap'i en küçük olan seçilir. Bu yüzden varsayılan 3 km isteği (15 sn, %1) önceden çözülmüş 1.800 sn'lik
  çözümü (fark %0,062) alır; 5 km'de kanıtlı optimum her gap isteğini karşılar.
- Yalnız başarılı ve denetimden geçmiş sonuçlar yazılır (`durduruldu` hariç). Boyut sınırı 300 MB / 500 kayıt, LRU
  (isabet kaydın zamanını tazeler). Bozuk ya da yarım dosyalar yok sayılır ve silinir; yazma atomiktir.
- `use_cache: false` önbelleği atlar (sonuç yine yazılır).

## 7. "En son istek kazanır", kuyruk ve iş durumları

Arayüz parametre değişikliklerinde `client_id` ve `replace: true` gönderir: aynı istemcinin aynı türdeki kuyruktaki
işleri hemen, çalışan işi ise motoru durdurarak iptal edilir (`replaced_by` yeni işi gösterir). Gruplar iş türüne
göredir (`solve` ve `evaluate` ayrı). Durumlar: `queued` → `running` → `done` | `failed` | `cancelled`. `position`
kuyruktaki işin önündeki iş sayısıdır (çalışan iş dahil).

## 8. Yeni iş türü (Faz 6: explain, sweep, pareto, sensitivity)

Altyapı değişmez: `backend/app/task_<ad>.py` yazılır ve içinde `register(JobType(name=…, request_model=…,
prepare=…, run=…))` çağrılır (`backend/app/tasks.py`). `prepare` sunucuda doğrular ve işçiye gidecek düz bir yük ile
önbellek anahtarını üretir; `run(ctx, payload)` işçide çalışır, `ctx.emit({...})` ile ilerleme gönderir,
`ctx.cancel_event`'i izler (motor fonksiyonlarına `cancel_event` olarak verilir: `solve`, `evaluate`, `bound_cards`,
`p_min`) ve JSON'a çevrilebilir bir sözlük döndürür. Modüller hem sunucuda hem işçide kendiliğinden yüklenir;
`POST /api/jobs/<ad>` hemen çalışır, istenirse kısa bir uç nokta (ör. `POST /api/sweep`) eklenebilir. İsteğe bağlı
kancalar: `satisfies`/`rank`/`summarize` (önbellek), `finalize` (sunucuda son işlem), `seed` (açılışta önbelleğe hazır
sonuç, ör. `precomputed/p_sweep_*`), `warmup` (işçi açılışında; ertelenmiş adımlar döndürebilir), `available`
(False → istek 501).

**`explain` taslağı** (`backend/app/task_explain.py`, şartname 7.3–7.4): istek modeli, doğrulama, önbellek anahtarı,
öğe öğe ilerleme, iptal ve sonuç biçimi hazır; Faz 6 yalnız üç hesap fonksiyonunu doldurur ve `available=False`'u
kaldırır. İstek:

```json
{"grid_km": 3, "overrides": {…}, "locks": {…},
 "hospitals": [{"cell_id": "3km_182_1436", "class": "IV-C", "beds": 53.1}, …],
 "what": ["hastane_kapat", "n_eksi_1", "n_arti_1"], "close": ["3km_182_1436"], "time_limit": null}
```

Sonuç: `{"ok", "status", "base": {objective_tl, n_hospitals}, "items": [{"kind": "hastane_kapat", "cell_id",
"feasible", "objective_tl", "delta_tl", "message_tr"}, {"kind": "n_eksi_1" | "n_arti_1", "p", "status",
"objective_tl", "opening_tl", "access_tl", "lb_tl", "gap", "delta_tl", "delta_opening_tl", "delta_access_tl",
"message_tr"}]}`. Ölçüm (Faz 4 incelemesi, 3 km, 89 hastane): bir hastane kapatılınca kalan 88'in değerlendirilmesi
olurlu değilse 0,12 sn ("34 talep hücresinin (1.323 kişi) R içinde açık hastanesi yok"), olurluysa 10,7 sn — tıklama
başına senkron hesaplanamaz, arka plan işi olmalı.

**Faz 6'ya bırakılanlar** (ek çözüm gerektirir): `explain` hesapları (N − 1 / N + 1, hastane kapatma), `sweep`
(p taraması, U eğrisi; varsayılan senaryo `precomputed/p_sweep_{3,5}km.json`'da), `pareto` (λ taraması), `sensitivity`
(tornado, ±%20).

**Çevrimdışı mod (Faz 5/8 notu):** sunucu kapalıyken `/api/precomputed/*` de kapalıdır. Varsayılan çözüm
(`precomputed/default_3km.json`), ızgara (`data/processed/grid/grid_3km_cells.geojson`) ve sınırlar
(`data/processed/web/*.geojson`) arayüz derlemesine gömülmeli (ya da bir service worker önbelleğe almalı).
Faz 5'te ikisi birden yapıldı: derleme bunları ve `/api/meta`, `/api/sources` kopyasını `backend/static/offline/`'a
yazar, `backend/static/sw.js` sayfa kabuğuyla birlikte önbelleğe alır (`frontend/build/offline.ts`;
`docs/FRONTEND.md` "Çevrimdışı").

## 9. Statik dosyalar

- `/basemap/…` → `data/basemap/` (PMTiles `application/vnd.pmtiles`, glif `.pbf` `application/x-protobuf`, simge
  `.json`/`.png`). Range isteği 206 (tek ya da çok aralık), aralık dışı 416. `Cache-Control: public, max-age=86400`.
  Yazı tipi klasörlerinde boşluk var: `/basemap/fonts/Noto%20Sans%20Regular/0-255.pbf`.
- `/` ve diğer yollar → `backend/static/` (Faz 5'te Vite derlemesi). `assets/` altındaki dosyalar
  `immutable`, `index.html` `no-cache`. Uzantısız bilinmeyen yol `index.html`'i döndürür; uzantılı eksik dosya 404.
  Arayüz henüz derlenmemişse `/` kısa bir Türkçe bilgi sayfası gösterir.
- Yol güvenliği: `..`, kodlanmış `%2e%2e`/`%2f`/`%5c`, mutlak yol, sürücü harfi, NUL ve gizli dosyalar reddedilir;
  çözülen yol kök dizinin altında değilse 404 (`tests/test_phase4_api.py`, `tests/test_phase4_server.py`).
- MIME türleri sabit tablodan gelir (Windows kayıt defterindeki yanlış `.js` türlerinden etkilenmez).

## 10. Ölçülen gecikmeler (API üzerinden, uçtan uca)

Ölçüm: `HASTANE_LATENCY=1 HASTANE_LATENCY_SERVERS=6 .venv/bin/python -m pytest -q -s tests/test_phase4_latency.py`
(her turda yeni bir `python -m backend.run` süreci ve **boş** önbellek dizini, gerçek HTTP). Sunucuların yarısında açılıştan
sonraki ilk çözüm varsayılan parametrelerle, yarısında `cost.fixed_per_hospital` = 300 milyon TL ile (yapıyı
değiştirmeyen tipik bir kaydırıcı değişikliği). 4 çekirdekli geliştirme kabı (Linux), boşta, 2026-10-23: Python 3.13
ile 12 sunucu (iki tur), Python 3.14 ile 6 sunucu. **Sunum dizüstü (Windows, Python 3.14) ölçülmedi** — Faz 8'de aynı
komutla ölçülmeli. Süreler istemcide, isteğin gönderilmesinden ilgili SSE olayının alınmasına kadar.

| Ölçüm | Python 3.13 (12 sunucu) | Python 3.14 (6 sunucu) |
|---|---|---|
| Sunucu açılışı: ilk HTTP yanıtı / çözücü hazır (boş önbellekle ilk açılış: 18 önceden çözülmüş sonuç önbelleğe yazılır, ≈0,35 sn; sonraki açılışlarda ≈0,05 sn) | 0,73–1,11 sn / 1,14–1,53 sn (yeniden sıralamadan sonraki 6 sunucu) | 0,81–0,91 sn / 1,22–1,54 sn |
| `POST /api/solve` yanıtı (iş kabul edildi, 202) | 6–16 ms | 7–12 ms |
| `POST /api/solve` önbellekten (varsayılan 3 km, sonuç gövdede, ≈0,48 MB JSON) | 62–85 ms | 68–81 ms |
| 3 km, **sunucu açıldıktan sonraki ilk** çözüm (`use_cache: false`): fark ≤ %1 | **3,26–5,10 sn** (12/12) | **3,34–5,13 sn** (6/6) |
| — aynı çözüm: istekten `result` olayına; son durum | 5,4–9,0 sn; 12/12 `optimal` (fark %0,46–0,50, 89 hastane) | 5,5–9,2 sn; 6/6 `optimal` |
| — ilk alt sınır | 1,63–3,00 sn | 1,64–3,00 sn |
| 3 km aynı istek tekrar (ılık) | 2,2–3,9 sn; %1'e 1,09–2,26 sn | 2,4–3,8 sn; %1'e 1,17–2,24 sn |
| 3 km ılık + parametre değişikliği (`cost.fixed_per_hospital` = 350 milyon TL) | 3,6–4,4 sn; %1'e 1,80–2,30 sn | 3,8–4,5 sn; %1'e 1,77–2,18 sn |
| 2 km, süre sınırı verilmeden (→ 60 sn), `cost.fixed_per_hospital` = 300 milyon TL | 14,2 / 15,5 sn, %1'e 10,6 / 11,9 sn, `optimal` | 14,5 sn, %1'e 11,0 sn, `optimal` |
| `POST /api/evaluate` 3 km, varsayılan çözümün 89 hücresi, sınıfları model seçer | 2,2–3,7 sn | 2,3–3,6 sn |
| — aynı küme, bir hastanenin sınıfı elle sabit (karışık yol) | 4,4–5,9 sn | 4,5–6,1 sn |
| — aynı istek önbellekten | 58–112 ms | 63–84 ms |
| SSE olay aralığı, ilk 3 km çözüm: ortanca / %95 / en büyük | 0,25–0,26 / 1,02–1,34 / 1,14–1,67 sn | 0,25–0,26 / 1,08–1,46 / 1,25–1,77 sn |
| Çekirdek MIP alt sürecinin çözüme başlama gecikmesi (yedek süreçle) | 0,12–0,23 sn | 0,10–0,21 sn |

İptal (gerçek sunucu; iş çalışmaya başladıktan belirtilen süre sonra `POST /api/jobs/{id}/cancel`; her biri tek ölçüm;
işçi hiçbirinde yeniden başlamadı):

| İş | İptal anı | İptalden `cancelled` sonuca, 3.13 / 3.14 |
|---|---|---|
| `evaluate` 2 km (110 hastane, sınıflar serbest, ≈12–15 sn sürer) | 2 sn / 6 sn | 0,84 / 0,11 sn · 0,32 / 1,78 sn |
| `bounds` 2 km (`time_limit` 120) | 3 sn | 9,3 sn · 7,2 sn (k = 0 yapısı ve küme kapsama küçültmesi kesilemez) |
| `bounds` 2 km | 8 sn | 3,9 sn · 1,9 sn |

Sonuç boyutu (API yanıtı, ham / gzip): 3 km 481 / 96 KB (katmanlar 145 KB ham), 2 km 837 / 161 KB (katmanlar
275 KB ham). SSE `result` olayı sıkıştırılmaz (yerel bağlantıda sorun değil).

Önceki ölçümle karşılaştırma (Faz 4 incelemesi, düzeltme öncesi): sunucu açıldıktan sonraki ilk 3 km çözümü %1'e
4 ölçümün 3'ünde 11,5–12,3 sn'de ulaşıyordu; doğrudan motor çağrısıyla (yeni süreç, sabit maliyet 300 milyon TL) tohumsuz
2/2 çalıştırma 15 sn'de %1'e ulaşamadı (%1,07 / %1,08), varsayılan çözümle tohumlu 2/2 çalıştırma %1'e 3,9 sn'de ulaştı
(bu düzeltmenin ölçümü, `task_solve.py` notu). Hazır senaryoların canlı denetimi (`pipeline/34_scenarios.py live`,
önbelleksiz, önerilen süre sınırı, işçi önceden çözülmüş sonuçlarla tohumlu): 18 senaryo × ızgaradan 17'si fark ≤ %5
ile çözüldü (15'i `optimal`; 5 ve 3 km düşük bütçe `sure_doldu`, fark %2,46 / %3,50); yalnız 2 km düşük bütçe 60 sn'de
%5,47 farkla bitti (olurlu; erişim 31,67 milyar TL) — sonuçlar `precomputed/scenarios.json` → `live_check` (2 km satırları
yeniden çözümden sonra yeniden ölçüldü), `GET /api/meta` → `scenarios[].live_grids`.
