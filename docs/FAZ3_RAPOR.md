# Faz 3 raporu — optimizasyon çekirdeği

Tarih: 2026-10-22 · Kapsam: şartname 4.1, 4.2, 4.4, 4.5, 4.6; 7.2, 7.3, 7.7, 7.10'un sayısal temeli; 10 (kabul ölçütleri).
Bütün süreler 4 çekirdekli geliştirme konteynerinde (Linux, Python 3.13, highspy 1.15.1) ölçüldü; sunum dizüstünün
(Windows, i7, Python 3.14) tek çekirdek hızı bilinmiyor. Para birimi TL (2026 fiyatları); erişim maliyeti 25 yıllık,
%5 iskontolu bugünkü değer. Sayı biçimi Türkçe (1.234,5).

## 1. Özet

- **Model şartname 4.5'teki gibi** (çok sınıflı kapasiteli tesis yerleşimi + R kapsama kısıtı; hücre başına en çok bir
  hastane; sınıf içinde sürekli yatak). Tek ekleme: geçerli eşitsizlik olarak **sınıf başına bağlantı satırları**
  (`x_a ≤ Σ_k min(1, U_k/q_i)·y_jk`); tamsayı çözüm kümesini değiştirmez, LP sınırını yükseltir.
- **Canlı çözüm hedefleri tutuyor** (bu makinede, 5'er soğuk çalıştırma): 3 km'de 15 sn sınırında **5/5 çalıştırma
  %1 sertifikasına 7,1–8,8 sn'de ulaştı**, çözüm 12–14,5 sn'de %0,22–0,36 farkla bitti; 2 km'de 60 sn sınırında **5/5
  çalıştırma %1'e 22–49 sn'de ulaştı** (ılık yeniden çözümlerin 1/5'i %1,15'te kaldı); 5 km'de 3,5–3,9 sn
  (ayrıntı §5). Temel sürüm (yalnız HiGHS dal-sınır) 3 km'de %1'e 192 sn'de ulaşıyordu, 2 km'de 60 sn'de %11 farkta
  kalıyordu.
- Bunu sağlayan, HiGHS'i bırakmak değil **alt sınırı başka yerden almak**: güçlü modelin kök LP'si 3 km'de tek başına
  126–160 sn sürüyor. Alt sınır artık **Lagrange gevşetmesinden** (atama kısıtları gevşetilir, problem aday başına
  sırt çantasına ayrışır; numpy) geliyor; üst sınırı **Lagrange'ın seçtiği adaylarla kurulan küçük bir "çekirdek"
  MILP** (HiGHS, ayrı süreçte; bu sırada Lagrange sınırı yükselmeye devam ediyor) iyileştiriyor. Bu bir yöntem seçimi —
  öğrencinin onayına sunuluyor (§10).
- **Önceden çözülen varsayılan çözümler** (precomputed/): 5 km 69 hastane, 402,61 milyar TL (fark %0,08);
  **3 km 89 hastane (67 küçük IV-C, 22 orta V-B), 381,33 milyar TL (fark %0,14)**; 2 km 109 hastane,
  377,28 milyar TL (fark %0,30; 30 dakikada hedef %0,2'ye inmedi). Hepsi bağımsız denetimden geçti.
- **Kabul ölçütü "p taramasının en iyisi = serbest p'li çözüm" sağlanıyor**: 5 km'de taramanın en iyisi p = 69,
  serbest optimum 69 hastane, fark %0,000; 3 km'de taramanın en iyisi p = 89,
  serbest optimum 89 hastane, fark %0,000 (tolerans %0,16); alt sınırlarla dışlanamayan "düz dip" p = 82–96 (§8).
- Beklendiği gibi **çoğu hastane küçük**: 3 km'de 37 hastane tam 199 yatakta (IV-C üst sınırı), 21'i tam 399'da
  (V-B üst sınırı) — yatak başı maliyet sınıf eşiğinde sıçradığı için. Bu verinin özelliği, hata değil (§4).

## 2. Model ve boyutları

Kümeler, değişkenler, kısıtlar ve maliyet katsayıları şartname 4.5 ve öğrenci kararlarıyla birebir
(`backend/model/milp.py` başındaki tanım). Talep q_i = w_i × 3,12/1000 × (1 + 0) → toplam 18.617,5 yatak.
λ = 1.902,6 TL/(kişi·km); yatak başı maliyet IV-C 15,54 / V-B 16,82 / V-C 18,70 / V-D 25,12 milyon TL; hastane başı
sabit kısım 478,7 / 497,6 / 525,3 / 552,1 milyon TL (testlerle sabitlenmiş: `tests/test_phase3_params.py`).

| Izgara | \|I\| | \|J\| | yay (k = 50, eşitlikler dahil) | satır | sütun | ikili | sıfırdan farklı | sınıf başına bağlantı satırı |
|---|---|---|---|---|---|---|---|---|
| 5 km | 1.073 | 454 | 27.639 | 34.463 | 31.725 | 1.816 | 132.037 | 1.209 |
| 3 km | 2.604 | 634 | 81.288 | 91.969 | 86.994 | 2.536 | 355.383 | 1.735 |
| 2 km | 4.842 | 891 | 162.497 | 177.419 | 170.516 | 3.564 | 686.122 | 1.168 |

(z_j = Σ_k y_jk yardımcı sütunu ve x_a ≤ z_j biçimi, x_a ≤ Σ_k y_jk ile aynı LP gevşetmesini 2 sıfırdan farklı
elemanla verir; Faz 3 çekirdeğinden.)

## 3. Çözüm yöntemi (`backend/model/solve.py`, `lagrange.py`)

Canlı çözüm akışı (süre sınırı T, hedef gap g; varsayılan T = 15 sn, g = %1):

1. **Sezgisel** (açgözlü + yerel arama + kesin değerlendirme): ilk olurlu çözüm 1–5 sn'de.
2. **Lagrange gevşetmesi, A evresi** (en çok 0,25·T; sınır durulunca ya da fark ≤ g/2 olunca erken biter): atama
   satırları Σ_a x_a = 1 gevşetilir (çarpan μ_i). Kalan problem aday başına ayrışır: her (j, k) için kesirli sırt
   çantası (ilk L_k yatak zaten ödenir, aşan her yatak Değişken_jk'ya mal olur, toplam ≤ U_k). Her μ için
   L(μ) = Σμ_i + Σ_{açık j} G_j ≤ optimum (geçerli alt sınır); y alt problemde ikili olduğundan Lagrange duali güçlü
   LP gevşetmesinden sıkıdır. Çarpanlar hacim algoritmasıyla (Barahona & Anbil 2000) güncellenir; başlangıç zayıf LP'nin
   dualleri ya da önbellekteki (aynı yapı) çarpanlar. Alt problem 3 km'de ≈11–13 ms, 2 km'de ≈25 ms. Bu evrede Lagrange
   sezgiseli hafif tutulur (açık kümeyi onarıp sabit sınıflı atama/boyut LP'si + sınıf onarımı).
3. **Çekirdek MIP ∥ Lagrange B evresi** (kalan süre): en iyi μ'de açılış indirgenmiş maliyeti G_j en küçük 150 aday ∪
   eldeki çözümün açık hücreleri; her talep hücresinin bu adaylar arasındaki en yakın 8 yayı ∪ eldeki çözümün yayları.
   Aynı amaç ve kısıtlarla küçük bir MILP (3 km: ≈155 aday, ≈15 bin yay; kök LP 1,3–3,4 sn) HiGHS ile **ayrı süreçte**,
   eldeki çözüm başlangıç olarak, hedef "Lagrange sınırına göre g/2". Aynı anda ana süreçte Lagrange yinelemeleri
   sürer (B evresi; sıcak çarpanlar); çekirdeğin bulduğu çözümler Lagrange'a üst sınır olarak beslenir (Polyak adımı
   ve durma ölçütü). Çekirdeğin çözümü tam modelde de olurludur; alt sınırı küresel değildir (kullanılmaz). Çekirdek
   pencerenin %30'unda başlangıç çözümünü iyileştiremezse (ara sıra oluyor) B evresinde Lagrange sezgiseli açılır;
   sayı sınırı ya da sabit p varken sezgisel baştan açıktır. İki işlemci çekirdeği kullanılır (sunum dizüstünde i7:
   yeterli).
4. **Cila**: en iyi çözümün açık kümesinde sınıf seçimli atama/boyut MIP'i (tüm yaylarla).
5. Hâlâ fark > g ve süre varsa **Lagrange 2. evre** (sezgisel açık; nadiren gerekiyor).
6. **Bağımsız denetim** (13–14 kontrol; amaç değeri ham parametrelerden sıfırdan) ve KPI'lar.

Durum: fark ≤ g → `optimal`; sertifika cümlesi (şartname 7.2) sonuçta `certificate_tr`:
"Alt sınır L = 380,81 milyar TL (Lagrange gevşetmesi), bulunan çözüm U = 381,33 milyar TL → fark %0,14. Bu modelde
(k = 50 en yakın aday listesiyle kurulan model için) daha iyi bir çözüm varsa bile bu çözümden en fazla %0,14 daha
ucuzdur." Alt sınırın kaynağı (`lb_source`: lagrange / mip / zayif_lp) ve kapsamı (`lb_scope`: k_listesi / tam) her
sonuçta yazılı.

Diğer yollar: **bütçe modu** Lagrange sınırını desteklemiyor (bütçe satırı ayrışmayı bozar) → eski yol (sezgisel →
tam MILP → gerekirse zayıf LP sınırı). **Süre sınırı ≥ 300 sn** (önceden çözülen senaryolar): Lagrange → çekirdek MIP →
gerekirse tam MILP (hedef: Lagrange sınırına göre g'ye ulaşınca dur). Hiç olurlu çözüm bulunamazsa tam MILP
(olursuzluğu kanıtlayabilir). İptal her evrede birkaç sn içinde etkili (MIP'ler ayrı süreçte).

**Yeni parametre yok.** Strateji sabitleri (A evresi payı 0,25·T, çekirdekte 150 aday ve talep başına 8 yay, %30 yedek
eşiği, iç hedef g/2, hacim algoritması ayarları) model parametresi değil, ölçümle seçilmiş modül sabitleri (`solve.py`,
`lagrange.py` başında gerekçeleriyle). Kayıtta yalnız `grid.k_nearest` girdisi güncellendi (değer 50 aynı; 0 = "R
içindeki tümü" seçeneği, aralık [0, 300], ölçülmüş gerekçe) ve `grid.candidate_min_pop` gerekçesine ölçüm notu eklendi.

## 4. Varsayılan çözümler (precomputed/default_{5,3,2}km.json)

Varsayılan parametreler; 5 km 600 sn / gap %0,1, 3 ve 2 km 1.200 / 1.800 sn / gap %0,2 sınırıyla (`pipeline/
32_precompute.py defaults`; aynı parametrelerle yazılmış önceki dosyanın çözümü MIP başlangıcı olarak verilir, yeniden
çalıştırma çözümü kötüleştirmez). Dosyalar tam sonuç sözlüğüdür: hastaneler (konum, sınıf, yatak, maliyet kırılımı, hizmet
nüfusu), atamalar, KPI'lar, denetim raporu, sertifika, iz, atama LP'sinin dual değerleri (şartname 7.6 için).

| | 5 km | 3 km | 2 km |
|---|---|---|---|
| durum, çözümün kaynağı / alt sınırın kaynağı | optimal, çekirdek MIP / tam MILP | optimal, çekirdek MIP / Lagrange | süre doldu, çekirdek MIP + cila / tam MILP |
| alt sınır L / bulunan U (milyar TL) | 402,28 / 402,61 | 380,81 / 381,33 | 376,14 / 377,28 |
| fark | %0,08 | %0,14 | %0,30 |
| hastane (IV-C / V-B / V-C / V-D) | 69 (48 / 13 / 8 / 0) | 89 (67 / 22 / 0 / 0) | 109 (105 / 4 / 0 / 0) |
| toplam yatak (talep 18.617,5) | 18.795 | 18.729 | 18.801 |
| açılış / erişim / toplam (milyar TL) | 355,83 / 46,78 / 402,61 | 345,26 / 36,07 / 381,33 | 346,42 / 30,86 / 377,28 |
| nüfus ağırlıklı ortalama yol | 4,16 km | 3,21 km | 2,74 km |
| 10 / 20 km içindeki nüfus | %95,8 / %98,7 | %94,9 / %98,6 | %95,3 / %98,4 |
| en uzak atama | 27,0 km | 29,7 km | 29,7 km |
| 50 / 199 / 399 yataklı hastane (sınıf sınırında) | 17 / 21 / 13 | 15 / 37 / 21 | 17 / 74 / 4 |
| en düşük doluluk | %15 | %16 | %3 |
| süre | 29 sn | 66 sn | 1.798 sn (sınır) |

Toplam maliyet ızgara inceldikçe düşüyor (402,6 → 381,3 → 377,3 milyar TL): ince ızgarada hem mesafe ölçümü (hücre içi
ortalama yolculuk) kısalıyor hem hastaneler talebe daha yakın konabiliyor; hastane sayısı 69 → 89 → 109. 2 km'de tam
modelin kök LP'si ≈24 dakika sürdü (117. sn'de başladı, alt sınır 1.568. sn'de 376,14'e çıktı; Lagrange sınırı 376,07);
5 km'de tam MILP 18 sn'de alt sınırı 402,18'den (Lagrange) 402,28'e çıkardı, 3 km'de Lagrange sınırıyla %0,2 hedefi
tutulduğu için tam MILP çalışmadı.

3 km açılış maliyeti kırılımı (milyar TL): yapı 192,3 + KDV 53,8 + altyapı 28,8 + donanım 48,1 + hastane başı sabit
22,2 (89 × 250 milyon) = 345,3; arsa 0 (varsayılan: kamu arazisi), işletme 0 (varsayılan kapalı).

Yorum (açıklama katmanına girdi): 3 km'de 37 hastane tam 199 yatakta, 21'i tam 399'da: yatak başı maliyet 200. ve 400.
yatakta sıçradığı için (15,54 → 16,82 → 18,70 milyon TL) model bir üst sınıfa geçmek yerine talebi komşu hastaneye
kaydırıyor. 15 hastane en küçük boyutta (50 yatak) — kırsal hücreler; R = 30 km kapsama kısıtı ve erişim maliyeti
yüzünden açılıyorlar, dolulukları düşük (en düşük %16; 2 km'de %3) — şartname 7.13'teki eşitlik–verimlilik ödünleşimi.
Şehir hastanesi (V-D) hiçbir ızgarada seçilmiyor (yatak başı 25,1 milyon TL).
Harita: `docs/screenshots/faz3_varsayilan_cozum_3km.png`.

k = 50 kırpmasının etkisi (ayar deneyi T2): R içindeki tüm adaylarla (k = 0, 124.064 yay) kurulan 3 km modelinin
20 dakikalık çözümü 381,378 milyar TL, alt sınırı 381,042. k = 50 varsayılan çözümümüz (381,331) o modelde de olurlu
olduğundan iki modelin optimumu arasındaki fark en çok 381,331 − 381,042 = 0,29 milyar TL (%0,08).

## 5. Süreler ve şartname hedefleri (data/processed/solver_benchmark.json)

`pipeline/31_benchmark.py`: varsayılan parametreler, her çalıştırma ayrı süreçte (soğuk: ızgara yükleme + yapı +
çarpanlar sıfırdan), ardından aynı süreçte "ılık" yeniden çözüm (yapı önbellekte, hastane başı sabit maliyet +%10 —
sunumda bir kaydırıcı oynatılınca olan). Konteyner başka iş yokken; süre sınırı 3 ve 5 km'de 15 sn
(`solver.time_limit_s`), 2 km'de 60 sn; hedef gap %1.

| Izgara (sınır) | çalıştırma | %1'e ulaşan / sonda sertifikalı (sınır içinde) | %1'e ulaşma, sn (ortanca; en çok) | toplam süre, sn (ortanca; en az–en çok) | son gap (ortanca; en kötü) | bulunan U, milyar TL (soğuk) | evreler, ortanca sn: sezgisel / Lagrange A / çekirdek ∥ B / cila |
|---|---|---|---|---|---|---|---|
| **3 km (15 sn)** | soğuk, 5 | **5/5** / 5/5 | **8,7 (8,8)** | 12,7 (12,2–14,5) | %0,23 (%0,36) | 381,62–382,12 | 1,1 / 3,8 / 6,2 / 1,5 |
| 3 km (15 sn) | ılık, 5 | 5/5 / 5/5 | 4,2 (4,2) | 6,3 (5,7–10,4) | %0,29 (%0,37) | — | 1,1 / 3,6 / 4,0 / 1,5 |
| **2 km (60 sn)** | soğuk, 5 | **5/5** / 4/5 | **26,8 (49,3)** | 52,5 (34,9–60,0) | %0,49 (%0,83) | 377,53–379,20 | 4,7 / 15,0 / 31,4 / 0,3 |
| 2 km (60 sn) | ılık, 5 | 4/5 / 4/5 | 22,9 (24,4) | 26,8 (25,4–57,5) | %0,38 (%1,15) | — | 5,3 / 13,8 / 5,7 / 0,4 |
| 5 km (15 sn) | soğuk, 5 | 5/5 / 5/5 | 3,5 (3,6) | 3,7 (3,5–3,9) | %0,27 (%0,27) | 403,24 | 1,4 / 2,0 / — / 0,1 |
| 5 km (15 sn) | ılık, 5 | 5/5 / 5/5 | 2,5 (2,6) | 3,4 (3,3–3,5) | %0,39 (%0,39) | — | 1,5 / 1,1 / — / 0,8 |

Notlar: (1) solve() %1'e ulaşınca durmaz; süre içinde fark %0,5'e inene dek çekirdek MIP + Lagrange B ile iyileştirmeye
devam eder (kalite: 3 km soğukta son fark ortanca %0,23). Şartname hedefi için ölçüt, izden okunan **%1'e ulaşma
süresi**; o andaki çözüm ilerleme akışında arayüze zaten gelir. (2) "Sonda sertifikalı (sınır içinde)": durum `optimal`
ve toplam süre ≤ hedef; 2 km soğukta bir çalıştırma 60,0 sn'yi birkaç ms aşarak bitti (%1'e 49 sn'de ulaşmıştı).
(3) 2 km ılık çalıştırmalardan birinde çekirdek MIP hiç iyileştiremedi, Lagrange sezgiseli yedeği 57 sn'de farkı
%1,15'e indirebildi (`sure_doldu`). (4) 5 km'de A evresi farkı tek başına %0,5'in altına indiriyor; çekirdek MIP
çalışmıyor. (5) İlk olurlu çözüm 1,1–1,5 sn (3/5 km), 5–7 sn (2 km; ızgara + yapı yükleme dahil soğuk). (6) Alt
sınırlar çalıştırmalar arasında kararlı (3 km 380,74–380,77; 2 km 376,05–376,08).

Temel sürümle karşılaştırma (aynı konteyner, `data/processed/solver_baseline.json`, yalnız sezgisel + tam güçlü MILP):
3 km'de ilk anlamlı alt sınır 144 sn'de (kök LP), %1'e 192 sn, %0,16'ya 200 sn; 2 km'de 60 sn'de fark %11,0 (zayıf LP
sınırı); 5 km'de %0,1'e 21 sn.

Hedeflerle karşılaştırma:
- **3 km ≲ 15 sn: tutuyor.** 5/5 soğuk çalıştırmada %1 sertifikası 7,1–8,8 sn'de, çözüm 12,2–14,5 sn'de (%0,22–0,36
  farkla) bitiyor; ılık yeniden çözüm (sunumda kaydırıcı) 5,7–10,4 sn. Pay: dizüstü bu konteynerden ≈%40 yavaşsa
  bile %1 sertifikası 15 sn içinde kalır; süre sınırı her durumda uygulanır (sonuç `sure_doldu` ile döner).
- **2 km ≲ 60 sn: tutuyor ama payı az.** 5/5 soğuk çalıştırmada %1'e 22–49 sn'de ulaşıldı; ılık çalıştırmaların
  biri 60 sn'de %1,15'te kaldı (çekirdek MIP'in iyileştiremediği nadir durum). Dizüstü yavaşsa 2 km canlı çözümde
  60 sn'de sertifika her zaman gelmeyebilir — karar maddesi §10.2.
- **5 km:** 3,3–3,9 sn, %0,27–0,39.
- Temel sürüme göre: 3 km'de %1'e ulaşma 192 sn → ≈8,7 sn (≈22 kat), 2 km'de 60 sn'de %11 → %1'e 27 sn.

## 6. Ayar deneyleri ve ölçülen etkiler

Şartname 4.6 sırası (aday filtresi → k → sınıf sayısı) ve HiGHS ayarları önce denendi; hiçbiri hedefi tutturmadı.
Uygulanan her değişiklik ölçülmüş etkisiyle:

| Değişiklik | Ölçülen etki | Karar |
|---|---|---|
| HiGHS ayarları: IPM (± crossover), PDLP, önsüzgeç kapalı, primal simpleks, amaç ölçeği, iş parçacığı, sezgisel çabası, simplex_strategy (T1) | 3 km kök LP'si 60 sn'de hiçbirinde bitmedi; 5 km'de en iyisi varsayılan (7,1 sn); önsüzgeç kapalı 29,5 sn, PDLP 39 sn | varsayılanlar korundu |
| Zayıf / kısmi (en yakın r yay) / kesme döngüsü bağlantı (T1) | 3 km 60 sn: alt sınır 370,2 / 376,1 / 378,0, hiçbiri %1 değil; 5 km'de güçlüden yavaş | güçlü bağlantı korundu |
| **Sınıf başına bağlantı satırları** (T1) | 5 km kök LP sınırı 401,11 → 402,18 (kök boşluğunun ≈%63'ü), süre ≈aynı; MIP sonu gap %0,31 → %0,24 | **uygulandı** |
| k (aday listesi) 5…50, tümü (T2) | k < 50 amacı bozuyor (3 km k = 30 +%1,2, k = 10 +%4,6) ve hızlandırmıyor; k = 50 tümüne göre ≤ %0,10 | **k = 50 korundu**; k = 0 (R içindeki tümü) seçeneği eklendi |
| Aday filtresi P_min 500 / 1.000 / 2.500 (T2) | 3 km 12,7 / 7,9 / 7,9 sn ama amaç +%2,1 / +%3,0 / +%3,5 (kanıtlı ≥ %1,2 / %2,0 / %2,6) | **P_min = 250 korundu** (hız ayarı değil) |
| Şehir sınıfını kapatmak (T2) | hiçbir optimum V-D kullanmıyor; 5 km'de %24 hızlı, 3 km'de fark yok | açık kaldı |
| **Lagrange alt sınırı** (T1, T3) | 3 km'de güçlü LP değerine (379,96) ≈5 sn'de, 380,5'e ≈4 sn'de ulaşıyor (LP: 126 sn); 2 km'de 376,0 (LP 60 sn'de bitmiyor) | **uygulandı** (`lagrange.py`, hacim algoritması) |
| Lagrange sezgiselinde sınıf onarımı + sınıf seçimli MIP (entegrasyon) | aynı açık kümede sabit sınıflı LP'den %0,07–1,3 ucuz (2 km'de bir örnekte 382,8 → 377,7) | uygulandı |
| Sezgiselin kesin değerlendirmesi gap 1e-3 → 1e-2 (entegrasyon) | 2 km 6,9 → 4,6 sn, 3 km 2,3 → 1,0 sn (3 km'de ilk çözüm %0,27 kötü; sonra iyileşiyor) | uygulandı |
| Kısıtlı MIP (T1: 200 aday, tüm yaylar) | 2 km'de iki denemede 16 sn'de hiç iyileşme yok | uygulanmadı |
| Çekirdek MIP'i Lagrange'dan SONRA sıralı çalıştırmak (entegrasyon, ilk sürüm: A 0,3·T → çekirdek 0,3·T → cila) | 3 km 5 çalıştırma: 4 sertifikalı, gap %0,93–0,95, biri %1,23 (çekirdek 5 sn'de iyileştiremedi); alt sınır 378,5 (A evresinde sezgisel süreyi yiyor) | yerine paralel düzen |
| **Çekirdek MIP ∥ Lagrange B** + A evresinde hafif sezgisel (entegrasyon) | 3 km 3 çalıştırma: gap %0,22 / %0,36 / %0,22, alt sınır 380,77–380,79; tam sezgiselle A: %0,33 / %0,90 / %0,90; 2 km: %0,46 / %0,48 (35 / 41 sn); 5 km %0,27 (4 sn) | **uygulandı** |
| Çekirdek iyileştiremezse B evresinde sezgisel yedeği (entegrasyon) | yedeksiz ilk paralel sürümde: 3 km soğuk 5 çalıştırmanın 1'inde (gap %1,26), 2 km ılık 5'in 2'sinde (%2,40, %1,38) çekirdek hiç iyileştiremedi ve sertifika çıkmadı; yedekle (son kıyaslama) 3 km 5/5, 2 km soğuk 5/5, ılık 4/5 | **uygulandı** (pencerenin %30'u) |
| Hafif sezgisel sayı sınırı / sabit p varken (entegrasyon) | 3 km p = 70: gap %1,27 (225 sn; kapsama onarımı p'yi bozduğundan p'yi sağlayan çözüm çıkmıyor) | sayı sınırı varken A ve B evrelerinde tam sezgisel (p = 70: %0,50) |
| **Çekirdek MIP** (entegrasyon: 150 aday, talep başına 8 yay) | 3 km: kök 1,3 sn, 2,4 sn'de 382,09, 11 sn'de 381,59 (Lagrange sezgiseli 383,5; tam MILP 200 sn'de 381,64); 2 km: 8 denemenin 7'sinde 1,4–4,3 sn'de 377,6–379,5, birinde 21 sn'de iyileşme yok; 200–250 aday daha yavaş | **uygulandı** |
| Lagrange indirgenmiş maliyetiyle sabitleme (T1, T3) | hücre sabitleme ≈0 (sabit maliyet boşluktan küçük) | uygulanmadı (`fixing` fonksiyonu duruyor) |
| Yay kontrolü (T2: açık kümeyi tüm yaylarla değerlendirme) | %0,013 (3 km) – %0,088 (2 km) iyileşme, ama çözümü k listesi dışına taşır (şartname 4.5 N_i tanımı) | uygulanmadı; k = 0 seçeneği yerine |

## 7. Sınır kartları (data/processed/sinir_kartlari.json; şartname 4.5)

| | 5 km | 3 km | 2 km |
|---|---|---|---|
| Izgaraya sığan en fazla hastane \|J\| | 454 | 634 | 891 |
| Kapasite alt sınırı ⌈18.617,5 / U_max⌉: şehir sınıfıyla / onsuz | 5 / 19 | 5 / 19 | 5 / 19 |
| Kapsama alt sınırı (LSCP, R = 30 km) | 26 (kanıtlı, 2 sn) | 27 (kanıtlı, 3 sn) | 34 (alt sınır 33; 600 sn'de kanıtlanamadı) |
| Gerçek p_min (tüm kısıtlar) | 27 (kanıtlı, 198 sn) | 28 (alt sınır 27; 300 sn'de kanıtlanamadı) | 36 (alt sınır 33; 600 sn'de kanıtlanamadı) |

(Şartnamedeki örnek "≈18.440 yatak → 19 / 5" ile aynı sonuç; talep 18.617,5.) Gerçek p_min'in "alt sınır"ı, p_min
MIP'inin, LSCP'nin ve kapasite sınırının en büyüğüdür. Karşılaştırma: varsayılan optimum 69 / 89 / 109 hastane — en
az sayının 2,5–3 katı; aradaki fark kapsama ya da kapasite değil maliyet (erişim) kararıdır.

## 8. U eğrisi ve kabul ölçütü (precomputed/p_sweep_{3,5}km.json; şartname 7.7)

Her p için sabit_p modunda çözüm (3 km: 60 sn, hedef %0,3; 5 km: 30 sn, hedef %0,2; gap'i %1'i aşan noktalar 4 kat
süreyle yeniden). p = n_serbest noktasında serbest çözüm başlangıç olarak verildi (bilinen olurlu çözüm).

| | 3 km | 5 km |
|---|---|---|
| p değerleri | 35 nokta, p = 40–150 (82–100 arası her p) | 30 nokta, p = 35–120 (64–76 arası her p) |
| hedef gap'e ulaşan nokta | 28 / 35 (ortanca gap %0,19; en kötü %2,3, p = 40) | 23 / 30 (ortanca %0,13; en kötü %3,6, p = 40) |
| taramanın en iyisi | **p = 89, 381,33 milyar TL** | **p = 69, 402,61 milyar TL** |
| serbest p'li optimum (önceden çözüm) | **89 hastane, 381,33** (alt sınır 380,81) | **69 hastane, 402,61** (alt sınır 402,28) |
| fark / tolerans | %0,000 / %0,16 | %0,000 / %0,12 |
| hiçbir p serbest alt sınırın altında değil | evet | evet |
| alt sınırla dışlanamayan p'ler ("düz dip") | 82–96 | 64–73 |
| uç noktalar | p = 40: +%11,9; p = 150: +%5,6 | p = 35: +%19,3; p = 120: +%5,2 |
| süre | 28 dk | 12 dk |

Kabul ölçütü (tolerans = serbest çözümün, taramanın en iyi noktasının ve p = n_serbest noktasının gap'lerinin en
büyüğü) iki ızgarada da sağlanıyor; `tests/test_phase3_outputs.py::test_p_sweep_best_equals_free_optimum` saklanan
dosyalar üzerinde aynı denetimi yapıyor. Not: p = n_serbest noktasında serbest çözüm başlangıç olarak verildiğinden
o noktanın değeri serbest çözümden kötü olamaz; ölçütün asıl içeriği **başka hiçbir p'nin daha iyi bir çözüm
vermemesi** (verseydi serbest çözüm optimum olmazdı) ve serbest p'nin alt sınırlarla dışlanamayan aralıkta olması.
Dip boyunca (3 km'de 82–96) maliyet farkları hedef gap'lerden küçük: "neden tam 89?" sorusunun dürüst yanıtı "82–96
arası ekonomik olarak neredeyse eşdeğer; 89 bulunan en iyisi" (önceki bir çalıştırmada 88 hastaneli 381,39'luk çözüm
bulunmuştu — fark %0,02).

Şekil: `docs/screenshots/faz3_u_egrisi_3km.png`. Bulgu (açıklama katmanına): U'nun sol kolu yalnız erişimden değil
açılıştan da gelir — az hastanede hastaneler büyük sınıflara çıkar ve yatak başı maliyet artar (5 km'de p = 35'te
açılış 408,2, p = 69'da 355,8 milyar TL). Sağ kolda her ek hastane sabit kısım ve en az 50 yatak getirir; erişim
neredeyse düz kalır. Dip geniş ve düzdür: dışlanamayan aralıktaki bulunan çözümler serbest optimumdan en çok %0,12
(5 km, p = 64–73) ve %0,26 (3 km, p = 82–96) pahalı.

## 9. Doğrulama ve testler

- **Kaba kuvvet = MILP** (şartname 4.6, 10): `tests/test_phase3_bruteforce.py` — 5–6 hücreli rastgele oyuncak
  örneklerde tüm açık kümeler × sınıf seçimleri sayılıyor, atama LP'si bağımsız (scipy linprog) kuruluyor; MILP (güçlü,
  sınıf başına bağlantı dahil, ve zayıf) aynı optimumu veriyor: 5 örnek + 4 mod (sabit p, en az, en çok, kilit) +
  bütçe modu + solve() boru hattı (ayrı süreçte ve aynı süreçte). Hepsi geçiyor.
- **Lagrange** (`tests/test_phase3_lagrange.py`): alt problem bağımsız LP kurulumuyla aynı G_jk'yı veriyor; L(μ) ≤
  kaba kuvvet optimumu (rastgele ve yakınsamış μ; toplam, sabit p, en az, en çok, kilit, α = 1,5 + arsa); sınıf başına
  satırlar MILP'i değiştirmiyor ve LP sınırını yükseltiyor; çekirdek örnek olurluluğu; 5 km sertifika; 3 km canlı
  hedef (≤ 15 sn × 1,25); k = 0 yapısı; çekirdek MIP sırasında iptal.
- **Saklanan çıktılar** (`tests/test_phase3_outputs.py`): varsayılan çözümler denetimden geçmiş ve tam; p taraması
  kabul ölçütü; sınır kartları; kıyaslama dosyası.
- Her çözümde otomatik denetim (`checks.py`, 13–14 kontrol): tam atama, R (koordinatlardan yeniden), hücre başına bir
  hastane, sınıf sınırları, kapasite, kilitler, sayı, bütçe, amacın sıfırdan yeniden hesabı.
- Tam test takımı (`.venv/bin/python -m pytest -q tests/`): **186 test geçti** (4 dk 48 sn; Faz 1–2 veri/ızgara
  testleri dahil). Zaman ölçen testler (3 km ≤ 18,75 sn, iptal ≤ 5 sn) payla yazıldı.

## 10. Açık konular ve öğrencinin kararları

1. **Çözüm yöntemi (onay gerekiyor).** Şartname 4.6 "highspy (HiGHS)" diyor. HiGHS hâlâ kullanılıyor (çekirdek MIP,
   değerlendirme LP/MIP'leri, uzun çözümlerde tam MILP), ama canlı çözümde **optimallik sertifikasının alt sınırı
   Lagrange gevşetmesinden** geliyor; HiGHS tam modelin kök LP'sini 3 km'de 126–160 sn'de bitiriyor. Seçenekler:
   (a) bu yöntem (önerilen; ders için de anlatılabilir: Lagrange duali, alt gradyan, sertifika);
   (b) yalnız HiGHS: 3 km canlı hedefi tutmaz (≈200 sn), 2 km'de 60 sn'de sertifika yok.
2. **2 km 60 sn hedefi sınırda.** Konteynerde 5/5 soğuk çalıştırmada %1'e 22–49 sn'de ulaşıldı ama ılık
   çalıştırmaların biri %1,15'te kaldı; dizüstü daha yavaşsa pay kalmayabilir. Seçenekler: (a) 2 km'yi "önceden
   çözülmüş + canlı en çok 60 sn, sonuç sertifikalı değilse farkı göster" olarak bırakmak (önerilen; arayüz farkı ve
   alt sınırı her zaman gösteriyor); (b) 2 km'de süre sınırını 90–120 sn yapmak; (c) 2 km'de hedef gap'i %1,5 yapmak.
   (Aday filtresi ve k ile hızlandırma önerilmez: §6, amaç %2–5 kötüleşiyor.)
3. **Dizüstü hızı bilinmiyor.** Konteyner ölçümleri; i7'nin tek çekirdek hızı daha yüksekse süreler kısalır, düşükse
   3 km'de 15 sn'ye yaklaşır. Faz 8'de dizüstünde `pipeline/31_benchmark.py` çalıştırılmalı. Süre sınırı aşılırsa
   çözüm yine döner (durum `sure_doldu`, fark gösterilir).
4. **Gap %1'in anlamı.** Canlı çözümler %0,2–0,8 farkla bitiyor; p'ye göre maliyet farkları (U eğrisinin dibi)
   bundan da küçük. Şartname 7.3 "Neden N hastane? N−1 ve N+1 ile kıyas" için önceden çözülmüş sıkı (%0,1–0,3) çözümler
   ve p taraması kullanılmalı; canlı çözümde bu kıyas "fark içinde" diye etiketlenmeli.
5. **Bütçe modu** Lagrange sınırını kullanmıyor: 3 km'de bütçe modunda canlı çözüm tam MILP'e kalıyor (15 sn'de
   sertifika beklenmez; olurlu çözüm ve zayıf LP sınırı döner). İstenirse bütçe satırı için ayrı bir çarpanla
   genişletilebilir (Faz 4+).
6. **k = 50** korundu; "R içindeki tüm adaylar" (k = 0) seçeneği eklendi (önceden çözülen senaryolar için tam model;
   3 km'de 124.064 yay). Varsayılanı değiştirmek öğrencinin kararı; fark ≤ %0,08 (3 km, §4).
7. **2 km LSCP / p_min kanıtsız.** 600'er sn'de LSCP 34 ve p_min 36 bulundu, ikisinin de alt sınırı 33 (3 km'de
   p_min 28, alt sınır 27). Kartta "en az 33, bulunan 34 / 36" diye gösterilmeli; daha uzun süre ya da daha güçlü
   formülasyon (LSCP için) istenirse Faz 4'te arka planda çözülebilir.
8. **Faz 4 için teknik notlar.** (a) solve() Lagrange döngüsünü çağıran iş parçacığında çalıştırır (numpy + Python,
   GIL tutar): FastAPI sunucusu donmasın diye çözüm ayrı bir süreçte ya da iş parçacığında çalıştırılmalı, ilerleme
   olayları (progress_cb: lagrange / incumbent / phase) kuyruktan akıtılmalı. (b) Çekirdek MIP ve uzun çözümlerdeki
   tam MILP `multiprocessing` (spawn) kullanır: Windows'ta sunucunun giriş betiği `if __name__ == "__main__":`
   korumalı olmalı. (c) Çarpan önbelleği (aynı yapı) süreç içindedir; sunucu süreci kalıcı olursa ılık çözümler
   hızlanır (3 km ılık: %1'e 4,2 sn, toplam 5,7–10,4 sn).
9. **Kırsal küçük hastanelerin düşük doluluğu** (3 km'de en düşük %16, 2 km'de %3) R = 30 km kapsama kısıtının
   sonucu; açıklama katmanında anlatılmalı.
10. **Önceden çözülen dosyalar ve kod sürümü.** Varsayılan çözümler ve p taramaları son motorla üretildi; yalnız
   "çekirdek iyileştiremezse B evresinde sezgisel yedeği" ondan sonra eklendi (varsayılan çözümlerde çekirdek her
   seferinde iyileştirdiği için yedek devreye girmezdi; taramalarda sayı sınırı nedeniyle sezgisel zaten açıktı).
   Süre sınırlı ve çok süreçli olduğundan yeniden çalıştırma birebir aynı sayıları vermez; aynı parametreyle yeniden
   çalıştırılan varsayılan çözüm önceki çözümden başlar, kötüleşmez.

## 11. Dosyalar

- Motor: `backend/model/milp.py` (sınıf başına bağlantı satırları; ayrı süreçte çözümü başlatıp yoklamaya izin veren
  `start_isolated` / `IsolatedRun`; çözümde kapalı hücreye tolerans içi payların temizlenmesi), `backend/model/
  lagrange.py` (yeni), `backend/model/solve.py` (yeni akış, sertifika cümlesi), `backend/model/evaluate.py` (hastane
  kırılımında kapalı hücre koruması), `backend/model/params.py` + `instance.py` (k = 0).
- Kayıt: `data/reference/params/izgara.yaml` (`grid.k_nearest` ölçüm gerekçesi, 0 seçeneği; `grid.candidate_min_pop`
  ölçüm notu) → `pipeline/build_registry.py` (69 anahtar, kanonik sayı değişmedi).
- Betikler: `pipeline/31_benchmark.py`, `pipeline/32_precompute.py`, `pipeline/33_faz3_figures.py`;
  `pipeline/30_solve_default.py --baseline` artık açıkça Lagrange'sız çalışır (temel ölçümü yeniden üretir).
- Çıktılar: `data/processed/solver_benchmark.json`, `data/processed/sinir_kartlari.json`,
  `precomputed/default_{5,3,2}km.json`, `precomputed/p_sweep_{3,5}km.json`, `docs/screenshots/faz3_*.png`.
- Testler: `tests/test_phase3_lagrange.py`, `tests/test_phase3_outputs.py` (yeni); engine testlerinde yorum güncellemesi.

Kaynakça (Crossref'te doğrulandı, ayar deneyi T3): Geoffrion & McBride 1978, AIIE Transactions 10:40–47,
doi:10.1080/05695557808975181; Cornuéjols, Sridharan & Thizy 1991, EJOR 50:280–297, doi:10.1016/0377-2217(91)90261-S;
Barahona & Anbil 2000, Mathematical Programming 87:385–399, doi:10.1007/s101070050002; Held, Wolfe & Crowder 1974,
Mathematical Programming 6:62–88, doi:10.1007/BF01580223.
