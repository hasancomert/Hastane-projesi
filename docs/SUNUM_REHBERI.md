# Sunum rehberi (8–10 dakika)

Başlatma: `start.bat` (bkz. `BASLAT.md`). Sayfa `http://127.0.0.1:8000`. Tüm sayılar sayfadan ve veri dosyalarından gelir;
aşağıdakiler varsayılan parametreler ve 3 km ızgara içindir.

## Akış

1. **Açılış (30 sn).** Başlıktaki cümle: *Optimum (kanıtlı fark %0,06): 89 hastane (67 küçük, 22 orta), toplam
   381,3 milyar TL; nüfusun %95,0'ı hastaneye 10 km içinde.* "Mevcut hastaneler yok sayıldı, sıfırdan planlama."
2. **Hocanın Listesi sekmesi (2 dk)** — alt kısımda, **9/9**. Her maddenin düğmesi sayfada ilgili yere götürür:
   - Nüfus 2026: **5.910.320** (TÜİK ADNKS 31.12.2025); her ilçe TÜİK'e birebir kalibre.
   - Toplam mesafe: alan **25.647,9 km²**, çevre **1.590 km**, doğu–batı **261 km**, kuzey–güney **232 km**,
     en uzak iki sınır noktası **293,8 km**, Kızılay'dan en uzak nokta **173,4 km** (Veri › Ankara ölçüleri; "Haritada göster").
   - Izgaraya kaç hastane sığar: 3 km'de **634** aday hücre (her hücrede en fazla 1 hastane).
   - Hastane alanı: 300 yataklı hastanenin arsası 39.000 m² = 3 km hücrenin %0,43'ü → **arazi bağlayıcı değil**.
3. **Harita (1 dk).** Katmanlar düğmesi: nüfus yoğunluğu, aday hücreler, hizmet bölgeleri, hasta akışları, erişim ısı haritası.
   Bir hastaneye tıkla → **hastane kartı** (sınıf, yatak, hizmet ettiği nüfus, mesafeler, maliyet; 199/399 yatak eşiği notu).
4. **Neden çok sayıda küçük hastane? (1 dk)** — Model sekmesi ve sağ panel: 2026 tebliğinde m² fiyatı sınıfla artıyor;
   yatak başı maliyet küçük **15,5** → orta **16,8** → büyük **18,7** → şehir hastanesi **25,1 milyon TL**; hastane başı
   sabit kısım yalnız **≈0,48–0,55 milyar TL**. Bu yüzden hastaneler 199 ve 399 yatakta yığılıyor; şehir hastanesi seçilmiyor
   (uzmanlaşmanın faydası modellenmedi — Sınırlılıklar).
5. **Canlı çözüm (1–2 dk).** Önce hazır senaryolar (önbellekte, anında): *Sadece genel hastane*, *Şehir hastanesi serbest*,
   *2030 nüfusu*. Sonra bir parametreyi canlı değiştir: **Azami erişim mesafesi R = 30 → 24 km** (tornado verisine göre
   ≈ +10 hastane, toplam +%3; temiz kurulumda denendi: **100 hastane, 393,2 milyar TL, optimum, fark %0,63, ≈15 sn**)
   — ilerleme çubuğunda alt/üst sınır ve fark canlı akar; sonuçta yeni sertifika cümlesi.
   *İpucu: canlı çözüm 5–15 saniye sürer; beklerken "alt sınırı Lagrange gevşetmesi veriyor" diye anlatın.
   Sayfayı sunumdan önce bir kez açıp bekletin (ilk açılışta çözücü ısınır).*
6. **Neden optimum? (2 dk)**
   - **Sertifika:** alt sınır L ≤ optimum ≤ bulunan çözüm U; fark %0,06 → bundan %0,06'dan daha iyi çözüm yok.
     Alt sınır: atama kısıtlarının **Lagrange gevşetmesi** (her aday için sırt çantası alt problemi, alt gradyan/hacim
     algoritması); HiGHS seçilmiş aday alt kümesinde çözümü iyileştiriyor (Model › "Alt sınır nereden geliyor?").
   - **En az / en fazla kaç:** kapasite alt sınırı 5 (şehir hastanesi açık) / 19 (kapalı); kapsama (her hücreye 30 km
     içinde hastane) en az **23**; tüm kısıtlarla en az **28** (kanıtlanan alt sınır 27); en fazla 634.
   - **U eğrisi** (grafik sekmesi): hastane sayısı p sabitken en iyi çözüm; en iyisi **p = 89 = serbest optimum**.
     Dip çok düz: 82–96 arası farklar çözücü farkının içinde (dürüstçe söyleyin).
   - **Tornado:** en etkili parametreler yatak oranı, birim maliyetler ve yatak başı alan (±%20 → toplam ≈ ±%16–17).
   - **Pareto:** erişime verilen ağırlık arttıkça hastane sayısı 92 → 105'e çıkar, ortalama mesafe 3,5 → 2,6 km'ye iner.
7. **Kapanış (30 sn).** Veri › Parametreler: her sayının kaynağı ya da "varsayım" etiketi; Kaynakça (DOI'li literatür:
   Hakimi 1964, ReVelle & Swain 1970, Toregas vd. 1971, Church & ReVelle 1974); Sınırlılıklar.

## Olası sorular

- **Nüfus verisi?** WorldPop 2025 (100 m) dağılımı, her ilçede TÜİK 2025 toplamına kalibre (il toplamı tam 5.910.320).
- **Mesafe?** Kuş uçuşu × Ankara OSM yol ağından ölçülen dolambaç profili (kısa yolda 1,38, uzunda ≈1,19); en uzak atama 29,7 km.
- **Maliyetler?** 2026 Yapı Yaklaşık Birim Maliyetleri Tebliği (RG 33157) + 2026 Yatırım Programı'ndaki 61 Bakanlık projesinden
  yatak başı alan (228 m²) ve çekirdek alan; KDV %20; 25 yıl, %5 iskonto ile erişim maliyetinin bugünkü değeri.
- **Neden 89, 88 değil?** Fark çözücü farkının içinde; model 82–96 arasını ayırt edemiyor — U eğrisinde gösterilir.
- **Ne kadar hızlı?** 3 km'de %1 optimallik sertifikası birkaç saniyede (geliştirme ortamında 4,8–6,7 sn).

## Yedek plan
Sunucu açılmazsa `sunum_yedek.bat`: paket gerektirmez, varsayılan çözümü "çevrimdışı mod" ile gösterir.
Ekran görüntüleri: `docs/screenshots/` (faz5_*, faz3_*, faz6_*).
