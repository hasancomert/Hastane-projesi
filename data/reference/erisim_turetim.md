# Erişim, arsa ve işletme parametreleri — türetme notu

Alan: **erisim** · Erişim tarihi: 21.10.2026 · Kaynak kimlikleri: `data/sources/erisim.yaml` · Parametreler:
`data/reference/params/erisim.yaml`, `arsa.yaml`, `isletme.yaml`, `izgara.yaml`. Ham belgeler `data/raw/erisim/`
(git dışı; `_manifest.jsonl` url/tarih/sha256). Sayılar Türkçe biçimde (1.234,5).

## 1. Yakıt fiyatı, tüketim ve km maliyeti (c)

**Filo payları** (TÜİK, Motorlu Kara Taşıtları Eylül 2026, yakıt cinsi tablosu, Eylül sonu stok; `erisim_tuik_mkt_eylul2026_yakit`):
benzin 5.616.803 + hibrit 915.892 = 6.532.695 · dizel 5.793.543 · LPG 5.268.278 → sıvı yakıtlı 17.594.516 otomobil.
Elektrikli (%2,7) ve yakıtı bilinmeyen (%0,2) dışarıda; hibrit benzine katıldı.

| Yakıt | Pay s | Ankara fiyatı p, 21.10.2026 (TL/L) | Tüketim q (L/100 km) | s·p·q/100 (TL/km) |
|---|---|---|---|---|
| Benzin (+hibrit) | 0,37129 | 80,60 | 8,5 | 2,544 |
| Motorin | 0,32928 | 97,80 | 7,3 | 2,351 |
| LPG (otogaz) | 0,29943 | 41,29 | 11,2 | 1,385 |
| **Toplam** | 1 | | **8,913** | **6,279** |

Fiyatlar: BirGün ve Yeni Ankara, 21.10.2026 (iki haber birebir aynı; `kind: haber`). Tüketimler: Yalılı Kılıç vd. 2021,
Tablo 7 (IPCC kılavuz değerleri).

