# Faz 3 raporu — optimizasyon çekirdeği

Tarih: 2026-10-23 (doğrulama düzeltmeleriyle güncellendi) · Kapsam: şartname 4.1, 4.2, 4.4, 4.5, 4.6; 7.2, 7.3, 7.7,
7.10'un sayısal temeli; 10 (kabul ölçütleri). Bütün süreler 4 çekirdekli geliştirme konteynerinde (Linux, Python 3.13,
highspy 1.15.1) ölçüldü; sunum dizüstünün (Windows, i7, Python 3.14) hızı ölçülmedi. Para birimi TL (2026 fiyatları);
erişim maliyeti 25 yıllık, %5 iskontolu bugünkü değer. Sayı biçimi Türkçe (1.234,5). Bu rapordaki her sayı depodaki
bir dosyadan gelir; kaynak dosya her bölümün başlığında ya da tabloda yazılı.

## 1. Özet

- **Model şartname 4.5'teki gibi** (çok sınıflı kapasiteli tesis yerleşimi + R kapsama kısıtı; hücre başına en çok bir
  hastane; sınıf içinde sürekli yatak). Tek ekleme: geçerli eşitsizlik olarak **sınıf başına bağlantı satırları**
  (`x_a ≤ Σ_k min(1, U_k/q_i)·y_jk`); tamsayı çözüm kümesini değiştirmez, LP sınırını yükseltir.
- **Canlı çözüm hedefleri bu konteynerde tutuyor** (`data/processed/solver_benchmark.json`, boşta, soğuk çalıştırma):
  3 km'de 15 sn sınırında **10/10 çalıştırma %1 sertifikasına 4,8–6,7 sn'de** (ortanca 5,7) ulaştı; 2 km'de 60 sn
  sınırında **10/10 çalıştırma %1'e 18,4–29,9 sn'de** (ortanca 18,9); 5 km'de 5/5, 3,0–3,4 sn. Hiçbir çalıştırma süre
  sınırını 0,5 sn'den fazla aşmadı. Dizüstü ölçülmedi (§5, §11).
- Bunu sağlayan, HiGHS'i bırakmak değil **alt sınırı başka yerden almak**: güçlü modelin kök LP'si 3 km'de tek başına
  126 sn sürüyor. Alt sınır **Lagrange gevşetmesinden** (atama kısıtları gevşetilir, problem aday başına sırt çantasına
  ayrışır; numpy) geliyor; üst sınırı **çekirdek LP'sinin yuvarlanması ve pencere araması** (küçük kesin MILP'ler,
  HiGHS) iyileştiriyor. Bu bir yöntem seçimi — öğrencinin onayına sunuluyor (§11).
- **Önceden çözülen varsayılan çözümler** (precomputed/): **5 km 69 hastane, 402,61 milyar TL — kanıtlı optimum**
  (alt sınır = üst sınır); **3 km 89 hastane (67 IV-C, 22 V-B), 381,314 milyar TL, fark %0,062**; 2 km 110 hastane,
  377,022 milyar TL, fark %0,21. 3 ve 2 km çözümleri bağımsız doğrulamanın bulduğu daha ucuz çözümlerden
  başlatıldı; 2 km'de 60 dakikalık çözüm onu da 136,9 milyon TL iyileştirdi (§4).
