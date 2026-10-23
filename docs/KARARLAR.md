# Karar günlüğü

Projede görev tanımından (spec) sapan ya da spec'in açık bıraktığı her tasarım kararı burada tarihiyle kayıtlıdır.
Sayıların kaynakları `data/SOURCES.md` dosyasındadır.

| # | Tarih | Konu | Karar | Gerekçe / seçenekler |
|---|---|---|---|---|
| 1 | 2026-10-21 | Sağlık istatistiklerinin yılı | **2025** (Sağlık İstatistikleri Yıllığı 2025 Haber Bülteni) varsayılan; 2024/2023 alternatif | Nüfus da 2025 (TÜİK 31.12.2025); yıllar uyumlu olur. Spec 2023/2024 değerleri alternatif olarak durur. |
| 2 | 2026-10-21 | Sunum dizüstünün Python sürümü | Python 3.14 (64-bit, Windows) | Kullanıcının kurulu sürümü. Sabit sürümlü paketlerin 3.12–3.14 için Windows tekerlekleri var. |
| 3 | 2026-10-21 | Arayüz bileşen kütüphanesi | Mantine | shadcn/ui bileşen sunucusuna ortamdan erişilemiyor; spec ikisini de kabul ediyor. |
| 4 | 2026-10-21 | GHS-POP indirme kaynağı | JRC resmi karosu R5_C21 (jeodpp) | Spec [23] (source.coop aynası) yerine resmi kaynak; JRC'nin AWS kopyası piksel piksel aynı çıktı. |
| 5 | 2026-10-21 | Yatak başına bina alanı | **Gerçekleşen projeler (2026 Yatırım Programı)**: genel hastane ≈228 m²/yatak + çekirdek alan ≈3.361 m²; şehir hastanesi ≈279,5 m²/yatak | Spec'teki 130 m²/yatak resmi düzenlemede asgari *arsa (imar)* standardıdır; şehir hastanesi için kullanılan 335,5 ise otopark ve idari bina dahil gerçekleşen alandır. İki sınıfın aynı tabanda olması için ikisi de Bakanlığın 2026 projelerinden alınır. 130 "asgari" alternatif olarak kalır. |
| 6 | 2026-10-21 | Hücre nüfusunun dağılım kaynağı | **WorldPop R2025A 2025** varsayılan; GHS-POP R2023A seçilebilir alternatif; ikisi de ilçe bazında TÜİK 2025'e kalibre | WorldPop TÜİK 2022 ilçe verisinden üretilmiş; kalibrasyon öncesi 25 ilçenin hepsi TÜİK'in %81–104'ü aralığında. GHS-POP TÜİK 2010 ilçe verisinden türetilmiş bir projeksiyon; 9 ilçede sapma %30'u aşıyor. Not: WorldPop R2025A yayıncısınca "alfa" sürüm olarak tanımlanıyor. |
| 7 | 2026-10-21 | Dolambaç katsayısı | **Mesafeye bağlı** k(d): Ankara OSM yol ağı ölçümünden (0–5 km 1,38 → 40 km üstü ≈1,19); arayüzde çarpan | Tek katsayı (1,22) kısa şehir içi yolculukları ≈%11 eksik ölçüyor. Spec'teki 1,3 kaynaksız varsayımdı. |
| 8 | 2026-10-21 | İskonto oranı | **%5** (reel); %9,8 duyarlılık analizinde | AB maliyet-fayda kılavuzunun uyum ülkeleri oranı; Türkiye için yayımlanmış bir tahmin de %5,06. Kalkınma Bakanlığı'nın %9,8 tahmini alternatif. |
| 9 | 2026-10-21 | Hastane başı sabit maliyet (F_sabit) | 250 milyon TL **varsayım**; duyarlılıkta 0–1,5 milyar TL | Yatırım Programı regresyonu sabit terimi ayırt edemedi (tutarlar formülle fiyatlanmış). Spec 4.2'deki yedek yol. Değer yalnız genel hastaneler regresyonunun sabitine (233 milyon TL) dayanır. |
| 10 | 2026-10-21 | Hastane başvuru oranı v | Bültendeki doğrudan hastane başvurusu: 656.167.336 / 86.092.168 ≈ 7,62 | Spec tanımı "kişi başı hastane başvurusu"; ikinci+üçüncü basamak payı özel tıp merkezlerini de içeriyor. Spec yöntemi (pay × toplam) alternatif olarak kalır. |
| 11 | 2026-10-21 | 2030 il nüfusu | TÜİK resmi projeksiyonu 6.062.005 | Spec "varsayımsal büyüme" diyordu; resmi il projeksiyonu bulundu. |
| 12 | 2026-10-23 | Canlı çözümün optimallik sertifikası | **Lagrange gevşetmesi alt sınırı + HiGHS** (çekirdek MILP, değerlendirme LP/MIP'leri, önceden hesaplanan çözümlerde tam MILP) | Tam modelin kök LP'si HiGHS ile 3 km'de 126 sn sürüyor; Lagrange alt sınırı birkaç saniyede daha sıkı sınır veriyor. 3 km'de %1 sertifika 4,8–6,7 sn (10/10), 2 km'de 18–30 sn (10/10). Spec 4.6 yalnız HiGHS diyordu; öğrenci onayladı (Faz 3 raporu, docs/FAZ3_RAPOR.md). |
