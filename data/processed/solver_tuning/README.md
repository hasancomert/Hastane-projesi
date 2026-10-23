# Faz 3 ayar deneylerinin ham sonuçları

Bu klasör, `docs/FAZ3_RAPOR.md` §6 (ayar deneyleri) tablosunun ve metindeki ayar ölçümlerinin dayandığı ham sonuç
kayıtlarını içerir (doğrulama bulgusu E3: deney verisi depoda yoktu). Kayıtlar Faz 3 ayar deneylerinde (T1, T2, T3;
4 çekirdekli geliştirme konteyneri, Python 3.13, highspy 1.15.1, varsayılan parametreler) üretildi ve değiştirilmeden
kopyalandı; deney betikleri depoda değildir (deney sırasında geçici çalışma klasöründeydi). Yalnız sonuç dosyaları
(JSON / JSONL) alındı; HiGHS günlükleri alınmadı.

Biçim: JSONL dosyalarında her satır bir çalıştırmadır. Para alanları `*_btl` adlıysa milyar TL, `*_tl`
adlıysa TL; süreler saniye. Izgara `km` alanında (5 / 3 / 2).

## Dosyalar

| Klasör / dosya | Deney | İçerik |
|---|---|---|
| `T1_formulasyon/results.jsonl` | T1: formülasyon ve HiGHS ayarları | `exp` alanı: `lp` (kök LP: çözücü seçenekleri `opts`), `mip` (60 sn MIP: bağlantı türü `link` = strong / weak / 5 / pc / dosya), `cutloop` (kesme döngüsü), `lp_pc` (sınıf başına bağlantı satırlı LP), `lagr`, `lagr_pipeline`, `pipeline` (uçtan uca deneme hattı) |
| `T2_k_aday/results_k5.jsonl`, `results_k3.jsonl`, `results_k2.jsonl` | T2: aday listesi uzunluğu k | `cfg` (k, P_min, yakınlık kuralı, şehir sınıfı, \|J\|, yay sayısı), `solve` (durum, UB, LB, gap, süreler, hastane sayısı), `arc_check`, `ls_all`, `cost_ref` (açık kümenin tüm yaylarla maliyeti) |
| `T2_k_aday/results_f5.jsonl`, `results_f3.jsonl` | T2: aday filtresi (P_min 500 / 1.000 / 2.500, yakınlık kuralı, şehir sınıfı) | aynı biçim |
| `T2_k_aday/ref3.jsonl` | T2: 3 km başvuru çözümü (k = 0, R içindeki tüm adaylar, 1.208 sn) | UB 381,378 / LB 381,042 milyar TL, 88 hastane |
| `T2_k_aday/arccheck_cost.jsonl`, `colgen.jsonl`, `filter_probe.jsonl`, `poprank_probe.jsonl`, `probe_baseline.jsonl`, `lb2.jsonl`, `results_test5.jsonl` | T2 yan ölçümleri | yay kontrolünün maliyeti, LP'nin yay ekleyerek kurulması, filtre ve nüfus sırası ölçümleri, başlangıç ölçümü, 2 km alt sınır denemesi, 5 km deneme çalıştırması |
| `T3_lagrange/results.jsonl` | T3: Lagrange gevşetmesi | `exp` alanı: `e1` (çarpan güncelleme yöntemleri), `e2` / `e2b` (Lagrange sezgiseli), `e3` (3 km uzun koşu), `e5` (uçtan uca hat), `e6` (başlangıç çarpanları), `e7` (ayar taraması), `e8_*` (MIP başlangıcı / kısıtlı MIP), `e9` (indirgenmiş maliyetle sabitleme), `e11` (sabit p); `L` alt sınır (milyar TL), `curve` [sn, L] |
| `k_karsilastirma_3km.json` | Doğrulama E2 sonrası yeniden ölçüm (`pipeline/32_precompute.py kcheck`) | 3 km, k = 30 (600 sn) ve k = 10 (300 sn) çözümleri; varsayılan k = 50 çözümüne göre kanıtlı en az ve bulunan artış |

## Rapor tablosu → dosya eşlemesi (`docs/FAZ3_RAPOR.md` §6)

| Rapor satırı | Dosya ve kayıtlar |
|---|---|
| HiGHS ayarları (IPM ± crossover, hipo, PDLP, önsüzgeç kapalı, amaç ölçeği, simplex_strategy) | `T1_formulasyon/results.jsonl`, `exp = "lp"` (`opts` alanı; 5 km süreleri 7,08 / 7,99 / 29,49 / 11,06 / 39,32 sn, simplex_strategy 4 ve 3 km IPM/hipo 60 sn sınırında bitmedi) ve `exp = "mip"`, `km = 5` (`m5_*` etiketleri: varsayılan 19,67 sn) |
| Zayıf / kısmi (r = 5) / kesme döngüsü bağlantı | `T1_formulasyon/results.jsonl`: `mip3_weak_start` (alt sınır 370,195), `mip3_5_start` (376,1), `mip3_file:cut_added_3_weak_start` (377,964); `exp = "cutloop"` (65,56 sn, 376,858) |
| Sınıf başına bağlantı satırları | `T1_formulasyon/results.jsonl`: `lp_pc_5` (402,179, 8,5 sn) ve `lp_5_strong_4528` (401,109, 7,08 sn); `m5_pc` (gap 0,00236) ve `m5_default` (0,0031) |
| k (aday listesi) 5…50, tümü | `T2_k_aday/results_k{5,3,2}.jsonl` (`cfg.k`, `solve`, `cost_ref`); başvuru `T2_k_aday/ref3.jsonl`; k = 30 ve 10 yeniden ölçümü `k_karsilastirma_3km.json` |
| Aday filtresi P_min, yakınlık kuralı, şehir sınıfı | `T2_k_aday/results_f{5,3}.jsonl` (`cfg.pmin`, `cfg.near`, `cfg.city`) |
| Lagrange alt sınırı ve çarpan güncelleme yöntemleri | `T3_lagrange/results.jsonl` (`e1`, `e5`, `e7`; `curve` alanında L'nin zamanla seyri) ve `T1_formulasyon/results.jsonl` (`lagr`, `lagr_pipeline`) |
| Lagrange indirgenmiş maliyetiyle sabitleme | `T3_lagrange/results.jsonl` (`e9`) |
| Kısıtlı MIP (200 aday) | `T1_formulasyon/results.jsonl` (`m2_r5`, `pipeline` km = 2) ve `T3_lagrange/results.jsonl` (`e8_restricted`) |

Bütünleştirme ve düzeltme turlarındaki süre / başarı ölçümleri bu klasörde değil:
`data/processed/solver_benchmark.json` (canlı çözüm kıyaslaması; önceki sürümün özetleri `previous` anahtarında) ve
`data/processed/solver_baseline.json` (yalnız HiGHS temel sürümü).