- **Kabul ölçütü "p taramasının en iyisi = serbest p'li çözüm" sağlanıyor** (güçlendirilmiş biçimde, §8): dip (n* ± 3,
  3 km'de ayrıca p = 83) simetrik başlangıçlarla uzun çözüldü; taramanın en iyi çözümü iki ızgarada da tam serbest
  optimumun hastane sayısında (5 km p = 69, 3 km p = 89). 5 km'de p = 69 taranan p değerleri arasında kanıtlı olarak
  tek en iyi (taramadaki diğer 29 p'nin alt sınırı serbest optimumun üstünde; en yakını p = 68, en az +4,8 milyon TL;
  taranmayan p'ler, ör. 77 ve 79, için ayrı kanıt yok); 3 km'de dip düz: 88 ve 90
  sırasıyla 3,9 ve 25,9 milyon TL pahalı, ama 82–96 arasındaki hiçbir p'nin 89'dan kötü olduğu bu gap'lerle
  kanıtlanamıyor.
- **Sınır kartları** (§7): "her talep hücresine R = 30 km içinde bir hastane" için en az **25 / 23 / 24** hastane
  (5 / 3 / 2 km, R içindeki tüm adaylarla, kanıtlı); modelin k = 50 listeleriyle 26 / 27 / 34 (kanıtlı); gerçek p_min
  27 (kanıtlı) / 28 / 35.
- Bağımsız doğrulamanın bulguları ve düzeltmeleri §10'da.
- Beklendiği gibi **çoğu hastane küçük**: 3 km'de 40 hastane tam 199 yatakta (IV-C üst sınırı), 20'si tam 399'da
  (V-B üst sınırı) — yatak başı maliyet sınıf eşiğinde sıçradığı için. Bu verinin özelliği, hata değil (§4).

## 2. Model ve boyutları

Kümeler, değişkenler, kısıtlar ve maliyet katsayıları şartname 4.5 ve öğrenci kararlarıyla birebir
(`backend/model/milp.py` başındaki tanım). Talep q_i = w_i × 3,15/1000 × (1 + 0) → toplam 18.617,5 yatak
(`demand.beds_per_1000` = 3,15, `data/reference/parameters.json`). λ = 1.902,6 TL/(kişi·km); yatak başı maliyet IV-C
15,54 / V-B 16,82 / V-C 18,70 / V-D 25,12 milyon TL; hastane başı sabit kısım 478,7 / 497,6 / 525,3 / 552,1 milyon TL
(testlerle sabitlenmiş: `tests/test_phase3_params.py`).

| Izgara | \|I\| | \|J\| | yay (k = 50, eşitlikler dahil) | satır | sütun | ikili | sıfırdan farklı | sınıf başına bağlantı satırı |
|---|---|---|---|---|---|---|---|---|
| 5 km | 1.073 | 454 | 27.639 | 34.463 | 31.725 | 1.816 | 132.037 | 1.209 |
| 3 km | 2.604 | 634 | 81.288 | 91.969 | 86.994 | 2.536 | 355.383 | 1.735 |
| 2 km | 4.842 | 891 | 162.497 | 177.419 | 170.516 | 3.564 | 686.122 | 1.168 |

(z_j = Σ_k y_jk yardımcı sütunu ve x_a ≤ z_j biçimi, x_a ≤ Σ_k y_jk ile aynı LP gevşetmesini 2 sıfırdan farklı
elemanla verir; Faz 3 çekirdeğinden.)

## 3. Çözüm yöntemi (`backend/model/solve.py`, `lagrange.py`, `lns.py`, `budget.py`)

Canlı çözüm akışı (süre sınırı T, hedef gap g; varsayılan T = 15 sn, g = %1; iç hedef g/2):

1. **Başlangıçlar**: verilen başlangıç çözümü; aynı yapıdaki önceki çözümlerin açık kümeleri (en çok 2) yeni
   parametrelerle yeniden değerlendirilir (şartname 4.6 "sıcak başlangıç"; bütçe modunda serbest çözüm bütçeye sığıyorsa
   bütçe modunun başlangıcı olur).
2. **Sezgisel** (açgözlü + yerel arama + kesin değerlendirme; bütçe modunda önce tüm talebi kapsar, sonra kalan bütçeyi
   harcar): ilk olurlu çözüm 3 / 5 km'de ≈1–1,5 sn, 2 km'de ≈5–6 sn (§5).
3. **Lagrange gevşetmesi, A evresi** (en çok 0,2·T; sınır durulunca erken biter): atama satırları Σ_a x_a = 1 gevşetilir
   (çarpan μ_i). Kalan problem aday başına ayrışır: her (j, k) için kesirli sırt çantası (ilk L_k yatak zaten ödenir,
   aşan her yatak Değişken_jk'ya mal olur, toplam ≤ U_k). Her μ için L(μ) = Σμ_i + Σ_{açık j} G_j ≤ optimum (geçerli alt
   sınır); y alt problemde ikili olduğundan Lagrange duali güçlü LP gevşetmesinden sıkıdır. Çarpanlar hacim algoritmasıyla
   (Barahona & Anbil 2000) güncellenir; başlangıç zayıf LP'nin dualleri ya da önbellekteki (aynı yapı) çarpanlar.
4. **İyileştirme evresi** (kalan süre; üst sınır tarafı):
   - *Çekirdek LP yuvarlaması* (`lns.lp_round`): en iyi μ'de açılış indirgenmiş maliyeti G_j en küçük 150 aday ∪ eldeki
     çözümün açık hücreleri, her talep hücresinin bu adaylar arasındaki en yakın 8 yayı; bu "çekirdek" problemin güçlü LP
     gevşetmesi çözülür ve z_j ≥ eşik olan adaylar açılır (kapsama onarımı, sınıflar LP'den) → kesin değerlendirme.
   - *Pencere araması* (`lns.WindowSearch`): bir merkezin çevresindeki en yakın 20 aday serbest bırakılır, çözümün geri
     kalanı sabit tutulur, küçük MILP HiGHS ile kesin çözülür (≤ 1 sn); iyileşme tam çözüme eklenir.
   - *Çekirdek MIP* (aynı çekirdek, HiGHS, **ayrı süreçte**): kök LP'den sonra ilerlemesi izlenir; iyileştiremezse ana
     süreçteki daha iyi çözümden daha geniş aday kümesiyle (200 / 250 aday, 6 yay) yeniden başlatılır.
   - *Lagrange B evresi* arka plan iş parçacığında: sınır durulana dek yükselir; çekirdeğin ve pencerelerin çözümleri
     üst sınır olarak beslenir (bütçe modunda ν aramasının A evresinin bıraktığı yerden sürdürülmesi).
5. **Cila**: en iyi açık kümede sınıf seçimli atama/boyut MIP'i (tüm yaylarla).
6. Süre sınırı ≥ 300 sn ise (önceden çözülen senaryolar) **tam MILP** (HiGHS, ayrı süreçte; Lagrange sınırına göre hedef
   gap'e ulaşınca durur); hâlâ fark > g ve süre varsa **Lagrange 2. evre**.
7. **Bağımsız denetim** (`checks.py`, 13–14 kontrol; amaç değeri ham parametrelerden sıfırdan) ve KPI'lar.

Ölçülen iş bölümü (§5'teki kıyaslama, 10'ar soğuk çalıştırma): 3 km'de 10, 2 km'de 30 çekirdek MIP denemesinin hiçbiri
eldeki çözümü iyileştirmedi; son çözümün kaynağı 3 km'de 9/10 çalıştırmada pencere araması (+ cila), 1/10'da LP
yuvarlaması (+ cila), 2 km'de 10/10 pencere araması (`solution_source`, `core_attempts`). Çekirdek MIP yine de tutuldu:
bütçe modunda 3 km'de son çözüm 3 soğuk çalıştırmanın 2'sinde çekirdek MIP'ten geliyor (aynı dosyanın `butce` anahtarı),
küçük örneklerde tam modelin kendisi olduğundan kesin ispat veriyor; bedeli ikinci bir işlemci çekirdeği.

**Durum kodları**: `optimal` (fark ≤ g) · `sure_doldu` (süre doldu) · `gap_ulasilamadi` (süre dolmadan durdu: alt ve
üst sınır yöntemleri yakınsadı, hedef gap bu yöntemlerle sayısal olarak erişilemiyor) · `durduruldu` · `sezgisel` ·
`olurlu_degil` · `cozum_yok` · `hata`. Sertifika cümlesi (şartname 7.2) sonuçta `certificate_tr`; alt sınırın kaynağı
(`lb_source`: lagrange / lagrange_butce / mip / zayif_lp), kapsamı (`lb_scope`: k_listesi / tam) ve gerçekten çalışan MIP
türü (`mip_kind`: none / core / core+full / full) her sonuçta yazılı. Alt sınır üst sınırı aşarsa (sayısal tolerans 1e-9
dışında) sessizce kırpılmaz: o kaynak geçersiz sayılır ve `bounds.lb_rejected`'a yazılır (`HASTANE_STRICT_BOUNDS=1`
iken hata).

**Süre sınırı sert**: her evre T − (cila + sonuç payı) anına göre sınırlanır; alt süreç kibarca durdurulur (son çözümü
alınır), gerekirse öldürülür. Kıyaslamadaki 25 soğuk çalıştırmanın hiçbiri T + 0,5 sn'yi aşmadı (§5);
`tests/test_phase3_lagrange.py::test_hard_time_limit` 5 / 3 / 2 km ve sabit p için aynı sınırı denetler.

**Ayrı süreç güvenliği** (Windows uyumlu `spawn`): alt süreçteki gözcü iş parçacığı ana süreç ölünce HiGHS'i durdurup
süreci kapatır (kök LP sırasında da; HiGHS orada GIL'i bırakıyor); çözüm vektörü yalnız sonda bir kez gönderilir; ana
süreç alt süreci her durumda `finally` içinde kapatır. `tests/test_phase3_process.py` çözüm sırasında ana süreci SIGKILL
ile öldürüp (çekirdek MIP sırasında ve tam MILP'in kök LP'si sırasında) 5 sn içinde HiGHS alt süreci kalmadığını
denetler.

**Bütçe modu** (`budget.py`): bütçe satırı ν ≥ 0 ile gevşetilir; açılış maliyetleri ν ile ölçeklenmiş serbest
problemin Lagrange sınırı L_ν ile LB(ν) = L_ν − ν·B erişim maliyeti için geçerli alt sınırdır, ν üzerinde en büyüklenir.
Raporlanan amaç ve sınırlar yalnız erişim maliyetidir (ε·açılış eşitlik bozucusu LB(ν)'ye girmez; MIP kaynaklı
sınırlardan ε·B düşülür). ν araması log ν üzerinde ve yönü yalnız sınır değerlerinden seçer (alt problemin açılış
maliyeti yakınsamamış çarpanlarda yanlış yönü gösterebiliyor; doğrulama bulgusu, §10): ilk ν (1 = serbest problem) →
iki yönde birer deneme → iyi yönde adımı büyüterek köşeli ayraç → altın oran araması (kısa süreli deneme yakınsamış en
iyi noktaya karşı "kararsız" kaybederse bir dilim daha alır, yine kararsızsa arama durur) → kalan süre en iyi ν'de
çarpanların yakınsatılmasına (az sayıda ν, her birine uzun süre). A evresi T'nin en çok %35'i; arama iyileştirme
evresinde arka plan iş parçacığında kaldığı yerden sürer. Çekirdek MIP ve LP yuvarlamasının adayları 1'e en yakın
değerlendirilmiş ν'nin indirgenmiş maliyetleriyle seçilir (geliştirme denemesi, depoda saklanmadı: 3 km'de en iyi ν'nin
indirgenmiş maliyetleriyle LP yuvarlaması ve çekirdek MIP pencere aramasının çözümünü iyileştirmedi). Oyuncak test:
ilk adım yanlış taraftayken arama yoğun bir ν ızgarasının en iyisine ulaşıyor
(`test_budget_nu_search_recovers_from_wrong_first_step`; eski arama orada ν = 1'de kalıyordu).

Ölçüm: varsayılan 350 milyar TL bütçe, 15 sn, 3'er soğuk çalıştırma (`data/processed/solver_benchmark.json`, `butce`
anahtarı; `pipeline/31_benchmark.py --butce`). Para milyar TL; aralıklar en az–en çok.

| Izgara | bulunan erişim maliyeti U | alt sınır L | gap | en iyi ν | son çözümün kaynağı | toplam süre |
|---|---|---|---|---|---|---|
| 5 km | 55,15–55,48 | 53,81–53,89 | %2,29–2,96 | 2 (3/3) | pencere araması + cila (3/3) | 14,7 sn |
| 3 km | 34,12–34,41 | 31,98–32,02 | %6,16–6,98 | 0,5 (3/3) | çekirdek MIP (2/3), LP yuvarlaması + cila (1/3) | 14,7–14,8 sn |

Her çalıştırmada açılış maliyeti bütçeye eşit (bütçe satırı bağlayıcı), durum `sure_doldu`; hepsi bağımsız denetimden
geçti ve süre sınırını 0,5 sn'den fazla aşmadı. 2 km bütçe modu ölçülmedi. Erişim maliyeti toplamın ≈%10'u olduğundan
(§4: 3 km'de 36,04 / 381,31) alt sınırın küçük mutlak hatası bütçe modunda göreli olarak ≈10 kat büyük görünür.

**Yeni parametre yok.** Strateji sabitleri (A evresi payı, çekirdek boyutları, pencere boyu, durma ölçütleri, hacim
algoritması ayarları) model parametresi değil, ölçümle seçilmiş modül sabitleri (`solve.py`, `lagrange.py`, `lns.py`
başında gerekçeleriyle).

## 4. Varsayılan çözümler (precomputed/default_{5,3,2}km.json)

Varsayılan parametreler; çevrimdışı uzun çözüm (`pipeline/32_precompute.py defaults`): 5 km 900 sn sınırı ve hedef gap
1e-6 (kanıtlı optimum), 3 km 1.800 sn, 2 km 3.600 sn (hedef gap 1e-4: ulaşılamaz, süre tam MILP'in alt sınırını
yükseltmeye gider). Başlangıç adayları: önceki dosyanın çözümü ve bağımsız doğrulamanın bulduğu çözümler (`--start`,
.npz: açık hücreler, sınıflar, yataklar, atamalar); en ucuzu MIP başlangıcıdır ve sonuç ondan kötü olamaz (adaylar ve
değerleri dosyanın `precomputed.start_candidates` alanında; .npz dosyaları depoda değil, ama çözümler varsayılan
dosyaların kendisinde saklı ve yeniden çalıştırmada "önceki dosya" adayı olur). Dosyalar tam sonuç sözlüğüdür: hastaneler (konum, sınıf,
yatak, maliyet kırılımı, hizmet nüfusu), atamalar, KPI'lar, denetim raporu, sertifika, iz, atama LP'sinin dual
değerleri (şartname 7.6 için).

| | 5 km | 3 km | 2 km |
|---|---|---|---|
| durum; alt sınırın kaynağı; çalışan MIP | **optimal (kanıtlı)**; tam MILP (HiGHS); çekirdek + tam | süre doldu; tam MILP (HiGHS); çekirdek + tam | süre doldu; tam MILP (HiGHS); çekirdek + tam |
| alt sınır L / bulunan U (milyar TL) | 402,6109 / 402,6109 | 381,0779 / 381,3143 | 376,2358 / 377,0216 |
| fark | %0 (HiGHS gap 0) | %0,062 | %0,208 |
| hastane (IV-C / V-B / V-C / V-D) | 69 (48 / 13 / 8 / 0) | 89 (67 / 22 / 0 / 0) | 110 (106 / 4 / 0 / 0) |
| toplam yatak (talep 18.617,5) | 18.795 | 18.729 | 18.792 |
| açılış / erişim / toplam (milyar TL) | 355,83 / 46,78 / 402,61 | 345,28 / 36,04 / 381,31 | 346,72 / 30,30 / 377,02 |
| nüfus ağırlıklı ortalama yol | 4,16 km | 3,20 km | 2,69 km |
| 10 / 20 km içindeki nüfus | %95,8 / %98,7 | %95,0 / %98,6 | %95,5 / %98,6 |
| en uzak atama | 27,0 km | 29,7 km | 29,7 km |
| 50 / 199 / 399 yataklı hastane (sınıf sınırında) | 17 / 21 / 13 | 15 / 40 / 20 | 17 / 73 / 2 |
| en düşük doluluk | %15 | %16 | %3 |
| süre | 108 sn | 1.795 sn (sınır) | 3.595 sn (sınır) |

Önceki sürüme göre (doğrulama A5/A6):
- **5 km**: çözüm aynı (402,6108961 milyar TL); alt sınır 402,28'den (fark %0,08) 402,6109'a çıktı: tam MILP 66 sn'de
  ağacı kapattı (3 düğüm). Bağımsız doğrulamanın kendi formülasyonuyla yaptığı optimallik ispatıyla tutarlı.
- **3 km**: 381,3307 → **381,3143 milyar TL** (−16,5 milyon TL, −%0,004). Merkezdeki 5 hastane birer hücre kaydı:
  kapanan 3km_152_1476, 3km_153_1474, 3km_162_1470, 3km_164_1477, 3km_167_1480; açılan 3km_153_1475, 3km_154_1473,
  3km_161_1470, 3km_165_1479, 3km_168_1480; sınıf karışımı aynı (67 IV-C / 22 V-B), kalan 84 hastaneden 10'unun yatak
  sayısı değişti. Bu çözümü bağımsız doğrulamanın büyük çekirdek MIP'i buldu (MIP başlangıcı olarak verildi); 30 dakikalık
  çözüm onu iyileştiremedi (839 pencere, 3 çekirdek MIP denemesi, tam MILP). Alt sınır 380,81'den (Lagrange) **381,08'e**
  (tam MILP; kök LP 125 sn'de, kesme turlarıyla 0 düğümde) çıktı: fark %0,14 → **%0,062**.
- **2 km**: 377,2767 → **377,0216 milyar TL** (−255,1 milyon TL, −%0,068); 109 → 110 hastane (106 IV-C, 4 V-B).
  Başlangıç bağımsız doğrulamanın çözümü (377,1585); 60 dakikalık çözümün iyileştirme evresinde pencere araması 74
  iyileşmeyle 120,9 milyon TL, son cila 16,0 milyon TL daha kazandırdı (başlangıca göre −136,9 milyon TL). Önceki
  dosyaya göre 13 hastane kapandı, 14 açıldı (çoğu bir hücre kayma), ortak kalanların 11'inin yatak sayısı değişti. Alt
  sınır 376,14'ten 376,24'e çıktı (tam MILP; kök LP 1.146 sn, 0 düğüm): fark %0,30 → **%0,208**.

Toplam maliyet ızgara inceldikçe düşüyor (402,6 → 381,3 → 377,0 milyar TL): ince ızgarada hem mesafe ölçümü (hücre
içi ortalama yolculuk) kısalıyor hem hastaneler talebe daha yakın konabiliyor; hastane sayısı 69 → 89 → 110.

3 km açılış maliyeti kırılımı (milyar TL): yapı 192,3 + KDV 53,8 + altyapı 28,8 + donanım 48,1 + hastane başı sabit
22,25 (89 × 250 milyon) = 345,3; arsa 0 (varsayılan: kamu arazisi), işletme 0 (varsayılan kapalı).

Yorum (açıklama katmanına girdi): 3 km'de 40 hastane tam 199 yatakta, 20'si tam 399'da: yatak başı maliyet 200. ve
400. yatakta sıçradığı için (15,54 → 16,82 → 18,70 milyon TL) model bir üst sınıfa geçmek yerine talebi komşu hastaneye
kaydırıyor. 15 hastane en küçük boyutta (50 yatak) — kırsal hücreler; R = 30 km kapsama kısıtı ve erişim maliyeti
yüzünden açılıyorlar, dolulukları düşük (en düşük %16; 2 km'de %3) — şartname 7.13'teki eşitlik–verimlilik
ödünleşimi. Şehir hastanesi (V-D) hiçbir ızgarada seçilmiyor (yatak başı 25,1 milyon TL).
Harita: `docs/screenshots/faz3_varsayilan_cozum_3km.png`.

Sertifika cümlesi (şartname 7.2; 3 km dosyasındaki `certificate_tr`): "Alt sınır L = 381,08 milyar TL (dal-sınır
(HiGHS)), bulunan çözüm U = 381,31 milyar TL → fark %0,06. Bu modelde (k = 50 en yakın aday listesiyle kurulan model
için) daha iyi bir çözüm varsa bile bu çözümden en fazla %0,06 daha ucuzdur."

k = 50 kırpmasının etkisi: R içindeki tüm adaylarla (k = 0, 124.064 yay) kurulan 3 km modelinin alt sınırı 381,042
(ayar deneyi T2, `data/processed/solver_tuning/T2_k_aday/ref3.jsonl`). k = 50 varsayılan çözümü (381,314) o modelde de
olurlu olduğundan iki modelin optimumu arasındaki fark en çok 381,314 − 381,042 = 0,27 milyar TL (%0,071). 5 km'de aynı
hesap: k = 0 alt sınırı 402,435 (`results_k5.jsonl`), k = 50 optimumu 402,611 → en çok %0,044.

## 5. Süreler ve şartname hedefleri (data/processed/solver_benchmark.json)

`pipeline/31_benchmark.py`: varsayılan parametreler, her çalıştırma ayrı ve yeni bir süreçte (soğuk: ızgara yükleme +
yapı + çarpanlar sıfırdan), konteynerde başka iş yokken; süre sınırı 3 ve 5 km'de 15 sn (`solver.time_limit_s`), 2 km'de
60 sn; hedef gap %1. Bu sürümde yalnız soğuk çalıştırmalar ölçüldü (3 ve 2 km'de 10'ar, 5 km'de 5); önceki sürümün
özetleri
(ılık yeniden çözümler dahil) aynı dosyada `previous` anahtarında.

| Izgara (sınır) | çalıştırma | %1'e sınır içinde ulaşan (başarı oranı) | %1'e ulaşma, sn: ortanca (en az–en çok) | %0,5'e ulaşan | toplam süre, sn: ortanca (en az–en çok) | son gap: ortanca (en az–en çok) | bulunan U / alt sınır L, milyar TL (en az–en çok) | evreler, ortanca sn: sezgisel / Lagrange A / iyileştirme / cila |
|---|---|---|---|---|---|---|---|---|
| **3 km (15 sn)** | 10 | **10/10 (%100)** | **5,7 (4,8–6,7)** | 9/10 | 10,4 (9,6–13,4) | %0,47 (%0,41–0,57) | 381,70–382,90 / 379,94–380,72 | 1,0 / 3,0 / 4,6 / 1,5 |
| **2 km (60 sn)** | 10 | **10/10 (%100)** | **18,9 (18,4–29,9)** | 2/10 | 57,1 (49,5–59,8) | %0,56 (%0,39–0,58) | 377,49–378,19 / 376,00–376,02 | 4,6 / 12,0 / 39,1 / 0,3 |
| 5 km (15 sn) | 5 | 5/5 (%100) | 3,2 (3,0–3,4) | 5/5 | 3,4 (3,1–3,5) | %0,27 (hepsi) | 403,24 / 402,17 | 1,4 / 1,9 / — / 0,1 |

Notlar: (1) solve() %1'e ulaşınca durmaz; süre içinde fark %0,5'e inene dek iyileştirme evresiyle devam eder. Şartname
hedefi için ölçüt, izden okunan **%1'e ulaşma süresi**; o andaki çözüm ilerleme akışında arayüze zaten gelir. (2) Hiçbir
çalıştırma süre sınırını 0,5 sn'den fazla aşmadı (25/25; `within_hard_limit`). (3) Bütün çalıştırmalar `optimal`
durumunda bitti ve bağımsız denetimden geçti. (4) 5 km'de A evresi farkı tek başına %0,5'in altına indiriyor;
iyileştirme evresi çalışmıyor. (5) Alt sınırlar çalıştırmalar arasında kararlı (2 km'de 376,00–376,02); üst sınırın
çalıştırmadan çalıştırmaya oynaması 3 km'de %0,3 (381,70–382,90): canlı çözümler arasındaki %1'den küçük farklar
yöntemin gürültüsü içindedir (§11).

Önceki sürümle karşılaştırma (aynı konteyner, `previous` anahtarı; 5'er soğuk çalıştırma): 3 km'de %1'e ortanca 8,7 sn
(7,1–8,8), 2 km'de 22,1–49,3 sn; yeni sürümde 3 km 5,7 sn, 2 km 18,4–29,9 sn. Bağımsız doğrulama önceki sürümün 2 km
başarısını abartılı buldu (çekirdek MIP kök düğümde takılınca kurtaran yol yoktu; §10) — bu sürümün iyileştirme evresi
bunun için yeniden kuruldu. Temel sürümle (yalnız sezgisel + tam güçlü MILP, `data/processed/solver_baseline.json`):
3 km'de ilk anlamlı alt sınır 143,8 sn'de (kök LP), %1'e 192 sn; 2 km'de 60 sn'de fark %11,0 (zayıf LP sınırı).

Hedeflerle karşılaştırma (yalnız bu konteyner, boşta):
- **3 km ≲ 15 sn: tutuyor.** 10/10 soğuk çalıştırmada %1 sertifikası 4,8–6,7 sn'de; çözüm 9,6–13,4 sn'de bitiyor.
- **2 km ≲ 60 sn: tutuyor.** 10/10 soğuk çalıştırmada %1'e 18,4–29,9 sn'de ulaşıldı; çözüm 49,5–59,8 sn'de bitiyor (süre
  %0,5 hedefiyle doluyor).
- **5 km:** 3,1–3,5 sn, %0,27.
- **Dizüstü ölçülmedi.** Yöntem evrelere süre payı verdiği için daha yavaş bir makinede süreler doğrusal ölçeklenmez;
  yüklü konteynerde (başka işler çalışırken) başarı oranının düştüğü bağımsız doğrulamada görüldü. Faz 8'de dizüstünde
  `pipeline/31_benchmark.py` çalıştırılmalı (§11).

## 6. Ayar deneyleri ve ölçülen etkiler (ham veri: `data/processed/solver_tuning/`)

Şartname 4.6 sırası (aday filtresi → k → sınıf sayısı) ve HiGHS ayarları önce denendi; hiçbiri hedefi tutturmadı.
Ayar deneyleri T1 (formülasyon, HiGHS), T2 (k, aday filtresi), T3 (Lagrange); sonuç kayıtları ve tablo satırı → dosya
eşlemesi `data/processed/solver_tuning/README.md`'de. Bütünleştirme ve düzeltme turlarının ölçümleri §5'teki kıyaslama
dosyasında. Para milyar TL.

| Değişiklik | Ölçülen etki | Karar | Veri |
|---|---|---|---|
| HiGHS ayarları: IPM (± crossover), hipo, PDLP, önsüzgeç kapalı, primal simpleks, amaç ölçeği | 3 km güçlü kök LP: IPM (crossover açık/kapalı) ve hipo 60 sn'de bitmedi (dual simpleks 126 sn); 5 km kök LP: dual simpleks 7,1 sn, amaç ölçeği 8,0, IPM (crossover kapalı) 11,1, önsüzgeç kapalı 29,5, PDLP 39,3 sn, primal simpleks 60 sn'de bitmedi | varsayılanlar korundu | T1 |
| Zayıf / kısmi (en yakın 5 yay) / kesme döngüsü bağlantı | 3 km, 60 sn MIP alt sınırı 370,2 / 376,1 / 378,0 (güçlü bağlantıda kök LP 60 sn'de bitmiyor); hiçbiri %1 değil | güçlü bağlantı korundu | T1 |
| **Sınıf başına bağlantı satırları** | 5 km kök LP sınırı 401,11 → 402,18 (7,1 → 8,5 sn); MIP sonu gap %0,31 → %0,24 | **uygulandı** | T1 |
| k (aday listesi) | k = 50 ile tümü (k = 0) arasındaki fark: 3 km'de en çok %0,071 (k = 0 alt sınırı 381,042, T2 başvurusu; k = 50 varsayılan çözümü 381,314 k = 0 modelinde de olurlu), 5 km'de en çok %0,044; k = 30: optimum en az %0,065 pahalı (kanıtlı), bulunan çözüm %0,21 pahalı (600 sn sınırlı çözüm 41 sn'de %0,15 gap'le bitti; eski "+%1,2" 60 sn'lik, alt sınırı zayıf LP olan bir çözümün üst sınırıydı); k = 10: en az %4,7 (kanıtlı), bulunan %6,2 (300 sn; 121 hastane); küçük k hızlandırmıyor (5 km'de k = 15 / 20 %1'e 97,9 / 94,2 sn, k = 50 21,2 sn) | **k = 50 korundu**; k = 0 (R içindeki tümü) seçeneği | T2, `k_karsilastirma_3km.json` |
| Aday filtresi P_min 500 / 1.000 / 2.500 | 3 km %1'e 12,1 / 7,2 / 7,2 sn ama bulunan çözüm %2,1 / %3,0 / %3,5 (kanıtlı en az %1,2 / %2,0 / %2,6) pahalı | **P_min = 250 korundu** (hız ayarı değil) | T2 |
| Şehir sınıfını (V-D) kapatmak | hiçbir optimum V-D kullanmıyor; 5 km'de %1'e 15,7 sn (açıkken 21,2 sn), 3 km'de 200 sn'de %1'e ulaşılmadı | açık kaldı | T2 |
| **Lagrange alt sınırı** (hacim algoritması) | 3 km'de güçlü LP'nin değerine (379,96; tek başına 126 sn) birkaç saniyede yaklaşıyor ya da onu geçiyor: canlı çözümlerde alt sınır 379,94–380,72 (10 çalıştırma, Lagrange A ≈3 sn); 2 km'de 376,00–376,02 (güçlü LP 60 sn'de bitmiyor) | **uygulandı** (`lagrange.py`) | T1, T3, §5 |
| Çarpan güncelleme: Polyak, ön koşullu Polyak, sapmalı Polyak, hacim algoritması | en iyi son L: hacim (θ0 0,3, 5 kırmızı adım, α0 0,3) 5 / 3 / 2 km 402,17 / 380,77 / 376,00 | hacim algoritması | T3 (`e1`, `e7`) |
| Lagrange indirgenmiş maliyetiyle sabitleme | 5 km'de 2 hücre kapalı, 14 açık sabitlenebildi; 3 ve 2 km'de hiç (hastane başı sabit maliyet boşluktan küçük) | uygulanmadı (`fixing` fonksiyonu duruyor) | T1, T3 (`e9`) |
| Kısıtlı MIP (200 aday, tüm yaylar) yerine çekirdek MIP (150 aday, talep başına 8 yay) | 2 km'de 200 adaylı kısıtlı MIP'in (tüm yaylar) kök LP'si 23 sn, 42 sn'de başlangıcını iyileştiremedi | çekirdek MIP | T3 (`e8_restricted`) |
| **LP yuvarlaması + pencere araması + duran çekirdeğin yeniden başlatılması** (doğrulama D1/D2 sonrası) | önceki sürüm (yalnız çekirdek MIP + Lagrange sezgiseli) → bu sürüm, soğuk: 3 km %1'e ortanca 8,7 → 5,7 sn; 2 km'de 10/10 çalıştırma %1'e 18,4–29,9 sn'de; son çözüm 3 km'de 9/10, 2 km'de 10/10 pencere aramasından | **uygulandı** (`lns.py`) | §5 (`previous` anahtarı: önceki sürüm) |
| Yay kontrolü (açık kümeyi tüm yaylarla değerlendirme) | %0,013 (3 km) – %0,088 (2 km) iyileşme, ama çözümü k listesi dışına taşır (şartname 4.5 N_i tanımı) | uygulanmadı; k = 0 seçeneği yerine | T2 (`arccheck_cost.jsonl`) |

## 7. Sınır kartları (data/processed/sinir_kartlari.json; şartname 4.5)

| | 5 km | 3 km | 2 km |
|---|---|---|---|
| Izgaraya sığan en fazla hastane \|J\| | 454 | 634 | 891 |
| Kapasite alt sınırı ⌈18.617,5 / U_max⌉: şehir sınıfıyla / onsuz | 5 / 19 | 5 / 19 | 5 / 19 |
| **Kapsama alt sınırı: her talep hücresine R = 30 km yol mesafesi içinde bir hastane** (LSCP, R içindeki tüm adaylar, k = 0) | **25** (kanıtlı, 1,4 sn) | **23** (kanıtlı, 2,3 sn) | **24** (kanıtlı, 4,2 sn) |
| Kapsama alt sınırı, model listeleriyle (k = 50): her talep hücresinin en yakın 50 adayından biri açık | 26 (kanıtlı, 1,3 sn) | 27 (kanıtlı, 3,5 sn) | 34 (kanıtlı, 504 sn) |
| Gerçek p_min: modelin tüm kısıtlarıyla (k = 50 listeleri, kapasite, sınıflar, R) | 27 (kanıtlı, 185 sn) | 28 (alt sınır 27; 300 sn'de kanıtlanamadı) | 35 (alt sınır 34; 600 sn'de kanıtlanamadı) |

- Kartın anlamı ("her talep hücresine R içinde bir hastane") yalnız R içindeki **tüm** adaylarla hesaplanınca doğru:
  k = 50 listeleri bir kapsama kısıtı gibi davranır (her talep hücresinin 50 en yakın adayından biri açık olmalı) ve
  değeri ızgara inceldikçe büyütür (26 → 27 → 34); bu k kırpmasının etkisidir, coğrafyanın değil (doğrulama C3).
  Geometrik en az sayı 25 / 23 / 24.
- p_min modelin kendisinde (k = 50 listeleriyle) tanımlıdır; alt sınırı p_min MIP'inin, k listeli LSCP'nin ve kapasite
  sınırının en büyüğüdür. 2 km'de k listeli LSCP'nin kanıtı (34) p_min'in alt sınırını 33'ten 34'e çıkardı; p_min
  çözümü 36'dan 35'e indi.
- Küme kapsama çözümünden önce satır ve sütun baskınlığıyla küçültme (`bounds.lscp`): N_i ⊆ N_i' ise i' satırı, bir
  adayın kapsadığı hücreler başka bir adayınkilerin alt kümesiyse o aday atılır (birim maliyette optimum değişmez;
  rastgele matrislerde kaba kuvvetle test: `test_lscp_dominance_reduction_exact`). 2 km k = 50 örneği 4.842 × 891'den
  1.826 × 788'e iner; HiGHS 504 sn'de (2.023 düğüm) kanıtladı (önceki sürüm 600 sn'de 33–34'te kalmıştı).
- (Şartnamedeki örnek "≈18.440 yatak → 19 / 5" ile aynı sonuç; talep 18.617,5.) Karşılaştırma: varsayılan optimum
  69 / 89 / 110 hastane — en az sayının 2,6–3,2 katı; aradaki fark kapsama ya da kapasite değil maliyet (erişim)
  kararıdır.

## 8. U eğrisi ve kabul ölçütü (precomputed/p_sweep_{3,5}km.json; şartname 7.7)

Tarama iki aşamalı (`pipeline/32_precompute.py sweep` ve `refine`):

1. **İlk tarama**: her p için sabit_p modunda 60 sn (3 km, hedef %0,3) / 30 sn (5 km, hedef %0,2); gap'i %1'i aşan
   noktalar 4 kat süreyle yeniden. 3 km'de 36 nokta (p = 40–150; 82–100 arası her p; 83 bu turda eklendi), 5 km'de 30 nokta
   (p = 35–120; 64–76 arası her p).
2. **Dibin sıkı çözümü** (doğrulama B3/B4): n* ± 3 (3 km'de ayrıca p = 83) yeniden çözüldü. Her p için başlangıç
   adayları **simetrik**: ilk taramadaki çözüm, serbest çözümden tek tek kapatma/açma ile türetilen çözüm, komşu p'lerin
   iyileştirilmiş çözümlerinden türetilen çözümler ve bağımsız doğrulamanın p = 88 / 90 çözümleri (3 km); p = n*'da serbest
   çözümün kendisi. Ardından büyük çekirdek MIP (G_j sıralı 300 aday, talep başına 12 yay; 3 km 330 sn, 5 km 60 sn) ve
   solve() (3 km: 240 sn, Lagrange + pencere araması + cila; 5 km: tam MILP). 5 km'de ikinci geçişte, ilk geçişten
   sonra hâlâ n*'dan ayırt edilemeyen p'ler (64, 65, 67, 68, 70, 73) tam MILP ile hedef gap 1e-5 ve 600 sn'yle yeniden
   çözüldü (geçişler dosyanın `refine.passes` alanında).
3. **Serbest alt sınır her p'ye uygulanır**: serbest problem her sabit p probleminin gevşetmesidir, bu yüzden her
   noktanın alt sınırı max(kendi alt sınırı, serbest çözümün alt sınırı)'dır (`lb_own_tl` ayrıca saklı).

**3 km dibi** (serbest optimum 89 hastane, U = 381,3143, L = 381,0779 milyar TL; fark = U(p) − U(89)):

| p | bulunan U (milyar TL) | fark (milyon TL) | kendi alt sınırı | kullanılan alt sınır | gap | fark gap içinde | IV-C / V-B / V-C | ilk taramada U (fark, milyon TL) |
|---|---|---|---|---|---|---|---|---|
| 83 | 381,8166 | +502,3 | 381,150 | 381,150 | %0,174 | evet | 58 / 24 / 1 | — (yeni nokta) |
| 86 | 381,4328 | +118,6 | 380,903 | 381,078 | %0,093 | evet | 62 / 24 / 0 | 381,8579 (+527,1) |
| 87 | 381,4333 | +119,1 | 380,841 | 381,078 | %0,093 | evet | 64 / 23 / 0 | 381,7751 (+444,4) |
| 88 | 381,3182 | +3,9 | 380,801 | 381,078 | %0,063 | evet | 65 / 23 / 0 | 381,7594 (+428,7) |
| **89** | **381,3143** | 0 | 380,752 | 381,078 | %0,062 | (n*) | 67 / 22 / 0 | 381,3307 (0) |
| 90 | 381,3402 | +25,9 | 380,801 | 381,078 | %0,069 | evet | 68 / 22 / 0 | 381,3609 (+30,1) |
| 91 | 381,4068 | +92,5 | 380,827 | 381,078 | %0,086 | evet | 70 / 21 / 0 | 381,6358 (+305,1) |
| 92 | 381,4745 | +160,3 | 380,852 | 381,078 | %0,104 | evet | 72 / 20 / 0 | 382,0941 (+763,4) |

(İlk taramadaki farklar o zamanki serbest çözüme, 381,3307'ye göre.) 88 ile 89 arasındaki fark ilk taramada 428,7 milyon
TL görünüyordu; sıkı çözümde 3,9 milyon TL (≈110 kat küçük) — doğrulama B3'ün "≈100 kat abartılı" bulgusu doğrulandı.
p = 88 ve 90'ın çözümleri bağımsız doğrulamanın çözümleridir (MIP başlangıcı olarak verildi, 10 dakikada
iyileştirilemedi); diğer p'lerin çözümleri bu turda bulundu (p = 86'nın en ucuz çözümü p = 87'ninkinden 0,5 milyon TL
ucuz: dipte sıralama gürültülü).

**5 km dibi** (serbest optimum 69 hastane, kanıtlı optimum U = L = 402,6109 milyar TL):

| p | bulunan U (milyar TL) | fark (milyon TL) | alt sınır | gap | fark gap içinde | IV-C / V-B / V-C | ilk taramada U (fark, milyon TL) |
|---|---|---|---|---|---|---|---|
| 64 | 402,9591 | +348,2 | 402,9577 | %0,0003 | hayır | 38 / 18 / 8 | 402,9591 (+348,2) |
| 65 | 402,7976 | +186,7 | 402,7946 | %0,0007 | hayır | 41 / 16 / 8 | 402,7976 (+186,7) |
| 66 | 402,6995 | +88,6 | 402,6231 | %0,019 | hayır | 43 / 15 / 8 | 402,7167 (+105,8) |
| 67 | 402,6540 | +43,1 | 402,6500 | %0,0010 | hayır | 44 / 15 / 8 | 402,6612 (+50,3) |
| 68 | 402,6192 | +8,3 | 402,6157 | %0,0009 | hayır | 46 / 14 / 8 | 402,6192 (+8,3) |
| **69** | **402,6109** | 0 | 402,6109 | 0 | (n*) | 48 / 13 / 8 | 402,6109 (0) |
| 70 | 402,6600 | +49,1 | 402,6572 | %0,0007 | hayır | 49 / 13 / 8 | 402,6954 (+84,5) |
| 71 | 402,7393 | +128,4 | 402,6803 | %0,015 | hayır | 51 / 12 / 8 | 402,7855 (+174,6) |
| 72 | 402,9080 | +297,1 | 402,8286 | %0,020 | hayır | 52 / 12 / 8 | 402,9417 (+330,8) |
| 73 | 403,0513 | +440,4 | 403,0475 | %0,0009 | hayır | 54 / 11 / 8 | 403,0934 (+482,5) |

5 km'de p ≠ 69 noktalarının alt sınırı kendi tam MILP'lerinden geliyor ve hepsi serbest optimumun üstünde: n*'dan
ayırt edilemeyen p yok. 5 km'de p = 70'in eski değeri %0,009 kötüydü (402,6954 → 402,6600), p = 73'ünki %0,010.

| | 3 km | 5 km |
|---|---|---|
| p değerleri | 36 nokta, p = 40–150 | 30 nokta, p = 35–120 |
| taramanın en iyisi | **p = 89, 381,3143 milyar TL** | **p = 69, 402,6109 milyar TL** |
| serbest p'li optimum (varsayılan çözüm) | **89 hastane, 381,3143** (alt sınır 381,0779) | **69 hastane, 402,6109** (kanıtlı optimum) |
| hiçbir p serbest alt sınırın altında değil | evet | evet |
| "fark gap içinde" (n*'dan ayırt edilemeyen) p'ler | 82–88, 90–96 | yok (yalnız n* = 69) |
| bunlardan alt sınırı serbest çözümün %0,02'den fazla altında kalan ("çözülmemiş") | 83–88, 90–95 (12 nokta) | yok (serbest çözüm kanıtlı optimum) |
| uç noktalar | p = 40: +%11,9; p = 150: +%5,6 | p = 35: +%19,3; p = 120: +%5,2 |
| gap, p ≠ n* noktaları üzerinden: ortanca (en kötü) | %0,165 (%2,3, p = 40) | %0,129 (%3,6, p = 40); dip (64–73) ≤ %0,02 |

**Kabul ölçütü** (güçlendirilmiş; `acceptance` alanı ve
 `tests/test_phase3_outputs.py::test_p_sweep_best_equals_free_optimum`):
(1) dip simetrik başlangıçlarla sıkı çözüldü ve taramanın en iyi üst sınırı tam p = n*'da (başka hiçbir p, serbest
çözümden türetilen başlangıçlara rağmen, serbest çözümden ucuz değil); (2) hiçbir p'nin çözümü serbest alt sınırın
altında değil; (3) düzlük dürüstçe raporlanıyor: alt sınırı serbest çözümün değerini aşmayan p'ler "fark gap içinde"
işaretli, alt sınırı serbest çözümün %0,02'sinden fazla altında kalanlar "çözülmemiş" listesinde. Eski test p = n*
noktasını serbest çözümle tohumlayıp yalnız o noktanın değerine bakıyordu (doğrulama B3).

**Sonuç.** İki ızgarada da taramanın en iyi çözümü serbest p'li optimumun hastane sayısında. 3 km'de dip düz: 82–96
arasındaki hiçbir p'nin n*'dan kötü olduğu alt sınırlarla kanıtlanamıyor (her birinin alt sınırı serbest çözümün
değerinin altında; serbest çözümün kendi belirsizliği 236 milyon TL, %0,062). "Neden tam 89?" sorusunun dürüst yanıtı:
"88–90 ekonomik olarak eşdeğer (fark 3,9–25,9 milyon TL, toplamın on binde birinden az); 89 bulunan en iyisi, ama bu
gap'lerle tek optimum olduğu kanıtlanamıyor". 5 km'de ise sonuç kesin: serbest optimum kanıtlı (alt sınır = üst sınır)
ve taramadaki diğer 29 p'nin her birinin alt sınırı ondan büyük — **p = 69, taranan p değerleri arasında tek en iyi
hastane sayısı** (taranmayan p'ler, ör. 77 ve 79, için ayrı kanıt yok); en yakın rakip p = 68 kanıtlı olarak en az
4,8 milyon TL (bulunan çözümüyle 8,3 milyon TL) pahalı.
Açıklama katmanı (şartname 7.3, "N−1 ve N+1 ile kıyas") bu dosyadaki `fark_tl` ve `fark_gap_icinde` alanlarını
kullanmalı; canlı çözümlerin (%1 gap) farkları bunun için yeterince kesin değil.

Şekil: `docs/screenshots/faz3_u_egrisi_3km.png` (sol: bileşenler; sağ: dipte serbest optimuma göre fark ve alt sınır …
bulunan çözüm çubukları). Bulgu (açıklama katmanına): U'nun sol kolu yalnız erişimden değil açılıştan da gelir — az
hastanede hastaneler büyük sınıflara çıkar ve yatak başı maliyet artar (5 km'de p = 35'te açılış 408,2, p = 69'da 355,8
milyar TL). Sağ kolda her ek hastane sabit kısım ve en az 50 yatak getirir; erişim neredeyse düz kalır.

## 9. Doğrulama ve testler

- **Kaba kuvvet = MILP** (şartname 4.6, 10): `tests/test_phase3_bruteforce.py` — 5–6 hücreli rastgele oyuncak
  örneklerde tüm açık kümeler × sınıf seçimleri sayılıyor, atama LP'si bağımsız (scipy linprog) kuruluyor; MILP (güçlü,
  sınıf başına bağlantı dahil, ve zayıf) aynı optimumu veriyor: 5 örnek + 4 mod (sabit p, en az, en çok, kilit) +
  bütçe modu + solve() boru hattı (ayrı süreçte ve aynı süreçte).
- **Lagrange** (`tests/test_phase3_lagrange.py`): alt problem bağımsız LP kurulumuyla aynı G_jk'yı veriyor; L(μ) ≤
  kaba kuvvet optimumu (rastgele ve yakınsamış μ; toplam, sabit p, en az, en çok, kilit, α = 1,5 + arsa); bütçe
  satırının ν gevşetmesi her ν için geçerli ve solve() bütçe modunda kaba kuvvet optimumunu buluyor; ν araması ilk
  adımı yanlış taraftayken de yoğun ν ızgarasının en iyisine ulaşıyor (en iyi ν < 1 ve > 1); durum etiketleri
  (yasak kilit, erişilemeyen gap → `gap_ulasilamadi`); sınıf başına satırlar MILP'i değiştirmiyor ve LP sınırını
  yükseltiyor; 5 km sertifika; 3 km canlı hedef (≤ 15 + 0,5 sn); sert süre sınırı (5 / 3 / 2 km ve sabit p); k = 0
  yapısı; çekirdek MIP sırasında iptal.
- **Ayrı süreç güvenliği** (`tests/test_phase3_process.py`): ana süreç çekirdek MIP sırasında ve tam MILP'in kök LP'si
  sırasında SIGKILL ile öldürülüyor; 5 sn içinde HiGHS alt süreci kalmıyor; kibar durdurma son çözümü döndürüyor.
- **Motor** (`tests/test_phase3_engine.py`): denetleyicinin ihlal yakalaması, modlar, kilitler, bütçe modu varsayılan
  bütçeyle (5 ve 3 km; olurlu, alt sınırlı, serbest çözümden kötü değil), olursuzluk mesajları, iptal, sınır kartları
  (k = 0 ve k = 50 kapsama satırları), küme kapsama küçültmesinin kaba kuvvetle doğrulanması (rastgele matrisler,
  kilitlerle), dual değerler, izinli paketler.
- **Saklanan çıktılar** (`tests/test_phase3_outputs.py`): varsayılan çözümler denetimden geçmiş, tam ve hiçbir
  başlangıç adayından kötü değil; p taraması güçlendirilmiş kabul ölçütü (§8: dip yeniden çözülmüş ve simetrik
  başlatılmış, en iyi üst sınır p = n*'da, serbest alt sınır her noktada, "fark gap içinde" işaretleri ve düzlük
  yeniden hesaplanıp karşılaştırılıyor); sınır kartları (k = 0 ve k = 50 değerleri kanıtlı ve bağımsız doğrulamanın
  değerleriyle aynı); kıyaslama dosyası (başarı oranı, sert süre sınırı, önceki sürümün özetleri).
- Her çözümde otomatik denetim (`checks.py`, 13–14 kontrol): tam atama, R (koordinatlardan yeniden), hücre başına bir
  hastane, sınıf sınırları, kapasite, kilitler, sayı, bütçe, amacın sıfırdan yeniden hesabı.
- Tam test takımı (`.venv/bin/python -m pytest -q tests/`; Faz 1–2 veri/ızgara testleri dahil). Test çalıştırması
  notu (bu sürüm, dosyada saklanmıyor): 203 test geçti. Zaman ölçen testler payla yazıldı.

## 10. Doğrulama bulguları ve düzeltmeler

Faz 3 çıktıları iki bağımsız doğrulamadan geçti: **doğruluk** (model verisi, maliyet katsayıları, kısıtlar, amaç, oyuncak
örneklerde kaba kuvvet, Lagrange sınırı — kendi numpy/scipy/highspy kodlarıyla) ve **optimallik / başarım** (kendi
formülasyonları, Lagrange alt problemi ve küme kapsama çözümleriyle). Model, maliyetler, kısıtlar, amaç değerleri, 5 km
optimumu, Lagrange sınırının geçerliliği ve kaba kuvvet karşılaştırmaları doğrulandı; hiçbir alt sınır ya da gap iddiası
yanlış çıkmadı (hiçbir ızgarada iddia edilen alt sınırın altında çözüm bulunamadı). Bulunan sorunlar ve düzeltmeleri:

| Bulgu | Önem | Düzeltme | Şimdiki durum (kaynak) |
|---|---|---|---|
| Bütçe modu varsayılan 350 milyar TL bütçeyle 5 km'de çözüm bulamıyordu, 3 km'de serbest çözümden kötü çözüm veriyordu; alt sınırı yoktu | yüksek | serbest çözümle tohumlama; sezgisel önce kapsar sonra kalan bütçeyi harcar; bütçe satırının ν ile Lagrange gevşetmesi; raporlanan sınırlardan ε eşitlik bozucusu çıkarıldı (§3) | 5 / 3 km testte olurlu, alt sınırlı ve serbest çözümden kötü değil (`test_budget_mode_default_budget`; oyuncaklarda kaba kuvvete eşit: `test_budget_lagrange_bound_valid_on_toys`) |
| Kapsama kartı (LSCP) "R içinde bir hastane" diyor ama k = 50 listeleriyle hesaplanıyordu (26 / 27 / 34); 2 km değeri kanıtsız | orta | kart R içindeki tüm adaylarla (k = 0) yeniden; k = 50 değeri ayrı ve etiketli ikinci satır; satır/sütun baskınlığıyla küçültme → 2 km de kanıtlı | R içindeki tüm adaylarla 25 / 23 / 24, k = 50 listeleriyle 26 / 27 / 34 — hepsi kanıtlı (`data/processed/sinir_kartlari.json`, §7) |
| Önceden çözülmüş 3 / 2 km çözümlerinden daha ucuzları vardı (gap içinde) | düşük | doğrulamanın çözümleri MIP başlangıcı; 3 km 30 dk, 2 km 60 dk, 5 km kanıtlı optimuma uzun çözüm | 5 km kanıtlı optimum (402,6109; alt sınır = üst sınır); 3 km 381,3307 → 381,3143 (fark %0,14 → %0,062); 2 km 377,2767 → 377,0216 (fark %0,30 → %0,21) (§4) |
| U eğrisinin dibindeki değerler kötüydü (3 km'de 88 ile 89 farkı ≈100 kat abartılı); kabul testi zayıftı (yalnız p = n* serbest çözümle tohumlanıyordu); p = 83 eksikti | orta | n* ± 3 (+ 83) simetrik başlangıçlarla (serbest çözümden ve komşu p'lerden türetilen) uzun yeniden çözüm; her noktada alt ve üst sınır; "fark gap içinde" işareti; güçlendirilmiş kabul testi | 3 km'de 88 − 89 farkı 428,7 → 3,9 milyon TL; düzlük (82–88, 90–96) "fark gap içinde" işaretli; 5 km'de p = 69 taranan p'ler arasında kanıtlı tek en iyi (§8) |
| 2 km 60 sn'de %1 başarısı abartılıydı; çekirdek MIP kök düğümde takılınca kurtaran yol yoktu | orta–yüksek | LP yuvarlaması + pencere araması + duran çekirdeğin yeniden başlatılması + Lagrange B'nin arka planda sürmesi (§3) | 2 km 10/10, 3 km 10/10 soğuk çalıştırmada %1 (§5, `solver_benchmark.json`) |
| "Dizüstü ≈%40 yavaşsa bile 15 sn içinde" iddiası desteksizdi | orta | iddia kaldırıldı; dizüstü ölçümü Faz 8'e | §5, §11 |
| Çekirdek MIP alt süreci ana süreç ölünce yaşamaya devam ediyordu | orta | gözcü iş parçacığı, kibar durdurma, sonda tek ileti, `os._exit`, `finally` temizliği (§3) | `tests/test_phase3_process.py` |
| Hedef gap < 1e-3 iken optimal çözümler "süre doldu" etiketi alıyordu; yasak kilitli yaylar çekirdeği tam modelden ayırıyordu | düşük | çekirdeğin kendi gap'i min(1e-3, g/2); yasak kilitli yaylar karşılaştırmada sayılmaz; yeni durum `gap_ulasilamadi` | `test_status_label_closed_lock_and_unreachable_gap` |
| `lb = min(lb, ub)` kırpması geçersiz bir alt sınırı gizleyebilirdi | düşük | kırpma kaldırıldı; aşan kaynak reddedilip kaydedilir (§3) | `bounds.lb_rejected` |
| Süre sınırı (özellikle yüklü makinede) birkaç saniye aşılabiliyordu | düşük | sert süre bütçesi (§3) | 25/25 soğuk çalıştırma ≤ T + 0,5 sn; `test_hard_time_limit` |
| `bounds.mip_kind` gerçekte çalışmayan tam MILP'i "full" yazıyordu | düşük | gerçekten çalışanlar yazılıyor (none / core / core+full / full) | varsayılan dosyalarda 5 / 3 km "core+full", 2 km "core+full" |
| Bütçe modu ν araması yönü alt problemin açılış maliyetinden alıyordu; yakınsamamış çarpanlarda bu yanlış yönü gösterince (3 km) arama yalnız ters yöne gidip ν = 1'de kalıyor, alt sınır zayıf kalıyordu (geçerli ama gap geniş); rapordaki bütçe gap aralıkları saklı veriye dayanmıyordu | düşük–orta | iki yönlü deneme + köşeli ayraç + altın oran + en iyi ν'de uzun yakınsatma; arama iyileştirme evresinde arka planda sürer; çekirdek adayları ν ≈ 1'in indirgenmiş maliyetleriyle; bütçe çalıştırmaları kıyaslama dosyasına yazıldı | `test_budget_nu_search_recovers_from_wrong_first_step`; `solver_benchmark.json` `butce` (§3) |
| Rapor: q_i katsayısı 3,12 yazılmıştı (kayıt 3,15) | düşük | düzeltildi | §2 |
| Rapor: "k = 30 → +%1,2" 60 sn'lik bir çözümün üst sınırıydı (alt sınır zayıf LP) | düşük | uzun çözümle yeniden ölçüldü, aralık olarak verildi | k = 30: en az +%0,065 (kanıtlı), bulunan +%0,21; k = 10: en az +%4,7, bulunan +%6,2 (§6) |
| Rapor: ayar deneylerinin verisi depoda yoktu | düşük | sonuç dosyaları `data/processed/solver_tuning/`e kopyalandı (README tablo → dosya eşlemesi) | §6 |
| Canlı senaryo karşılaştırmalarının (şehir sınıfı aç/kapa, kilit maliyeti, duyarlılık) farkları %1 gap'ten küçük olabilir | düşük–orta | açık konu (Faz 6): bu karşılaştırmalar sıkı gap ya da sıcak başlangıçla yapılmalı | §11 |

## 11. Açık konular ve öğrencinin kararları

1. **Çözüm yöntemi (onay gerekiyor).** Şartname 4.6 "highspy (HiGHS)" diyor. HiGHS hâlâ kullanılıyor (çekirdek MIP,
   pencere alt MILP'leri, değerlendirme LP/MIP'leri, uzun çözümlerde tam MILP), ama canlı çözümde **optimallik
   sertifikasının alt sınırı Lagrange gevşetmesinden** geliyor; HiGHS tam modelin kök LP'sini 3 km'de 126 sn'de bitiriyor
   (`solver_baseline.json`). Seçenekler: (a) bu yöntem (önerilen; ders için de anlatılabilir: Lagrange duali, alt gradyan,
   sertifika); (b) yalnız HiGHS: 3 km canlı hedefi tutmaz (%1'e 192 sn), 2 km'de 60 sn'de sertifika yok.
2. **2 km'de 60 sn.** Konteynerde 10/10 soğuk çalıştırma %1'e 18,4–29,9 sn'de ulaştı. Dizüstü ölçülmedi; pay
   yetmezse seçenekler: (a) 2 km'yi "önceden çözülmüş + canlı en çok 60 sn, sertifikalı değilse farkı göster" olarak
   bırakmak (arayüz farkı ve alt sınırı her zaman gösteriyor); (b) 2 km süre sınırını 90–120 sn yapmak; (c) 2 km hedef
   gap'ini %1,5 yapmak. (Aday filtresi ve k ile hızlandırma önerilmez: §6.)
3. **Dizüstü hızı bilinmiyor.** Bütün süreler bu konteynerde; yöntem evrelere süre payı verdiği için yavaş ya da yüklü
   bir makinede süreler doğrusal ölçeklenmez ve başarı oranı düşebilir. Faz 8'de dizüstünde `pipeline/31_benchmark.py`
   çalıştırılmalı. Süre sınırı her durumda sert (≤ T + 0,5 sn) ve çözüm `sure_doldu` ile farkıyla döner.
4. **Gap %1'in anlamı ve canlı karşılaştırmalar (Faz 6).** Canlı çözümler %0,4–0,6 farkla bitiyor ve üst sınır aynı
   ayarda çalıştırmadan çalıştırmaya ≈%0,3 oynuyor (§5); p'ye göre maliyet farkları (U eğrisinin dibi) bundan da küçük
   (§8). Şartname 7.3 "Neden N hastane? N−1 ve N+1 ile kıyas" için önceden çözülmüş sıkı çözümler ve "fark gap içinde"
   işaretli p taraması kullanılmalı; canlı senaryo kıyasları (şehir sınıfı aç/kapa, kilit maliyeti, duyarlılık) %1'den
   küçük farklarda "fark gap içinde" diye etiketlenmeli ya da sıcak başlangıç ve sıkı gap ile çözülmeli.
5. **Bütçe modunun gap'i geniş.** Alt sınır artık var (bütçe satırının Lagrange gevşetmesi) ama erişim maliyeti
   toplamın ≈%10'u olduğundan göreli gap büyük: varsayılan bütçeyle ve 15 sn'de 5 km %2,29–2,96, 3 km %6,16–6,98
   (3'er soğuk çalıştırma, `solver_benchmark.json` `butce` anahtarı, §3; 2 km ölçülmedi). Bütçe modunda sertifika
   beklenmemeli; arayüz farkı göstermeli.
6. **k = 50** korundu; "R içindeki tüm adaylar" (k = 0) seçeneği var (3 km'de 124.064 yay). Varsayılanı değiştirmek
   öğrencinin kararı; fark 3 km'de en çok %0,071 (§6).
7. **p_min kanıtı.** 3 km'de p_min 28 (alt sınır 27; 300 sn), 2 km'de 35 (alt sınır 34; 600 sn) kanıtlanamadı; 5 km'de
   27 kanıtlı (§7). Kartta "en az L, bulunan U" diye gösterilmeli.
8. **Faz 4 için teknik notlar.** (a) solve() Lagrange döngüsünü çağıran iş parçacığında çalıştırır (numpy + Python,
   GIL tutar): FastAPI sunucusu donmasın diye çözüm ayrı bir süreçte ya da iş parçacığında çalıştırılmalı, ilerleme
   olayları (progress_cb: lagrange / cozum / phase / uyari) kuyruktan akıtılmalı. (b) Çekirdek MIP ve uzun çözümlerdeki
   tam MILP `multiprocessing` (spawn) kullanır: Windows'ta sunucunun giriş betiği `if __name__ == "__main__":` korumalı
   olmalı. (c) Çarpan ve çözüm önbellekleri (aynı yapı) süreç içindedir; sunucu süreci kalıcı olursa ılık çözümler
   hızlanır. (d) Canlı çözüm iki işlemci çekirdeği kullanır (çekirdek MIP alt süreci + ana süreç).
9. **Kırsal küçük hastanelerin düşük doluluğu** R = 30 km kapsama kısıtının sonucu; açıklama katmanında anlatılmalı (§4).

## 12. Dosyalar

- Motor: `backend/model/milp.py` (sınıf başına bağlantı satırları; ayrı süreçte çözüm `start_isolated` / `IsolatedRun`,
  gözcü iş parçacığı, kibar durdurma, sert `deadline`), `backend/model/lagrange.py` (Lagrange alt sınırı ve sezgiseli),
  `backend/model/lns.py` (LP yuvarlaması, pencere araması), `backend/model/budget.py` (bütçe satırının Lagrange
  gevşetmesi), `backend/model/solve.py` (akış, sert süre bütçesi, durum kodları, sertifika cümlesi, önceki çözüm
  önbelleği), `backend/model/heuristics.py` (bütçe sezgiseli), `backend/model/bounds.py` (sınır kartları; R içindeki
  tüm adaylarla ve k listeleriyle küme kapsama, baskınlık küçültmesi), `backend/model/evaluate.py`,
  `backend/model/params.py` + `instance.py` (k = 0).
- Kayıt: `data/reference/params/izgara.yaml` (`grid.k_nearest` ölçüm gerekçesi, 0 seçeneği; `grid.candidate_min_pop`
  ölçüm notu) → `pipeline/build_registry.py`.
- Betikler: `pipeline/31_benchmark.py` (`--butce`: bütçe modu), `pipeline/32_precompute.py` (defaults / bounds / sweep / refine / kcheck),
  `pipeline/33_faz3_figures.py`; `pipeline/30_solve_default.py --baseline` (yalnız HiGHS temel ölçümü).
- Çıktılar: `data/processed/solver_benchmark.json`, `data/processed/solver_baseline.json`,
  `data/processed/sinir_kartlari.json`, `data/processed/solver_tuning/` (ayar deneylerinin ham sonuçları + README),
  `precomputed/default_{5,3,2}km.json`, `precomputed/p_sweep_{3,5}km.json`, `docs/screenshots/faz3_*.png`.
- Testler: `tests/test_phase3_bruteforce.py`, `test_phase3_engine.py`, `test_phase3_lagrange.py`,
  `test_phase3_outputs.py`, `test_phase3_params.py`, `test_phase3_process.py`.

Kaynakça (Crossref'te doğrulandı, ayar deneyi T3): Geoffrion & McBride 1978, AIIE Transactions 10:40–47,
doi:10.1080/05695557808975181; Cornuéjols, Sridharan & Thizy 1991, EJOR 50:280–297, doi:10.1016/0377-2217(91)90261-S;
Barahona & Anbil 2000, Mathematical Programming 87:385–399, doi:10.1007/s101070050002; Held, Wolfe & Crowder 1974,
Mathematical Programming 6:62–88, doi:10.1007/BF01580223; küme kapsama: Toregas, Swain, ReVelle & Bergman 1971,
Operations Research 19(6):1363–1373, doi:10.1287/opre.19.6.1363.
