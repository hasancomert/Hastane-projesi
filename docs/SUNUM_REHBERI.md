# Sunum rehberi (≈10 dakika)

Sayılar varsayılan parametreler ve 3 km ızgara içindir; hepsi sayfadan ve veri dosyalarından gelir.

## Başlamadan (5 dk önce)
- `start.bat` açık, sayfa yüklenmiş olsun; sağ üstte **"canlı çözüm"** rozeti görünmeli.
- Bir kez **Varsayılan** senaryo kartına tıklayın (anında gelir).
- Projeksiyonda yazı küçükse tarayıcıda **Ctrl +**.
- "Sunum modu" düğmesi henüz pasif — tıklamayın.
- **"▶ Sunum turu"** (sağ üst) hocanın 9 maddesini 15 adımda anlatır: her adım haritayı / sekmeyi ilgili yere götürür, kartta o adımın sayıları yazar. **→ / PageDown** ileri, **← / PageUp** geri, **Esc** kapatır (sunum kumandası da çalışır). Kart bir şeyi örterse kartın üstündeki **⇄** ile karşı köşeye alın. Turu aşağıdaki akış yerine ya da onunla birlikte kullanabilirsiniz.

## Akış

**1. Problem (1 dk)** — *Söyle:* "Ankara'yı 3 km'lik karelere böldüm; her hücre hem talep noktası hem aday hastane yeri.
Model hangi hücreye, hangi boyutta hastane açılacağına ve her hücrenin hangi hastaneye gideceğine aynı anda karar veriyor;
amaç açılış maliyeti + 25 yıllık erişim maliyetini en küçüklemek. Mevcut hastaneler yok sayıldı, sıfırdan planlama."
*Göster:* başlıktaki cümle — **89 hastane, 381,3 milyar TL, kanıtlı fark %0,06**.

**2. Hocanın Listesi (2 dk)** — *Tıkla:* alttaki **Hocanın Listesi** sekmesi (9/9); her kartın düğmesi ilgili yere götürür.
- Nüfus 2026: **5.910.320** (TÜİK, 31.12.2025); her ilçe TÜİK ile birebir.
- Toplam mesafe: *Veri › Ankara ölçüleri › Haritada göster* — alan **25.647,9 km²**, çevre **1.590 km**, doğu–batı **261 km**,
  kuzey–güney **232 km**, en uzak iki nokta **293,8 km** (Nallıhan–Şereflikoçhisar), Kızılay'dan en uzak nokta **173,4 km**.
  Kaynaklardaki farklı yüzölçümleri (24.521 – 26.897 km²) aynı yerde tabloda.
- Izgaraya kaç hastane sığar: **634** aday hücre (her hücrede en fazla 1 hastane).
- Hastane alanı: 300 yataklı hastanenin arsası 39.000 m² = hücrenin **%0,43**'ü → arazi bağlayıcı değil; bağlayıcı olan talep ve maliyet.

**3. Harita ve sonuç (1,5 dk)** — *Tıkla:* **Katmanlar** → nüfus yoğunluğu, hizmet bölgeleri, hasta akışları.
- 89 hastane: **67 küçük, 22 orta**; büyük ve şehir hastanesi yok. 18.729 yatak (talep 18.618).
- Nüfusun **%95,0'ı 10 km**, %98,6'sı 20 km içinde; ortalama yol **3,2 km**, en uzak 29,7 km.
- *Tıkla:* merkezde bir hastane → **hastane kartı** (hizmet ettiği nüfus, mesafeler, yatak, maliyet).

**4. Neden çok sayıda küçük hastane? (1,5 dk)** — *Tıkla:* **Model** sekmesi.
- 2026 tebliğinde m² fiyatı sınıfla artıyor → yatak başı maliyet: küçük **15,5**, orta **16,8**, büyük **18,7**,
  şehir hastanesi **25,1 milyon TL**. Hastane başı sabit kısım yalnız **≈0,48–0,55 milyar TL** → bölmek ucuz.
- Hastaneler sınıf eşiklerinde yığılıyor: 40'ı **199**, 20'si **399** yatakta. 15 hastane en küçük boyutta (50 yatak):
  kırsalda 30 km kısıtını sağlamak için açılıyorlar, dolulukları düşük.