- `access.fuel_l_per_100km` = Σ s·q = **8,91 L/100 km**
- `access.cost_tl_per_km` = Σ s·p·q / 100 = **6,28 TL/km**
- `access.fuel_price_tl_per_l` = c·100 / Σ s·q = 627,93 / 8,913 = **70,45 TL/L** (litre ağırlıklı; böylece
  fiyat × tüketim / 100 = c eşitliği korunur). Basit filo payı ağırlıklı fiyat Σ s·p = 74,49 TL/L alternatiftir
  (74,49 × 8,91 / 100 = 6,64 TL/km — LPG'nin yüksek litre tüketimini yok saydığı için %6 fazla).
- Resmî çapraz kontrol: EPDK Fiyatlandırma Raporu Eylül 2026, İstanbul Avrupa yakası aylık ortalama 78,826 / 91,543 /
  35,780 TL/L → c = 5,89 TL/km. Ekim fiyatları Brent'e bağlı yükseldi (Eylül ort. ≈115,8 $/varil).
- KGM taşıt işletme maliyeti 2013: 0,26029 TL/km (Bağdatlı ve Akbıyıklı 2015) × TÜFE (2025=100) 137,23 / 6,9845 =
  19,648 → 5,11 TL/km (yakıt dışı giderler dahil; alternatif).
- c yalnız yakıttır; lastik, bakım, amortisman, sigorta, otopark hariç. Toplu taşıma modellenmedi.

## 2. Zaman değeri (VOT) ve hız

- Günlük brüt asgari ücret 2026: **1.101 TL** (RG 26.12.2025/33119, Karar 2025/1). 4857 sayılı Kanun md. 63: haftada
  en çok 45 saat, çalışılan günlere eşit bölünür → 6 × 7,5 saat. Saatlik brüt = 1.101 / 7,5 = **146,80 TL/sa**
  (= aylık 33.030 / 225).
- `access.value_of_time_tl_per_h` = f × 146,80 = 0,5 × 146,80 = **73,4 TL/sa** — **f = 0,5 varsayımdır** (iş dışı
  yolculuk kesrini veren uluslararası rehberlere bu ortamdan erişilemedi; Türkiye için resmî oran bulunamadı).
  Alternatifler: 146,8 (f = 1); 293,0 (ortalama ücret: TÜİK KYA 2023 saatlik ortalama 119 TL / (13.414,5/225) = 1,996 ×
  146,80); 100 (Karayolu Ulaştırma Ana Planı yolcu zaman değeri, Demirci 2025 aktarımı, fiyat yılı belirsiz); 0.
- `access.speed_kmh` = 10 km / (21 dk 3 sn) = 10 / 0,35083 sa = **28,5 km/sa** (TomTom Trafik Endeksi 2025 Ankara,
  Yeni Ankara aktarımı). Yoğun saat 20,9; serbest akış tahmini 28,5 × 1,558 = 44,4.
- Km başına zaman maliyeti: VOT / hız = 73,4 / 28,5 = **2,58 TL/km**.

## 3. Ufuk, iskonto, bugünkü değer çarpanı

- `access.horizon_years` T = **25** (şehir hastanesi KÖİ sözleşmeleri: TBMM PBK 10.11.2023 Bakan açıklaması; Diken
  [27]; 6428 md. 4 üst sınır 30 yıl + yatırım dönemi).
- `access.discount_rate` r = **%5 reel** (AB CBA rehberi 2014–2020 uyum ülkeleri; Türkiye sosyal zaman tercihi
  tahmini %5,06 — Kalaycı Oflaz 2022 derlemesi). Kalkınma Bakanlığı ekonomik indirgeme oranı tahmini %9,8 (Uzunkaya ve
  Uzunkaya 2012) alternatiftir; SBB 2026-2028 Rehberi sabit oran vermiyor.
- PV(T, r) = (1 − (1+r)^−T) / r → PV(25; %5) = **14,094** · PV(25; %3) = 17,413 · PV(25; %9,8) = 9,218 ·
  PV(30; %5) = 15,373 · PV(20; %5) = 12,462 · PV(15; %5) = 10,380.

## 4. λ (erişim maliyeti katsayısı)

    λ = PV(T, r) · v · 2 · (c + VOT / hız)        [TL / (kişi · km), α = 1]

Güncel varsayılanlarla (v = `access.hospital_visits_per_capita`, saglik alanı; şu an 7,75 başvuru/kişi/yıl):

    c + VOT/hız = 6,279 + 73,4 / 28,504 = 6,279 + 2,575 = 8,854 TL/km
    yıllık:  v · 2 · 8,854 = 7,75 × 2 × 8,854 = 137,2 TL / (kişi · km · yıl)
    λ = 14,094 × 137,2 = 1.934 TL / (kişi · km)

v = 6,92 (2024, şartname ≈6,9) ile λ = 1.727. Arayüz λ'yı bu formülden canlı hesaplamalı (v, c, VOT, hız, T, r
kaydırıcılarından); sabit sayı olarak saklanmamalı. Mesafe d_ij = kuş uçuşu × `access.circuity` (arazi-osm: 1,223).
Ölçek fikri: nüfus 5,91 mn ve ortalama 5 km yolla erişim maliyetinin bugünkü değeri ≈ 1.934 × 5,91 mn × 5 ≈ 57 milyar TL.

## 5. Azami mesafe R

Resmî standart bulunamadı (Ruhsatlandırma Yönetmeliği ve Mekânsal Planlar Yapım Yönetmeliği EK-2'de mesafe ölçütü yok;
Sağlık Bakanlığı planlama rehberine erişilemedi) → **30 km yol mesafesi, varsayım** (≈24,5 km kuş uçuşu; 28,5 km/sa ile
≈63 dk, serbest akışta ≈41 dk). Ön kontrol (3 km, kalibre edilmemiş GHS-POP 2025): P_min = 250 ile tüm nüfuslu
hücrelerin R içinde adayı var; P_min = 500'de ≈71 hücre (≈4.900 kişi) kapsam dışı.

