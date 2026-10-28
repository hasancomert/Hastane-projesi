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
| `GET /api/precomputed/{ad}` | yalnız beyaz liste: `default_2km`, `default_3km`, `default_5km`, `p_sweep_3km`, `p_sweep_5km`, `scenarios` (senaryo dizini), `scenario_<id>_<km>km` ve Faz 6 açıklamaları `explain_[<senaryo>_]<km>km`, `pareto_<km>km`, `tornado_<km>km` (§11) |
| `GET /api/literature` | kaynakça (`data/reference/literatur.yaml`, doğrulanmış künyeler) JSON olarak (§11) |
| `POST /api/solve` | çözüm işi başlatır (202) ya da önbellekten döner (200) |
| `POST /api/evaluate` | manuel mod: elle seçilen hastaneler için değerlendirme işi |
| `POST /api/bounds` | verilen parametrelerle sınır kartları işi (süre sınırlı) |
| `POST /api/explain` | açıklama işi: "bu hastane kapatılırsa", N − 1 / N + 1, "neden bu boyut?", gölge fiyatlar, model nedenleri (§11) |
| `POST /api/sweep` | p taraması (U eğrisi), arka planda, nokta nokta ilerlemeli (§11) |
| `POST /api/pareto` | λ taraması: açılış maliyeti – ortalama mesafe (§11) |
| `POST /api/sensitivity` | tornado: önemli parametreler ±%20 (§11) |
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
- `job_types`: `{ad: {description_tr, url, available, unavailable_tr}}` (Faz 6'dan beri yedi tür de `available: true`).
- `explain` (Faz 6): `{tornado_params: [{id, keys, label_tr, unit, status, base}], tornado_delta, pareto_factors,
  pareto_lambda_key, precomputed_patterns}`; `grids[].precomputed_explanations`: o ızgaranın açıklama dosyaları.
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
dolu (`Retry-After: 5`; toplam 8, istemci başına 4 bekleyen iş), **501** iş türü kullanılamıyor (`available: false`
olan tür; Faz 6'dan beri yok),
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

**`explain` taslağı (Faz 4; Faz 6'da dolduruldu — güncel sözleşme §11)** (`backend/app/task_explain.py`, şartname
7.3–7.4): istek modeli, doğrulama, önbellek anahtarı, öğe öğe ilerleme, iptal ve sonuç biçimi hazır; Faz 6 yalnız üç
hesap fonksiyonunu doldurur ve `available=False`'u kaldırır. İstek:

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
(tornado, ±%20). → Faz 6'da yapıldı: §11.

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

## 11. Faz 6 — açıklama işleri ("neden bu çözüm optimum?")

Dört iş türü (şartname 7.3–7.9) ve kaynakça ucu. Hesaplar motordadır (`backend/model/explain.py`; sunucunun da
kullandığı hafif parçalar — sabitler, Türkçe biçim, farklar ve özet cümleleri — `backend/model/explain_common.py`),
iş türleri `backend/app/task_explain.py`, `task_sweep.py`, `task_pareto.py`, `task_sensitivity.py`. Hepsi
`POST /api/<tür>` ve `POST /api/jobs/<tür>` ile çalışır; ilerleme, iptal, "en son istek kazanır" (`client_id` +
`replace`) ve önbellek kuralları §4–§7 ile aynıdır. Her sayı çözümden, çözücü sınırlarından ya da kayıttan gelir.

### 11.1 Ortak kavramlar

- **Temel çözüm** (`hospitals`): açıklanan çözümün hastaneleri, sonuçtaki gibi `[{cell_id, class, beds}]`. Sunucu bunu
  verilen parametrelerle yeniden değerlendirir (sınıf ve yatak verilmişse yalnız atama LP'si: çözümün kendisi).
- **`base_bounds`** (`{ub_tl, lb_tl}`, isteğe bağlı): çözümün `result.bounds` değerleri. `lb_tl` verilmezse sunucu
  önbellekteki aynı problemin (parametreler + kilitler) solve kayıtlarının en büyük alt sınırını kullanır
  (`base.lb_from: "onbellek"`); hiçbiri yoksa yalnız noktaların kendi alt sınırları kullanılır.
- **"Fark gap içinde"** (`fark_gap_icinde`, Faz 3 raporu §8 ile aynı kural): bir karşılaştırma noktasının ALT sınırı
  temel çözümün bulunan değerini aşmıyorsa (`lb ≤ ub_temel`) iki çözüm bu sınırlarla ayırt edilemez. Temel çözüm toplam
  modundaysa onun alt sınırı her sabit p probleminin de alt sınırıdır (serbest problem gevşetmedir): `lb = max(kendi,
  temel)`; `lb_own_tl` ayrıca saklanır. Gerçek fark `[fark_lo_tl, fark_hi_tl] = [lb(nokta) − ub_temel, ub(nokta) −
  lb_temel]` aralığındadır. Arayüz bu durumda "fark gap içinde" yazmalı, kesinlik uydurmamalıdır.
- **Önceden hesaplananlar** (`pipeline/35_explanations.py`, §11.6): aynı parametre özeti (`params.hash()`) + aynı kilitler
  (+ explain'de aynı temel çözüm) için istenen öğelerin hepsi dosyadaysa yanıt **sunucuda, kuyruğa girmeden** 200 ile
  döner (`from_cache: true`, `result.precomputed: {name, generated_at, …}`); eksik öğeler işçide hesaplanır.
  Varsayılan parametrelerde arayüz dosyaları doğrudan da okuyabilir: `GET /api/precomputed/explain_3km` … (çevrimdışı
  yedeğe gömmek için de bunlar kullanılmalı).
- **İlerleme olayları** (`progress`): `{phase, phase_tr, done, total, …}`; explain'de her öğe bitince `last` (kısa özet:
  kind, cell_id, p, feasible, reason, delta_tl, fark_gap_icinde), taramalarda her nokta bitince `point` / `run`
  (grafiği nokta nokta çizmek için yeterli alanlar). Bir noktanın içindeki motor çözümü sürerken `inner: {lb, ub, gap}`
  alanlı seyrek olaylar gelir. Son olay `phase: "bitti"`.
- **İptal**: o ana dek tamamlanan öğe/noktalar `status: "durduruldu"` sonucuyla döner, iş `cancelled` olur; iptal edilen
  sonuç önbelleğe yazılmaz. Motor çözümleri iptali HiGHS kesme geri çağrısıyla izler (§4).
- **Süre**: N ± 1, tarama noktası, Pareto noktası ve tornado çalışması başına `time_limit` (yoksa ızgaranın önerilen
  süresi: 3 ve 5 km 15 sn, 2 km 60 sn); hedef `gap` explain/sweep'te 1e-3, Pareto/tornado'da 2e-3 (süre dolana dek
  iyileştirir). Bekçi süresi öğe/nokta sayısıyla ölçeklenir.

### 11.2 `POST /api/explain`

```http
POST /api/explain
{"grid_km": 3, "overrides": {}, "hospitals": [{"cell_id": "3km_182_1436", "class": "IV-C", "beds": 53.0995}, …],
 "what": ["hastane_kapat", "n_eksi_1", "n_arti_1", "boyut", "golge_fiyat", "model"],
 "close": ["3km_141_1460"], "close_all": false, "exact": false, "reoptimize": false,
 "time_limit": null, "gap": null, "base_bounds": {"ub_tl": 381314289391.98, "lb_tl": 381077855962.55},
 "client_id": "arayuz-1"}
```

| Alan | Anlamı |
|---|---|
| `what` | öğe türleri (en az 1): `hastane_kapat`, `n_eksi_1`, `n_arti_1`, `boyut`, `golge_fiyat`, `model` |
| `close` / `close_all` | kapatılacak hastaneler (her biri ayrı öğe) / çözümün tüm hastaneleri |
| `exact` | kapatmada tüm hastanelerde sınıf seçimli kesin değerlendirme (3 km'de hastane başına ortanca 8,8 sn, en çok 13,8 sn; hızlı tahmin ≈1 sn) |
| `reoptimize` | kapatmada ayrıca j yasakken tam yeniden çözüm (öğe başına `time_limit`) |

Sonuç: `{ok, status ("tamam" | "durduruldu"), grid_km, mode, params_hash, base: {objective_tl, n_hospitals, ub_tl,
lb_tl, lb_from, gap, opening_tl, access_tl, pop_weighted_mean_km, count_by_class, mode}, items: […],
closure_summary (≥ 2 kapatma), p_sweep (varsayılan parametrelerde p taramasının künyesi ve 'düz dip' özeti),
precomputed?, warnings, times}`. Öğeler (her biri `kind` taşır; Faz 4 taslağındaki alanlar korunur):

**`hastane_kapat`** (şartname 7.4). Hızlı tahmin = evaluate(açık küme − j): konumlar sabit, kalan hastanelerde yeniden
atama + yeniden boyutlandırma; kapatılan hastaneye kuş uçuşu en yakın 16 hastanede sınıf da yeniden seçilir (küçük
MIP), diğerlerinde sınıf sabit. `exact: true` ile tüm hastanelerde sınıf seçimi. İkisi de kalan hastanelerle OLURLU bir
çözümdür: konumlar yeniden optimize edilirse artış ancak daha küçük olabilir — `delta_tl` gerçek artışın **üst
sınırıdır** (`upper_bound: true`; `tests/test_phase6_explain.py` oyuncak örneklerde kesin MILP ile denetler).
Alanlar: `cell_id, district, class, beds, feasible, reason, method ("hizli_komsu_sinif" | "kesin_sinif_secimi"),
objective_tl, delta_tl, delta_pct, quick_objective_tl, opening_tl, access_tl, delta_opening_tl, delta_access_tl,
base_objective_tl, base_gap_tl, fark_gap_icinde` (artışın üst sınırı bile temel çözümün gap'inden küçükse `true`: bu
hastanenin gerekliliği kanıtlanamıyor), `displaced_population, displaced_beds, mean_km_before, mean_km_after`
(kapatılan hastanenin hastalarının ortalama yolu), `absorbers` (hastaları devralanlar: `cell_id, district,
absorbed_population, absorbed_beds, share, class_before/after, beds_before/after`), `resized` / `n_resized` /
`n_class_changed`, `message_tr`, isteğe bağlı `reopt` (`status, objective_tl, lb_tl, lb_own_tl, gap, delta_tl,
delta_lo_tl, delta_hi_tl, n_hospitals, opened, closed, improvement_vs_quick_tl, fark_gap_icinde, message_tr`).
Olurlu olmayan kapatma: `feasible: false`, `reason`:
`"R"` (bazı talep hücrelerinin R içinde hiç açık hastanesi kalmaz — hastaneyi R kısıtı zorunlu kılıyor; `uncovered:
{cells, population, districts, nearest_other_km_max, nearest_other_km_mean, within_R_geometric}`),
`"k_listesi"` (hücrelerin R içinde başka hastanesi var ama modelin en yakın k = 50 aday listesinin dışında),
`"kapasite"`, `"kilit"`, `"sayi"`.

Örnek (5 km varsayılan çözüm, `exact`; `precomputed/explain_5km.json`): 5km_84_876 (Polatlı) kapatılırsa toplam
maliyet en çok 34,8 milyon TL artar (açılış −84,1 milyon, erişim +118,9 milyon TL); hastalarının (29.625 kişi) %89'u
5km_85_876'ya gider, o hastane IV-C 199 yataktan V-B 292,3 yatağa geçer; bu hastaların ortalama yolu 5,3 → 8,7 km.

**`n_eksi_1` / `n_arti_1`** (şartname 7.3, "neden N?"). sabit_p modunda N ∓ 1 hastaneyle en iyi çözüm; başlangıç
mevcut çözümden tek tek kapatma/açma ile türetilir (yaklaşık modelin en iyi 8 hamlesi kesin değerlendirilir). Varsayılan
parametrelerde (kilitsiz) `precomputed/p_sweep_{3,5}km.json`'un uzun çözülmüş noktaları kullanılır (`source:
"p_taramasi"`; canlı: `"canli"`). Alanlar: `p, ok, status, source, ub_tl (= objective_tl), lb_tl, lb_own_tl, lb_source,
gap, opening_tl, access_tl, total_tl, count_by_class, pop_weighted_mean_km, fark_tl (= delta_tl), fark_lo_tl,
fark_hi_tl, fark_gap_icinde, delta_opening_tl, delta_access_tl, delta_mean_km, hospitals, closed_vs_ref,
opened_vs_ref, message_tr`. Bütçe modunda tanımsız (422 `mod_uyumsuz`).

**`boyut`** (şartname 7.5). `hospitals: [{cell_id, district, class, beds, class_min_beds, class_max_beds,
assigned_beds, population, occupancy, fixed_tl, per_bed_tl, opening_tl, bed_value_tl, sizing_bed_price_tl,
next_class: {class, beds, opening_tl, per_bed_tl, jump_tl, extra_beds}, prev_class, same_beds_next_class_tl,
sole_cover_cells, r_forced_cells, r_forced_population, reason, reason_tr}]`, `counts`, `at_bound_beds` (ör. 3 km'de
`{"199": 40, "399": 20, "50": 15}`), `summary_tr`, `note_tr`. Neden kodları: `ust_esik` (sınıfın üst sınırında; bir
yatak daha sınıfı değiştirir, `next_class.jump_tl` sıçramayı verir: 3 km'de 199 → 200 yatak +291,5 milyon TL),
`ust_sinir` (izinli en büyük boyut), `alt_sinir` (sınıfın en az yatağında; boş yatak ya da çevreden doldurulan yatak),
`ic_denge` (sınıf içinde; 1 ek yatağın erişim değeri = yatak başı maliyet, LP'nin denge koşulu).

**`golge_fiyat`** (şartname 7.6). Açık hastaneler ve sınıfları sabitken atama/boyutlandırma LP'sinin dual değerleri:
`demand_cell, marginal_cost_tl, marginal_cost_per_person_tl` (talep hücresi sırası = `layers.demand`),
`bed_value_tl` + `hospital_cells` (hastane sırası), `per_person: {p5, p25, median, p75, p95, pop_weighted_mean,
mean_within_10km, mean_beyond_10km, annual_equiv, top_cells}`, `bed_value: {min, median, max, n_zero, n_full,
median_full, per_bed_cost_by_class, top}`, `interpretation_tr`, `caveat_tr` (dejenere LP'de dual değerler tek
değildir; tamsayı programın gölge fiyatı değildir; yalnız marjinal yorum).

**`model`**: model düzeyindeki nedenler, sayılar kayıttan ve çözümden: `classes` (sınıf başına `fixed_tl`,
`per_bed_tl`, `unit_cost_tl_m2`, `m2_per_bed`, `n_chosen`), `splits` (aynı yatağı tek büyük ↔ çok küçük hastanede
açmanın maliyeti: 398 yatak V-B 7,19 ↔ 2 × IV-C 7,14 milyar TL; 995 yatak V-C 19,13 ↔ 5 × IV-C 17,85; 3.980 yatak
V-D 100,54 ↔ 20 × IV-C 71,41), `at_upper_bound`, `at_lower_bound`, `r_forced` (R'nin zorladığı hastaneler: hücre,
ilçe, tek başına kapsanan nüfus, doluluk), `k_list_forced`, `occupancy`, `reasons: [{id, text_tr}]` (`yatak_basi_artiyor`,
`sabit_kisim`, `bolme`, `esik_yigilma`, `R_zorunlu`, `sehir_hastanesi`, `uzmanlasma`), `summary_tr`.

`closure_summary`: `n, n_feasible, n_r_forced, r_forced_cells, n_k_list_forced, k_list_forced_cells, n_within_gap,
delta_min_tl, delta_median_tl, delta_max_tl, lowest[5], highest[5], summary_tr`.

### 11.3 `POST /api/sweep` (U eğrisi, şartname 7.7)

```http
POST /api/sweep
{"grid_km": 3, "overrides": {…}, "hospitals": [… temel çözüm …], "p_values": [83, 85, 87, 88, 89, 90, 91, 93],
 "half_width": 6, "step": 1, "time_limit": 15, "base_bounds": {"ub_tl": …, "lb_tl": …}}
```

`p_values` yoksa `n ± half_width` (adım `step`); en çok 60 nokta. Noktalar `|p − n|` artan sırayla çözülür, her biri en
yakın çözülmüş p'nin çözümünden tek tek kapatma/açma ile başlar. Varsayılan parametrelerde `p_sweep_{3,5}km.json`'daki
p'ler çözülmez (`source: "onceden"`); hepsi dosyadaysa ve `base_bounds.ub_tl` verilmişse yanıt kuyruğa girmez.
Temel çözümün kendisi p = n noktasıdır (`source: "temel_cozum"`, `referans: true`). Sonuç: `{ok, status, points:
[{p, ok, status, source, ub_tl, lb_tl, lb_own_tl, lb_source, gap, opening_tl, access_tl, total_tl, count_by_class,
pop_weighted_mean_km, fark_tl, fark_lo_tl, fark_hi_tl, fark_gap_icinde, referans, delta_opening_tl, delta_access_tl,
hospitals, …}], reference, best_p, best_ub_tl, plateau_p, summary_tr, precomputed?, times}`. Bütçe modunda 422.

### 11.4 `POST /api/pareto` (şartname 7.8)

```http
POST /api/pareto
{"grid_km": 3, "overrides": {…}, "factors": [0.25, 0.5, 1, 2, 4], "hospitals": [… mevcut çözüm …],
 "base_bounds": {…}, "time_limit": 15}
```

Erişim ağırlığı λ = PV · v · gidiş-dönüş · (c + zaman değeri / hız), kişi başı başvuru sayısı `v`'ye
(`access.hospital_visits_per_capita`) doğrusal bağlıdır: `v · f` tam olarak `λ · f` verir (yeni parametre yok). Her f
için toplam modunda çözüm. Noktalar: `{factor, lambda_tl_per_person_km, n_hospitals, count_by_class, opening_tl,
access_scaled_tl (ölçekli amaçtaki erişim), access_tl (= access_scaled_tl / f, ASIL λ ile), total_tl (= açılış +
asıl erişim), pop_weighted_mean_km, share_within_10km, max_km, ub_tl, lb_tl, gap (ölçekli problemin), dominated,
source ("mevcut_cozum" | "canli"), hospitals, classes, time_s}`. `factors` yoksa 0,25 … 4 (12 nokta); 1 her zaman
eklenir ve `hospitals` verilmişse çözülmeden mevcut çözümle doldurulur (eğri üzerinde işaretlenecek nokta). Asıl toplam
f = 1'de en küçük olmalıdır (her nokta asıl problem için de olurludur): `current_is_min_total`,
`current_total_gap_tl`, `best_total_factor`, `summary_tr`. Bütçe modunda 422.

### 11.5 `POST /api/sensitivity` (tornado, şartname 7.9)

```http
POST /api/sensitivity
{"grid_km": 3, "overrides": {…}, "keys": ["cost.fixed_per_hospital", "cost.unit"], "delta": 0.2,
 "hospitals": [… temel çözüm …], "base_bounds": {…}, "time_limit": 15}
```

Satırlar (`GET /api/meta → explain.tornado_params`; etiketler kayıttan): `cost.fixed_per_hospital`,
`cost.core_area_m2`, `cost.m2_per_bed.general`, `cost.unit` (IV-C, V-B, V-C, V-D birim maliyetleri birlikte),
`cost.equipment_ratio`, `cost.infra_ratio`, `cost.vat_rate`, `demand.beds_per_1000`,
`access.hospital_visits_per_capita`, `access.discount_rate`, `access.horizon_years`, `access.value_of_time_tl_per_h`,
`access.cost_tl_per_km`, `access.speed_kmh`, `access.circuity_scale`, `access.max_distance_km` (son ikisi yapıyı
değiştirir). Her satır için iki çalışma — taban × (1 − δ) ve × (1 + δ); modelin tanım kümesi dışına çıkan değer sınıra
çekilir (`clipped`) — AYNI süre sınırı, AYNI gap ve AYNI sıcak başlangıçla (temel çözüm o parametrelerle yeniden
değerlendirilir; olurlu değilse çözücünün sezgiseli; `warm_start`). `hospitals` verilmezse temel çözüm de aynı
ayarlarla çözülür. Sonuç: `{ok, status, delta, base: {objective_tl, lb_tl, n_hospitals, …}, rows: [{id, keys,
label_tr, unit, status, base, low, high, swing_pct, range_pct}] (en büyük etkiden küçüğe), summary_tr}`; `low`/`high`:
`{id, side ("dusuk" | "yuksek"), factor, values, clipped, ok, status, objective_tl, lb_tl, gap, n_hospitals,
count_by_class, opening_tl, access_tl, pop_weighted_mean_km, delta_tl, delta_pct, delta_lo_tl, delta_hi_tl,
delta_lo_pct, delta_hi_pct, sign_certain, delta_n, warm_start, hospitals, time_s}`. Gerçek fark
`[L(çalışma) − U(temel), U(çalışma) − L(temel)]` aralığındadır; aralık sıfırı içeriyorsa `sign_certain: false`.
Hastane sayısındaki küçük değişimler U eğrisinin düz dibinde gap içinde kalabilir (3 km varsayılanda p = 82–96 n* =
89'dan ayırt edilemiyor).

### 11.6 Önceden hesaplananlar (`pipeline/35_explanations.py`)

```
.venv/bin/python pipeline/35_explanations.py all --grids 3,5     # explain (tüm senaryolar) → pareto → tornado
.venv/bin/python pipeline/35_explanations.py reopt --grids 3     # kapatmalara "j yasakken yeniden çözüm" ekler
```

Kaldığı yerden sürer (öğe sonuçları `cache/precompute_35/*.jsonl`, git dışı). Dosyalar iş türlerinin sonuç
biçimindedir ve `settings` alanında süre/gap ayarları, her çözümün kendi sınırları kayıtlıdır. Türkçe cümleler
saklanan sayılardan yeniden yazılır (`explain_common`, `closure_message`): metin düzeltmesi yeniden hesap gerektirmez,
`all` komutunu yeniden çalıştırmak yeter (denetim noktalarından, birkaç dakika).

Üretilen dosyalar (2026-10-23/24, geliştirme kabı, 4 çekirdek, başka ağır iş yokken; `all` 4.220 sn):
`explain_{3,5}km` + beş hazır senaryonun `explain_<senaryo>_{3,5}km` dosyaları (12 dosya, açıklama aşaması 32 dk; 3 km
varsayılan kesin kapatmalar 8,7 dk), `pareto_3km` (7,6 dk), `pareto_5km` (3,2 dk), `tornado_3km` (20,4 dk),
`tornado_5km` (7,3 dk); `reopt --grids 3` (kesin kapatma + 20 sn yeniden çözüm, hastane başına ≈35 sn) ayrıca. 5 km'de `reopt`
çalıştırılmadı (süre bütçesi).

### 11.7 Önceden hesaplanan sonuçlar ve ölçümler (geliştirme kabı, 2026-10-23)

Ayarlar dosyaların `settings` alanında: kapatmalar varsayılan senaryoda kesin (`exact`, sınıf seçimli MIP en çok 30 sn),
senaryolarda hızlı tahmin; senaryolarda N ± 1 canlı (3 km 45 sn, 5 km 20 sn, hedef gap 1e-3), varsayılanda p
taramasından. Para milyar/milyon TL; "gap içinde" = artışın üst sınırı temel çözümün gap'inden (ub − lb) küçük.

| Dosya | hastane · temel U · gap | R'nin zorladığı / k listesi / olurlu kapatma | kapatma Δ: en az – ortanca – en çok (milyon TL) · gap içinde | N − 1 · N + 1 (fark, gap içinde?) |
|---|---|---|---|---|
| `explain_3km` | 89 · 381,314 · %0,062 | 22 / 3 / 64 | 3,9 – 340,2 – 1.136,2 · 21 | 88: +3,9 (evet) · 90: +25,9 (evet) — p taraması |
| `explain_5km` | 69 · 402,611 · %0 (kanıtlı) | 24 / 0 / 45 | 15,6 – 689,0 – 3.163,2 · 0 | 68: +8,3 (hayır, kanıtlı en az +4,8) · 70: +49,1 (hayır, en az +46,3) |
| `explain_dusuk_butce_3km` (erişim) | 89 · 35,421 · %1,701 | 22 / 3 / 64 | 87,5 – 341,4 – 1.120,3 · 50 | bütçe modunda tanımsız |
| `explain_erisim_oncelikli_3km` | 92 · 406,588 · %0,080 | 23 / 2 / 67 | 20,5 – 535,3 – 2.368,6 · 18 | 91: −20,4 · 93: −8,1 (ikisi de gap içinde; canlı çözüm temelden ucuz: temel kanıtlı optimum değil) |
| `explain_sadece_genel_3km` | 89 · 381,314 · %0,128 | 22 / 3 / 64 | 3,9 – 340,2 – 1.136,2 · 44 | 88: +115,5 · 90: +31,0 (gap içinde) |
| `explain_sehir_serbest_3km` | 89 · 381,314 · %0,090 | 22 / 3 / 64 | 3,9 – 340,2 – 1.136,2 · 33 | 88: +115,5 · 90: +31,0 (gap içinde) |
| `explain_nufus_2030_3km` | 90 · 390,655 · %0,153 | 23 / 2 / 65 | 59,9 – 329,2 – 1.150,0 · 50 | 89: +69,9 · 91: −59,4 (gap içinde) |
| `explain_dusuk_butce_5km` (erişim) | 75 · 65,871 · %1,127 | 25 / 0 / 50 | 97,5 – 1.087,0 – 4.443,0 · 20 | — |
| `explain_erisim_oncelikli_5km` | 70 · 436,630 · %0,010 | 23 / 0 / 47 | 37,4 – 1.047,2 – 5.392,4 · 1 | 69: +37,4 · 71: +8,5 (gap içinde) |
| `explain_sadece_genel_5km` | 69 · 402,611 · %0,010 | 24 / 0 / 45 | 15,6 – 689,0 – 3.239,1 · 2 | 68: +8,3 · 70: +49,1 (gap içinde) |
| `explain_sehir_serbest_5km` | 69 · 402,611 · %0,009 | 24 / 0 / 45 | 15,6 – 689,0 – 3.239,1 · 2 | 68: +8,3 · 70: +49,1 (gap içinde) |
| `explain_nufus_2030_5km` | 68 · 411,798 · %0,009 | 23 / 0 / 45 | 20,5 – 739,2 – 3.244,1 · 1 | 67: +20,5 · 69: +38,2 (gap içinde) |

Okuma notları: (1) 3 km'de R'nin zorladığı 22 hastanenin kapatılması bazı hücrelerin R = 30 km içinde hiç hastanesi
kalmaması demek (ör. Şereflikoçhisar: 34 hücre, 1.323 kişi, kalan en yakın hastane 45,3 km; Nallıhan'da üç hastane);
3'ünde engel yalnız k = 50 aday listesi. (2) Hiçbir hastanenin tek başına kapatılması maliyeti düşürmüyor, ama 3 km'de
21 hastanede artışın üst sınırı bile çözücü gap'inden (236,4 milyon TL) küçük: bunların gerekliliği kanıtlanamıyor.
(3) Canlı N ± 1 çözümlerinin farkları (45 sn) çözücü gürültüsü içinde: ör. `sadece_genel` 3 km temel çözümü varsayılanla
aynı, canlı N − 1 farkı +115,5 milyon TL, p taramasınınki +3,9 milyon TL; hepsi "gap içinde" işaretli.
(4) Kesin ↔ hızlı kapatma tahmini (3 km varsayılan, 64 olurlu kapatma): 59'unda aynı, 5'inde hızlı tahmin daha yüksek
(ortalama fark 2,6, en çok 109,4 milyon TL). Süreler: kesin kapatma (hızlı + kesin) hastane başına ortanca 8,8 sn (en
çok 13,8; 3 km), 3,3 sn (5 km); hızlı tahmin 3 km senaryolarında ortanca 0,9–1,3 sn (en çok 3,3–7,1 sn; bütçe modunda
2,0 sn), 5 km'de 0,2–0,6 sn.

**Pareto** (`pareto_{3,5}km.json`; nokta başına 3 km 45 sn, 5 km 20 sn, hedef gap 2e-3; f = 1 noktası önceden çözülmüş
varsayılan çözüm). Açılış maliyeti λ ile artıyor, ortalama yol azalıyor (her iki ızgarada her adımda); asıl λ ile
toplam f = 1'de en küçük (3 km'de f = 0,85 noktası 381,315 ile aynı, gap içinde). Hastane sayısı tekdüze değil: erişim
ucuzlayınca model yatağı daha küçük sınıflara bölüyor (3 km f = 0,25: 76 IV-C + 16 V-B = 92 hastane; f = 1: 67 + 22 =
89; 5 km f = 0,25: V-C hiç yok, f = 1: 8 V-C).

| f (λ çarpanı) | 3 km: hastane · açılış (milyar TL) · ort. yol (km) · asıl toplam | 5 km: hastane · açılış · ort. yol · asıl toplam |
|---|---|---|
| 0,25 | 92 · 343,46 · 3,489 · 382,690 | 77 · 345,34 · 6,096 · 413,883 |
| 0,35 | 89 · 343,62 · 3,455 · 382,474 | 72 · 347,76 · 5,360 · 408,023 |
| 0,5 | 88 · 344,59 · 3,290 · 381,586 | 73 · 349,73 · 4,903 · 404,873 |
| 0,7 | 88 · 344,75 · 3,263 · 381,442 | 70 · 352,34 · 4,552 · 403,525 |
| 0,85 | 89 · 345,26 · 3,206 · 381,315 | 67 · 354,74 · 4,265 · 402,700 |
| **1 (mevcut)** | **89 · 345,28 · 3,205 · 381,314** | **69 · 355,83 · 4,160 · 402,611** |
| 1,2 | 89 · 347,08 · 3,059 · 381,481 | 70 · 356,77 · 4,084 · 402,694 |
| 1,5 | 91 · 348,16 · 2,987 · 381,747 | 70 · 356,93 · 4,073 · 402,737 |
| 2 | 93 · 349,88 · 2,901 · 382,506 | 70 · 358,89 · 3,976 · 403,597 |
| 2,5 | 96 · 352,00 · 2,810 · 383,603 | 72 · 360,98 · 3,888 · 404,703 |
| 3 | 101 · 354,88 · 2,716 · 385,420 | 75 · 366,61 · 3,710 · 408,333 |
| 4 | 105 · 358,85 · 2,616 · 388,273 | 78 · 370,46 · 3,608 · 411,031 |

Ölçekli problemlerin gap'i 3 km'de %0,075–0,224, 5 km'de %0,061–0,160 (noktaların `gap` alanı).

**Tornado** (`tornado_{3,5}km.json`; çalışma başına 3 km 40 sn, 5 km 15 sn, hedef gap 2e-3, sıcak başlangıç temel
çözüm; temel = önceden çözülmüş varsayılan çözüm). Toplam maliyet değişimi (%) · hastane sayısı değişimi, 3 km
(temel 381,314 milyar TL, 89 hastane); çalışmaların gap'i %0,14–0,41, 32 çalışmanın hepsinde farkın işareti kesin
(gerçek fark aralığı sıfırı içermiyor):

| Parametre (×0,8 / ×1,2) | −%20 | +%20 |
|---|---|---|
| 1000 kişiye düşen hastane yatağı | −16,98 · −7 | +17,28 · +9 |
| Yapı birim maliyetleri (dört sınıf birlikte) | −17,00 · −1 | +16,94 · 0 |
| Yatak başına kapalı alan — genel hastane | −15,93 · −2 | +15,84 · +2 |
| Dolambaç profili çarpanı | −3,00 · −7 | +3,80 · +7 |
| Tıbbi donanım oranı | −3,03 · −1 | +3,03 · 0 |
| Azami erişim mesafesi R | +2,93 · +10 | −1,12 · −7 |
| KDV oranı | −2,83 · −1 | +2,82 · 0 |
| Kişi başı yıllık hastane başvurusu (v) | −1,89 · 0 | +1,85 · 0 |
| Altyapı + çevre düzenlemesi oranı | −1,82 · −1 | +1,82 · 0 |
| Araç km başına yakıt maliyeti (c) | −1,34 · 0 | +1,32 · 0 |
| Hastane başına sabit maliyet F_sabit | −1,17 · 0 | +1,15 · −1 |
| Planlama ufku T | −1,09 · 0 | +0,85 · 0 |
| Çekirdek alan A0 | −1,09 · +1 | +1,08 · −1 |
| Sosyal iskonto oranı r | +1,02 · 0 | −0,88 · 0 |
| Ortalama yolculuk hızı | +0,69 · 0 | −0,46 · 0 |
| Yolculuk zamanı değeri | −0,55 · 0 | +0,55 · 0 |

Okuma: toplamın %90,5'i açılış maliyeti olduğundan yatak talebi, birim maliyet ve yatak başı alan ±%20'ye neredeyse
doğrusal (±%16–17) tepki veriyor; erişim parametreleri ±%0,5–1,9. Hastane sayısındaki 0 / ±1 değişimler süre sınırlı
(40 sn) çözümlerin temel çözüm çevresinde kalmasıyla tutarlıdır ve U eğrisinin düz dibinde (p = 82–96 n* = 89'dan
ayırt edilemiyor) anlamlı değildir; sayıyı gerçekten değiştirenler yatak talebi, dolambaç ve R'dir (yapıyı değiştiren
son ikisi: R = 24 km'de 99, R = 36 km'de 82 hastane).

5 km (temel 402,611 milyar TL, kanıtlı optimum, 69 hastane; çalışmaların gap'i %0,08–0,42; 32 çalışmanın hepsinde
farkın işareti kesin): yatak talebi −16,90 · −8 / +17,38 · +4; birim maliyetler −16,84 · −1 / +16,80 · 0; yatak başı
alan −16,05 · −2 / +15,98 · 0; dolambaç −3,43 · −7 / +4,74 · +9; donanım oranı ∓3,00 · 0; KDV ∓2,80 · 0; R +2,62 ·
+10 / −1,04 · −6; başvuru sayısı −2,35 · 0 / +2,30 · +1; altyapı oranı ∓1,80 · 0; yakıt maliyeti −1,67 · −2 / +1,64 ·
+1; ufuk −1,36 · −1 / +1,05 · 0; iskonto +1,26 · 0 / −1,09 · −2; F_sabit −0,86 · +1 / +0,84 · −3; hız +0,84 · 0 /
−0,56 · 0; A0 −0,81 · 0 / +0,80 · −3; zaman değeri ∓0,68 · 0.

### 11.8 Arayüz için notlar

- **Varsayılan parametreler / hazır senaryolar** (kilitsiz): açıklamalar `GET /api/precomputed/explain_<km>km` ya da
  `explain_<senaryo>_<km>km` (`meta.grids[].precomputed_explanations`), U eğrisi `p_sweep_<km>km`, Pareto `pareto_<km>km`,
  tornado `tornado_<km>km` dosyalarından doğrudan okunabilir; aynı istekler `POST /api/explain|sweep|pareto|sensitivity`
  ile de kuyruğa girmeden döner. Çevrimdışı yedeğe (Faz 5 `build/offline.ts`) en az `explain_3km`, `p_sweep_3km`,
  `pareto_3km`, `tornado_3km` eklenmeli.
- **Kullanıcı parametreleri değiştirince**: hastane kartındaki "bu hastane kapatılırsa" tek öğe (`close: [hücre]`,
  3 km'de ≈1–5 sn); "tüm hastaneler" `close_all: true` (3 km'de ≈1–3 dk, öğe öğe ilerlemeyle); U eğrisi `sweep`
  (nokta başına ≈ süre sınırı + başlangıç); Pareto/tornado birkaç dakika sürer — "En son istek kazanır" için aynı
  `client_id` + `replace: true`; tek çözücü işçisi olduğundan yeni bir `solve` işi bekleyen açıklama işini geciktirir.
- **Dürüst gösterim**: `fark_gap_icinde: true` ise fark sayısı "fark gap içinde" etiketiyle ve aralığıyla
  (`fark_lo_tl … fark_hi_tl`) gösterilmeli; kapatma `delta_tl` her zaman "en çok" (üst sınır) diye okunmalı;
  tornado'da `sign_certain: false` ise işaret kesin değildir; hastane sayısındaki küçük değişimler (Δn) düz dipte
  anlamlı değildir. Gölge fiyatlarda `caveat_tr` gösterilmeli.
- **Sayılar**: bütün tutarlar TL (2026 fiyatları; erişim 25 yıllık, %5 iskontolu bugünkü değer), mesafeler yol km;
  `*_tr` alanları Türkçe biçimli hazır cümlelerdir (`1.234,5`, `%0,06`, `−` işareti).