- Uzmanlaşmanın faydası modelde yok → şehir hastanesi seçilmiyor (Sınırlılıklar'da yazıyor).

**5. Neden optimum? (2 dk)**
- **Sertifika:** alt sınır ≤ optimum ≤ bulunan çözüm; fark **%0,06** → bundan %0,06'dan daha iyi bir çözüm yok.
- Alt sınır nereden: *Model › Alt sınır nereden geliyor?* — atama kısıtlarının **Lagrange gevşetmesi** (her aday için sırt
  çantası alt problemi, alt gradyan); HiGHS çözümü iyileştiriyor. Grafik: **Dal-sınır** sekmesi.
- **En az / en fazla kaç:** kapasite alt sınırı **5** (şehir hastanesi açık) / 19 (kapalı); her hücreye 30 km içinde hastane
  için en az **23**; tüm kısıtlarla en az **≈28** (kanıtlanan alt sınır 27); en fazla **634**.
- **U eğrisi** sekmesi: hastane sayısı sabitken en iyi çözüm; en iyisi **p = 89** = serbest optimum.
  *Dürüst not:* dip çok düz, 82–96 arası farklar çözücü farkının içinde.

**6. Canlı demo (1,5 dk)**
- Önce hazır senaryolar (önbellekte, anında): **Sadece genel hastane**, **Şehir hastanesi serbest**, **2030 nüfusu** —
  sonuçları ekranda okuyun.
- Sonra canlı: *Parametreler › G (Erişim) › Azami erişim mesafesi (R)* → **30 → 24 km**. 10–20 sn'de
  **100 hastane, 393,2 milyar TL (+%3)** gelir; beklerken alt/üst sınırın canlı yaklaştığını gösterin.
- Geri dönmek için **Varsayılan** kartı.

**7. Duyarlılık (1 dk)**
- **Tornado:** en etkili üç parametre yatak oranı, yapı birim maliyetleri ve yatak başı alan (±%20 → toplam ≈ ±%16–17);
  R −%20 → +10 hastane.
- **Pareto:** erişime verilen ağırlık arttıkça 92 → 105 hastane, ortalama mesafe 3,5 → 2,6 km.

**8. Kapanış (30 sn)** — **Veri › Parametreler**: her sayının kaynağı ya da "varsayım" etiketi. **Kaynakça**: Hakimi 1964,
ReVelle & Swain 1970, Toregas vd. 1971, Church & ReVelle 1974. **Sınırlılıklar** sekmesi.

## Olası sorular
- **Ankara'da zaten ~85 hastane var, neden 89?** Mevcutlar yok sayıldı (madde 6); sayı maliyet yapısından çıkıyor;
  82–96 arası pratikte eşdeğer (U eğrisi).
- **Neden şehir hastanesi yok?** Yatak başı 25,1 vs 15,5 milyon TL; uzmanlaşma faydası modellenmedi.
  "Şehir hastanesi serbest" senaryosunu gösterin.
- **Optimum olduğu nasıl kanıtlanıyor?** Lagrange alt sınırı ile bulunan çözüm arasındaki fark %0,06.
- **Mesafe kuş uçuşu mu?** Hayır: kuş uçuşu × Ankara yol ağından (OSM) ölçülen dolambaç katsayısı (kısa yolda 1,38,
  uzunda ≈1,19); en fazla 30 km yol.
- **Nüfus verisi?** WorldPop 2025 (100 m) dağılımı, her ilçede TÜİK 2025 toplamına ölçeklendi.
- **Maliyetler?** 2026 Yapı Yaklaşık Birim Maliyetleri Tebliği (RG 33157); yatak başı 228 m² ve çekirdek alan 2026
  Yatırım Programı'ndaki 47 Bakanlık projesinden; KDV %20. Hastane başı sabit maliyet 250 milyon TL **varsayım**
  (tornado'da etkisi küçük).
- **Erişim nasıl paraya çevrildi?** Kişi başı yılda 7,62 hastane başvurusu × gidiş-dönüş × (yakıt + yolculuk zamanı),
  25 yıl %5 iskontoyla → kişi·km başına ≈1.903 TL.
- **Neden 3 km?** 2/3/5 km seçilebilir: 5 km'de 69, 2 km'de 110 hastane — hücre boyutu sonucu etkiliyor (Sınırlılıklar).
- **Neden k-means değil de MILP?** Sabit maliyet, kapasite, boyut sınıfları ve 30 km kısıtı birlikte; üstelik
  optimallik kanıtı veriyor.

## Bir şey ters giderse
- Sayfa takılırsa **F5**.
- Canlı çözüm uzarsa **İptal** → en iyi bulunan çözüm gösterilir; ya da bir senaryo kartına tıklayın.
- Siyah pencere kapandıysa `start.bat`'ı yeniden açın; olmazsa **`sunum_yedek.bat`** (varsayılan çözüm, canlı çözüm kapalı).
- Canlıda **2 km** ızgarayı ve **Bütçe** modunu denemeyin (daha uzun sürer).