## 6. Arsa (varsayılan kapalı)

- `land.include` = false: kamu (Hazine) arazisi; SBB Rehberi ekonomik analizde 'arazi bedeli' transferdir.
- Vekil model p_j = taban · (yoğunluk_j / medyan)^β; taban **2.000 TL/m²** (medyan aday yoğunluğu ≈85 kişi/km²;
  Evren konut imarlı arsa 1.379 TL/m², haber), β **0,5** (varsayım). Kaba tutarlılık: 90. yüzdelik (≈2.700 kişi/km²)
  ≈11.200 TL/m², en yoğun hücre (≈33.000 kişi/km²) ≈39.400 TL/m²; TOKİ Eylül 2026 muhammen bedelleri Sincan ≈6.000–12.900,
  Yenimahalle Susuz 31.001, Gölbaşı İncek 38.482 TL/m² (140,7 mn / 4.538,61 m²; 169,9 mn / 4.415 m²).
- **İlçe ortalaması fizibilitesi:** emlak vergisi asgari arsa m² birim değerleri (Seri No 89 Tebliğ md. 3) takdir
  komisyonlarınca dört yılda bir (son: 2025'te 2026–2029 için) mahalle içinde cadde/sokak bazında belirleniyor ve yerel
  olarak ilan ediliyor; arazide il/ilçe bazında cins (kıraç/taban/sulak) değeri var. Ankara için açık, derlenmiş bir veri
  seti bulunamadı (belediye, defterdarlık, TKGM, TOKİ siteleri bu ortamdan erişilemedi). İlçe ortalaması ancak 25 ilçenin
  sokak listeleri elle toplanırsa mümkün ve bunlar piyasa değerinin altında kalan vergi değerleridir.

## 7. İşletme gideri (varsayılan kapalı)

    yatak başı = 1.288.472,3 mn TL (TÜİK 2024 cari harcama, 'Hastaneler') / 268.359 yatak (SİY 2024 bülteni)
               = 4.801.301 TL (2024) × 137,23 / 74,1393 (TÜFE) = 8.887.084 TL/yatak·yıl (Eylül 2026)

Hastane başı sabit gider 0 (ayrıştıran kaynak yok; çift sayımı önlemek için). Değer ayakta tedaviyi de içerdiğinden üst
sınırdır.

## 8. Kontrol edilip değer alınmayan belgeler ve erişilemeyenler

- Arşivlendi, ilgili sayı yok: SBB 12. Kalkınma Planı ÖİK raporları "Kent İçi Ulaşım" ve "Karayolu Ulaştırması"
  (`sbb_oik2023_*.pdf`); Yaman ve Dalkıç 2019 YHT zaman değeri makalesi (ücret oranı vermiyor); SYGM KÖİ Daire
  Başkanlığı sayfası; Cumhuriyet 15.04.2026 arazi fiyat artışı haberi (Ankara %10,34, düzey yok); Kastamonu
  erişilebilirlik makalesi (dergipark 4066881).
- Vekil sunucu reddi (yeniden denenmedi): bildirim/lisans.epdk.gov.tr, Petrol Ofisi, Opet, Shell, BP, dunya.com,
  tomtom.com, theicct.org, iea.org, ec.europa.eu, gov.uk, transportation.gov, worldbank.org, sayistay.gov.tr, sgk.gov.tr,
  kgm.gov.tr, uab.gov.tr, ankara.bel.tr, toki.gov.tr, tkgm.gov.tr, unfccc.int.
- Yeniden üretim: indirme ve hesap betikleri oturum çalışma alanındadır (`fetch.py`, `derive.py`, `grid_stats.py`,
  `grid_cover.py`); bu nottaki tüm sayılar `derive.py` çıktısıyla aynıdır.
