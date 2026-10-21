"""Maliyet alanı: tebliğ birim maliyet tablosu, hastane boyut örnekleri ve KYP alan türetmeleri.

Girdiler:
  data/raw/maliyet/                               arşiv belgeleri (git dışı; eksikse indirilir, varsa atlanır)
  data/reference/yatirim_programi_2026_saglik.csv 2026 KYP sağlık satırları (pipeline/14_investment_program.py; yalnız okunur)

Çıktılar:
  data/reference/tebligat_2026_birim_maliyet.csv  2026 tebliği sağlık yapıları sınıf/fiyat tablosu
  data/reference/hastane_boyut_ornekleri.csv      yatak / kapalı alan örnekleri (her ifade arşiv belgesinde aranır)
  data/processed/kyp_alan_yatak.json              KYP m²/yatak, alan = A0 + a·yatak, makine-teçhizat oranları
                                                  (kaynak kaydı: maliyet_hesap_kyp_alan)

Çalıştırma (repo kökünden):  .venv/bin/python pipeline/18_costs.py [--offline]
  --offline: hiç indirme denemez (arşivde eksik belge varsa ilgili adım hata verir).
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import common  # noqa: E402

RAW_DIR = common.RAW / "maliyet"
OUT_TEBLIG = common.REFERENCE / "tebligat_2026_birim_maliyet.csv"
OUT_SIZES = common.REFERENCE / "hastane_boyut_ornekleri.csv"
OUT_KYP_JSON = common.PROCESSED / "kyp_alan_yatak.json"
KYP_CSV = common.REFERENCE / "yatirim_programi_2026_saglik.csv"

SRC_TEBLIG_2026 = "maliyet_rg_2026_birim_maliyet_teblig"

# ---------------------------------------------------------------- indirme (arşiv)

DOWNLOADS = {
    # Resmî Gazete — 2026 ve 2025 tebliğleri
    "RG_20260203-4_yapi_yaklasik_birim_maliyetleri_2026.htm":
        "https://www.resmigazete.gov.tr/eskiler/2026/02/20260203-4.htm",
    "RG_20260203_fihrist.htm": "https://www.resmigazete.gov.tr/eskiler/2026/02/20260203.htm",
    "RG_20260203_tam.pdf": "https://www.resmigazete.gov.tr/eskiler/2026/02/20260203.pdf",
    "RG_20250131-3_yapi_yaklasik_birim_maliyetleri_2025.htm":
        "https://www.resmigazete.gov.tr/eskiler/2025/01/20250131-3.htm",
    # Balcalı makalesi (spec [11])
    "dergipark_704217_balcali.html": "https://dergipark.org.tr/tr/pub/cukurovaummfd/article/704217",
    "dergipark_704217_balcali.pdf": "https://dergipark.org.tr/tr/download/article-file/1007709",
    # spec [10] ikincil metin (çapraz kontrol)
    "alomaliye_2026_yapi_yaklasik_birim_maliyetleri.html":
        "https://www.alomaliye.com/2026/02/03/2026-yili-mimarlik-ve-muhendislik-hizmet-bedellerinin-hesabinda-kullanilacak-yapi-yaklasik-birim-maliyetleri/",
    # Mekânsal Planlar Yapım Yönetmeliği + EK-2 (yatak başına alan standardı)
    "mevzuat_7.5.19788_mekansal_planlar_yapim_yonetmeligi.pdf":
        "https://www.mevzuat.gov.tr/MevzuatMetin/yonetmelik/7.5.19788.pdf",
    "mevzuat_7.5.19788-ek.zip": "https://www.mevzuat.gov.tr/MevzuatMetin/yonetmelik/7.5.19788-ek.zip",
    # Esnek hastane tasarımı makalesi (2010 Kılavuz s. 45 alıntısı)
    "dergipark_ehta_899977_karakas_sonmez_2021.pdf": "https://dergipark.org.tr/en/download/article-file/1649898",
    "dergipark_ehta_899977.html": "https://dergipark.org.tr/en/pub/ehta/issue/62985/899977",
    # Boyut örnekleri (spec [12], [13], [16]–[20])
    "memurlar_757810_sehir_hastanesi_yatirimi.html":
        "https://www.memurlar.net/haber/757810/sehir-hastanesi-yatirimi-tam-gaz.html",
    "medikalakademi_bilkent_sehir_hastanesi.html":
        "https://www.medikalakademi.com.tr/hastane/ankara-bilkent-sehir-hastanesi-randevu-iletisim-ve-konum/",
    "tbmm_tutanak_4125.pdf": "https://tbmm.gov.tr/Tutanaklar/TutanakGoster/4125",
    "kalkinmakutuphanesi_ankara_saglik_termal_2017.pdf":
        "https://www.kalkinmakutuphanesi.gov.tr/assets/upload/dosyalar/ankara-saglik-termal-2017.pdf",
    "sygm_TR-86211_bahce_50_yatak.html":
        "https://sygm.saglik.gov.tr/TR-86211/osmaniye-bahceye-50-yatakli-hastane-mujdesi.html",
    "cnnturk_dikili_75_yatak.html":
        "https://www.cnnturk.com/yerel-haberler/izmir/dikili/dikilinin-75-yatakli-yeni-devlet-hastanesi-hizmete-girdi-2404810",
    "sygm_TR-84485_cayirova_200_yatak.html":
        "https://sygm.saglik.gov.tr/TR-84485/kocaeli-cayorovaya-200-yatakli-hastane.html",
    "sygm_TR-92828_etlik_sehir_hastanesi.html":
        "https://sygm.saglik.gov.tr/TR-92828/turkiyenin-alan-olarak-en-buyugu-etlik-sehir-hastanesi-bugun-aciliyor.html",
    # Şehir hastaneleri programı: yatak, kapalı alan, tıbbi donanım
    "dergipark_972597_savas_2020.pdf": "https://dergipark.org.tr/tr/download/article-file/972597",
    "dergipark_1105037_aliefendioglu_2021.pdf": "https://dergipark.org.tr/tr/download/article-file/1105037",
    # KDV — Cumhurbaşkanı Kararı 7346 (RG 07.07.2023/32241)
    "RG_20230707-11_KDV_karar_7346.pdf": "https://www.resmigazete.gov.tr/eskiler/2023/07/20230707-11.pdf",
    "RG_20230707_fihrist.htm": "https://www.resmigazete.gov.tr/eskiler/2023/07/20230707.htm",
}


def ensure_downloads(offline: bool = False) -> None:
    """Eksik belgeleri indirir; var olanları atlar (manifest ilk indirmede yazıldı)."""
    missing = [n for n in DOWNLOADS if not (RAW_DIR / n).exists()]
    print(f"arşiv: {len(DOWNLOADS) - len(missing)}/{len(DOWNLOADS)} belge var")
    for name in missing:
        if offline:
            print("EKSİK (çevrimdışı, indirilmedi):", name)
            continue
        try:
            rec = common.download(DOWNLOADS[name], RAW_DIR / name)
            print("indirildi:", name, rec["bytes"], rec["sha256"][:12])
        except Exception as exc:  # noqa: BLE001 — ağ hatası: kalan adımlar arşivle devam eder
            print("İNDİRİLEMEDİ:", name, DOWNLOADS[name], repr(exc))


# ---------------------------------------------------------------- 2026 tebliği

# Sağlık yapısı içeren kalemler (insan sağlığı; 'Hayvan hastaneleri' bilinçli olarak dışarıda)
HEALTH_RE = re.compile(r"hastane|sağlı|tıp merkez|fizik tedavi|112 acil", re.IGNORECASE)
EXCLUDE_RE = re.compile(r"hayvan", re.IGNORECASE)
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
SPEC_UNIT_COSTS = {"IV-C": 40_500, "V-A": 42_350, "V-B": 43_850, "V-C": 48_750, "V-D": 53_500}  # Bölüm 3


def html_text(path: Path) -> str:
    """RG/haber sayfasını düz metne çevirir (önce windows-1254, sonra utf-8)."""
    from bs4 import BeautifulSoup

    raw = path.read_bytes()
    for enc in ("windows-1254", "utf-8"):
        try:
            t = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    s = BeautifulSoup(t, "lxml")
    for x in s(["script", "style"]):
        x.decompose()
    return re.sub(r"\s+", " ", s.get_text(" ", strip=True))


def parse_teblig(text: str) -> list[dict]:
    """'X. SINIF (Y) GRUBU YAPILAR …… N TL/m²' başlıklarını ve numaralı kalemlerini ayrıştırır."""
    head = re.compile(r"\b(I{1,3}|IV|V)\. SINIF \(([A-E])\) GRUBU YAPILAR[ .…]*?([\d.]+,\d{2}) TL/m²")
    heads = list(head.finditer(text))
    end_marker = text.find("Yapının mimarlık hizmetlerine esas olan sınıfının belirlenmesine")
    rows = []
    for k, m in enumerate(heads):
        stop = heads[k + 1].start() if k + 1 < len(heads) else end_marker
        body = text[m.end():stop].strip()
        items = re.split(r"\s*(?:^|\s)(\d{1,2})\.\s+", " " + body)
        # items: ['', '1', 'metin', '2', 'metin', ...]
        for j in range(1, len(items) - 1, 2):
            rows.append({
                "sinif": f"{m.group(1)}-{m.group(2)}",
                "tl_m2": float(m.group(3).replace(".", "").replace(",", ".")),
                "no": int(items[j]),
                "ifade": items[j + 1].strip(),
            })
    return rows


def build_unit_cost_table() -> tuple[dict, dict]:
    """2026 tebliğinden sağlık yapıları tablosunu yazar; Bölüm 3 değerlerini ve dahil/hariç ifadelerini doğrular."""
    t26 = html_text(RAW_DIR / "RG_20260203-4_yapi_yaklasik_birim_maliyetleri_2026.htm")
    t25 = html_text(RAW_DIR / "RG_20250131-3_yapi_yaklasik_birim_maliyetleri_2025.htm")
    r26, r25 = parse_teblig(t26), parse_teblig(t25)
    p26 = {r["sinif"]: r["tl_m2"] for r in r26}
    p25 = {r["sinif"]: r["tl_m2"] for r in r25}
    health = [r for r in r26 if HEALTH_RE.search(r["ifade"]) and not EXCLUDE_RE.search(r["ifade"])]
    health.sort(key=lambda r: (ROMAN[r["sinif"].split("-")[0]], r["sinif"], r["no"]))
    with open(OUT_TEBLIG, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sinif", "tl_m2", "yapi_turu_ifadesi", "source_id"])
        for r in health:
            w.writerow([r["sinif"], f"{r['tl_m2']:.0f}", r["ifade"], SRC_TEBLIG_2026])
    print(f"yazıldı: {OUT_TEBLIG.relative_to(common.ROOT)} ({len(health)} satır)")
    for r in health:
        print(f"  {r['sinif']:6} {common.fmt_tr(r['tl_m2'])} | {r['ifade']}")
    # Bölüm 3 kontrolü
    for k, v in SPEC_UNIT_COSTS.items():
        flag = "OK" if p26.get(k) == v else "ÇELİŞKİ"
        print(f"  spec {k}: {v} | RG 2026: {p26.get(k)} | RG 2025: {p25.get(k)} | 2026/2025 = "
              f"{p26[k] / p25[k]:.3f} [{flag}]")
    # Dahil/hariç notları (MADDE 1 ve MADDE 2(4)) metinde var mı
    for phrase in ("KDV hariç, genel giderler (%15) ile yüklenici kârı (%10) dâhil",
                   "arsa bedeli, çevre düzenlemesi (peyzaj",
                   "altyapı (zemin iyileştirme, bina dışı;",
                   "ortalama nitelikli yapı malzemeleri",
                   "1/1/2026 tarihinden geçerli olmak üzere"):
        assert phrase in t26, phrase
    print("  dahil/hariç ifadeleri RG metninde doğrulandı")
    return p26, p25


# ---------------------------------------------------------------- hastane boyut örnekleri
# Her sayı arşivdeki belgede aranarak doğrulanır: (belge anahtarı, belgede geçen tam ifade).
# Kapalı alan yoksa None.
EXAMPLES = [
    # hastane, il, tur, yatak, kapali_alan_m2, yil, source_id, not, [(belge, ifade), ...]
    ("Elazığ Şehir Hastanesi", "Elazığ", "şehir hastanesi (KÖİ)", 1038, None, 2018, "maliyet_memurlar_2018",
     "Haberde '355 bin 752 metrekare alan üzerine kurulu' deniyor; kapalı alan mı arsa mı belirsiz, m²/yatak hesaplanmadı "
     "(kapalı alan sayılırsa 342,7 m²/yatak olurdu). Bölüm 3 ile uyumlu (1.038).",
     [("memurlar", "bin 38 yatak kapasiteli"), ("memurlar", "355 bin 752 metrekare alan üzerine kurulu")]),
    ("Eskişehir Şehir Hastanesi", "Eskişehir", "şehir hastanesi (KÖİ)", 1081, 333303, 2018, "maliyet_memurlar_2018",
     "'333 bin 303 metrekare kapalı alana sahip … bin 81 yatak'. Bölüm 3 ile uyumlu.",
     [("memurlar", "333 bin 303 metrekare kapalı alana sahip"), ("memurlar", "bin 81 yatak kapasitesine")]),
    ("Kayseri Şehir Hastanesi", "Kayseri", "şehir hastanesi (KÖİ)", 1607, None, 2018, "maliyet_memurlar_2018",
     "Kapalı alan haberde yok. Bölüm 3 ile uyumlu.", [("memurlar", "bin 607 yatak kapasitesiyle")]),
    ("Manisa Şehir Hastanesi", "Manisa", "şehir hastanesi (KÖİ)", 558, 178204, 2018, "maliyet_memurlar_2018",
     "Bölüm 3'te yok; ek örnek. 1.000 yatağın altındaki bir 'şehir hastanesi' (eşik model seçimi).",
     [("memurlar", "178 bin 204 metrekare kapalı alan"), ("memurlar", "558 yatak kapasitesine")]),
    ("Ankara Bilkent Şehir Hastanesi", "Ankara", "şehir hastanesi (KÖİ, kampüs)", 3704, None, 2018, "maliyet_memurlar_2018",
     "ÇELİŞKİ: 3.704 (bu kaynak; Kalkınma 2017; Aliefendioğlu 2021) / 3.711 (SB 2020 bütçe sunumu, Savaş 2020) / "
     "3.810 (Medikal Akademi bileşen toplamı) / 4.050 (TBMM 2025, fiilî+resmî). Haberdeki bileşenler (562+441+506+599+542+588+100+100 = 3.438) 3.704'e eşit değil.",
     [("memurlar", "3 bin 704 yatak kapasitesine")]),
    ("Ankara Bilkent Şehir Hastanesi", "Ankara", "şehir hastanesi (KÖİ, kampüs)", 3704, 1200000, 2017,
     "maliyet_kalkinma_ankara_2017",
     "Planlama aşaması (2017): '1.200.000 m2 inşaat alanı'; bileşenler 62+562+441+507+564+580+588+300+100 = 3.704. Arazi bu belgede yok.",
     [("kalk", "1.200.000"), ("kalk", "toplamda 3704 yatak")]),
    ("Ankara Bilkent Şehir Hastanesi", "Ankara", "şehir hastanesi (KÖİ, kampüs)", 3810, 1312358, 2019,
     "maliyet_medikalakademi_bilkent",
     "Aynı sayfa metinde '3 bin 704 nitelikli yatak', tabloda 'Toplam Yatak Sayısı: 3.810' diyor (bileşen toplamı 3.810). "
     "Toplam arazi 674.000 m² (176,9 m² arsa/yatak). Sayfa 15.03.2019'da yayımlanmış, 24.08.2021'de güncellenmiş. "
     "Bölüm 3 ile uyumlu (3.810; 1.312.358 m²).",
     [("medikal", "Toplam Yatak Sayısı: 3.810"), ("medikal", "Toplam Kapalı Alan: 1.312.358"),
      ("medikal", "Toplam Arazi Alanı: 674.000")]),
    ("Ankara Bilkent Şehir Hastanesi", "Ankara", "şehir hastanesi (KÖİ, kampüs)", 3711, None, 2019, "maliyet_savas_2020",
     "Tablo 3 (kaynak: Sağlık Bakanlığı 2020 Yılı Bütçe Sunumu, TBMM 2019): 'Ankara Bilkent 3711'.",
     [("savas", "Ankara Bilkent 3711")]),
    ("Ankara Bilkent Şehir Hastanesi", "Ankara", "şehir hastanesi (KÖİ, kampüs)", 4050, None, 2025,
     "maliyet_tbmm_2025_ankara",
     "Ankara İl Sağlık Müdürü sunumu (27.02.2025): 'Bilkent ve Etlik Şehir Hastanesi 4.050'şer yatakla toplamda 8.100 yatak'; "
     "soru üzerine 'hem fiilî hem resmî'. Bölüm 3 ile uyumlu.",
     [("tbmm", "4.050'şer yatakla toplamda 8.100 yatak")]),
    ("Ankara Etlik Şehir Hastanesi", "Ankara", "şehir hastanesi (KÖİ, kampüs)", 3566, None, 2017,
     "maliyet_kalkinma_ankara_2017",
     "Planlanan 3.566 yatak (bileşenler 718+362+478+500+508+494+300+106+100 = 3.566). Belgede '1.071.885 m2 arazi üzerinde' ve "
     "'1 milyon 71 bin metrekare alanda yükselen' ifadeleri birlikte geçiyor; kapalı alan mı arsa mı belirsiz, m²/yatak hesaplanmadı.",
     [("kalk", "Toplamda 3.566 yatak"), ("kalk", "1.071.885 m2 arazi")]),
    ("Ankara Etlik Şehir Hastanesi", "Ankara", "şehir hastanesi (KÖİ, kampüs)", 3577, None, 2019, "maliyet_savas_2020",
     "Tablo 4 (SB 2020 Yılı Bütçe Sunumu): 'Ankara Etlik 3.577'; Aliefendioğlu & Bostancı 2021 Tablo 1 de 3.577.",
     [("savas", "Ankara Etlik 3.577")]),
    ("Ankara Etlik Şehir Hastanesi", "Ankara", "şehir hastanesi (KÖİ, kampüs)", 4050, None, 2025, "maliyet_tbmm_2025_ankara",
     "Bkz. Bilkent satırı (TBMM 2025).", [("tbmm", "4.050'şer yatakla")]),
    ("Etlik Kampüsü Bölge Hastanesi", "Ankara", "genel (şehir hastanesi kampüsü içinde)", 718, None, 2017,
     "maliyet_kalkinma_ankara_2017", "Etlik kampüsündeki genel (bölge) hastanesi; planlama aşaması. Bölüm 3 ile uyumlu.",
     [("kalk", "Bölge Hastanesi (718 Yatak Kapasiteli)")]),
    ("Ankara Eğitim ve Araştırma Hastanesi", "Ankara", "eğitim ve araştırma (genel)", 616, None, 2017,
     "maliyet_kalkinma_ankara_2017",
     "'Fiili yatak kapasitesi 616'dır; acil müşahede, diyaliz ve hemodiyaliz de dahil edildiğinde bu sayı 678'e çıkmaktadır'. Bölüm 3 ile uyumlu.",
     [("kalk", "Fiili yatak kapasitesi 616"), ("kalk", "678")]),
    ("Bahçe Devlet Hastanesi", "Osmaniye", "ilçe devlet hastanesi", 50, None, 2021, "maliyet_sygm_bahce_2021",
     "Sözleşme imzalandı (17.11.2021). 2 ameliyathane, 11 poliklinik, 15 müşahede yataklı acil, 10 yatak diyaliz. Kapalı alan yok. Bölüm 3 ile uyumlu.",
     [("bahce", "50 yataklı yeni devlet hastanesinin sözleşmesi imzalandı")]),
    ("Dikili Devlet Hastanesi", "İzmir", "ilçe devlet hastanesi", 75, None, 2026, "maliyet_cnnturk_dikili_2026",
     "Hizmete girdi (10.03.2026). '15 bin 10 metrekare açık alan, 3 bin 969 metre kare kapalı alan üzerine kurulan, 4 katlı'. "
     "3.969 m² 4 katlı bina için büyük olasılıkla taban alanı; m²/yatak hesaplanmadı (yazıldığı gibi alınsa 52,9 m²/yatak — gerçekçi değil). "
     "Arsa (açık alan) 15.010 m² → 200,1 m²/yatak. Bölüm 3 ile uyumlu.",
     [("dikili", "75 yataklı yeni devlet hastanesi"), ("dikili", "15 bin 10 metrekare açık alan, 3 bin 969 metre kare kapalı alan")]),
    ("Çayırova Devlet Hastanesi", "Kocaeli", "ilçe devlet hastanesi", 200, 45655, 2021,
     "maliyet_sygm_cayirova_2021;yatirim_kyp2026",
     "Yatak: SYGM (04.07.2021). Kapalı alan: 2026 KYP karakteristiği (proje 2017I00-145177, basılı s. 334). 6 ameliyathane, 51 yoğun bakım, 75 poliklinik. Bölüm 3 ile uyumlu (200).",
     [("cayirova", "200 yataklı yeni devlet hastanesinin")]),
    ("Balcalı Hastanesi (Ç.Ü. Tıp Fakültesi)", "Adana", "üniversite (3. basamak)", 1200, 190651, 2019, "maliyet_balcali_2019",
     "Toplam kapalı alan kampüs içi tüm sağlık yapıları (22.012 m² katlı otopark dahil; otopark hariç 168.639 m² → 140,5 m²/yatak). "
     "Ana bina 149.164 m². Açık alan 95.000 m² (79,2 m²/yatak). Makalede 190651/1200 = 158,87. Bölüm 3 ile uyumlu (≈159).",
     [("balcali", "190651/1200=158,87"), ("balcali", "Katlı Otopark 22012")]),
    ("Ankara Dr. Sami Ulus Çocuk Sağlığı ve Hastalıkları EAH", "Ankara", "eğitim ve araştırma (dal)", 300, 75000, 2026,
     "yatirim_kyp2026", "2026 KYP proje 2023I00-214842 (yapım 2023–2027), karakteristik 'Hastane İnşaatı (300 yatak, 75.000 m²)'.", []),
    ("Ankara Dr. Abdurrahman Yurtaslan Onkoloji EAH", "Ankara", "eğitim ve araştırma (dal)", 600, 150000, 2026,
     "yatirim_kyp2026", "2026 KYP proje 2023I00-214844 (2023–2027).", []),
    ("Ankara Dışkapı Yıldırım Beyazıt EAH", "Ankara", "eğitim ve araştırma (genel)", 600, 150000, 2026,
     "yatirim_kyp2026", "2026 KYP proje 2023I00-214845 (2023–2027).", []),
    ("İstanbul Sancaktepe Şehir Hastanesi 1. Etap", "İstanbul", "şehir hastanesi (genel bütçe)", 4050, 990500, 2026,
     "yatirim_kyp2026", "2026 KYP; en büyük tek proje (şehir hastanesi üst sınırı için örnek).", []),
    ("Şehir hastaneleri programı (32 hastane, planlanan)", "Türkiye", "toplam (KÖİ programı)", 42199, 14159000, 2017,
     "maliyet_savas_2020",
     "'Aralık 2017 itibariyle toplam 32 şehir hastanesi … 14 milyon 159 bin m2 kapalı alana sahip 42.199 yeni hasta yatak' (Atasever 2018'e atıfla). "
     "Aliefendioğlu & Bostancı 2021: 34 şehir hastanesi, 14.000.000 m², 42.199 yatak, 'yatak başına 331 m²'.",
     [("savas", "14 milyon 159 bin m2"), ("savas", "42.199 yeni hasta yatak")]),
    ("Sağlık Bakanlığı hastaneleri (850 hastane, mevcut stok)", "Türkiye", "toplam (mevcut stok)", 137000, 19000000, 2021,
     "maliyet_aliefendioglu_2021",
     "'850 kamu hastanesinin toplam kapalı alanı 19 milyon m² ve yatak sayısı ise 137.000 … SB yatak başına 138 m²' "
     "(makale şehir hastanelerinin bu alanın %74'ü olduğunu da söylüyor; iç tutarlılığı sınırlı).",
     [("alief", "SB yatak başına 138 m²"), ("alief", "19 milyon m²")]),
]

DOCS = {
    "memurlar": ("memurlar_757810_sehir_hastanesi_yatirimi.html", "html"),
    "medikal": ("medikalakademi_bilkent_sehir_hastanesi.html", "html"),
    "kalk": ("kalkinmakutuphanesi_ankara_saglik_termal_2017.pdf", "pdf"),
    "tbmm": ("tbmm_tutanak_4125.pdf", "pdf"),
    "bahce": ("sygm_TR-86211_bahce_50_yatak.html", "html"),
    "cayirova": ("sygm_TR-84485_cayirova_200_yatak.html", "html"),
    "dikili": ("cnnturk_dikili_75_yatak.html", "html"),
    "balcali": ("dergipark_704217_balcali.pdf", "pdf"),
    "savas": ("dergipark_972597_savas_2020.pdf", "pdf"),
    "alief": ("dergipark_1105037_aliefendioglu_2021.pdf", "pdf"),
}
_DOC_CACHE: dict[str, str] = {}


def doc_text(key: str) -> str:
    """Arşiv belgesinin tek satırlık düz metni (önbellekli)."""
    if key not in _DOC_CACHE:
        name, kind = DOCS[key]
        p = RAW_DIR / name
        if kind == "html":
            t = html_text(p)
        else:
            import pdfplumber
            with pdfplumber.open(p) as pdf:
                t = " ".join((pg.extract_text() or "") for pg in pdf.pages)
        _DOC_CACHE[key] = re.sub(r"\s+", " ", t)
    return _DOC_CACHE[key]


def build_size_examples() -> None:
    """hastane_boyut_ornekleri.csv: her ifade belgede, her KYP satırı KYP CSV'sinde aranır."""
    import pandas as pd

    kyp = pd.read_csv(KYP_CSV)
    with open(OUT_SIZES, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["hastane", "il", "tur", "yatak", "kapali_alan_m2", "m2_per_yatak", "yil", "source_id", "not"])
        for (name, il, tur, bed, area, yil, sid, note, checks) in EXAMPLES:
            for key, phrase in checks:
                if phrase not in doc_text(key):
                    raise SystemExit(f"DOĞRULANAMADI: {name}: '{phrase}' {DOCS[key][0]} içinde yok")
            if "yatirim_kyp2026" in sid:
                hit = kyp[(kyp.yatak == bed) & (kyp.alan_m2 == area)] if area else kyp[kyp.yatak == bed]
                if hit.empty:
                    raise SystemExit(f"DOĞRULANAMADI (KYP): {name}")
            m2 = f"{area / bed:.1f}" if area else ""
            w.writerow([name, il, tur, bed, area if area else "", m2, yil, sid, note])
    print(f"yazıldı: {OUT_SIZES.relative_to(common.ROOT)} ({len(EXAMPLES)} satır; tüm ifadeler belgelerde doğrulandı)")


# ---------------------------------------------------------------- 2026 KYP türetmeleri

def _summary(s) -> dict:
    """m²/yatak özeti; proje başına oran alan/yatak'tan yeniden hesaplanır (CSV sütunu 1 ondalıklı)."""
    r = s.alan_m2 / s.yatak
    return {"n": int(len(s)), "medyan": float(s.m2_yatak.median()), "ortalama": float(r.mean()),
            "toplam_bolu_toplam": float(s.alan_m2.sum() / s.yatak.sum()),
            "c1": float(s.m2_yatak.quantile(.25)), "c3": float(s.m2_yatak.quantile(.75)),
            "min": float(r.min()), "max": float(r.max())}


def kyp_area_stats() -> dict:
    """2026 KYP Sağlık Bakanlığı hastaneleri: m²/yatak, alan = A0 + a·yatak, makine-teçhizat oranı."""
    import numpy as np
    import pandas as pd
    import statsmodels.api as sm

    d = pd.read_csv(KYP_CSV)
    h = d[(d.dahil_ana == True) & d.yatak.notna() & d.alan_m2.notna()]  # noqa: E712
    g, c = h[h.sehir_hastanesi == False], h[h.sehir_hastanesi == True]  # noqa: E712
    out: dict = {
        "girdi": {"dosya": str(KYP_CSV.relative_to(common.ROOT)), "sha256": common.sha256(KYP_CSV),
                  "kaynak_id": "yatirim_kyp2026",
                  "suzgec": "dahil_ana == True, yatak ve alan_m2 dolu; genel = sehir_hastanesi False, "
                            "sehir = sehir_hastanesi True (genel bütçeli şehir hastaneleri)"},
        "m2_per_yatak": {},
    }
    for nm, s in [("genel", g), ("sehir", c), ("sehir_1000", c[c.yatak >= 1000])]:
        st = _summary(s)
        out["m2_per_yatak"][nm] = st
        print(f"  KYP {nm}: n={st['n']} medyan={st['medyan']:.1f} toplam/toplam={st['toplam_bolu_toplam']:.1f} "
              f"Ç1–Ç3={st['c1']:.1f}–{st['c3']:.1f}")
    print(f"  KYP sehir ortalama (proje başına alan/yatak): {out['m2_per_yatak']['sehir']['ortalama']:.1f} "
          f"(n={out['m2_per_yatak']['sehir']['n']})")
    out["sehir_projeleri"] = [
        {"proje_no": r.proje_no, "proje_adi": r.proje_adi, "yatak": int(r.yatak), "alan_m2": int(r.alan_m2),
         "m2_per_yatak": float(r.alan_m2 / r.yatak), "basili_sayfa": int(r.basili_sayfa)}
        for r in c.sort_values("proje_no").itertuples()]

    # Boyut sınıfları (tebliğ eşikleri 200 / 400)
    g2 = g.copy()
    g2["sinif"] = pd.cut(g2.yatak, [0, 199, 399, 10_000], labels=["<200", "200-399", ">=400"])
    agg = g2.groupby("sinif", observed=True).agg(n=("yatak", "size"), a=("alan_m2", "sum"), y=("yatak", "sum"))
    agg["m2"] = agg.a / agg.y
    agg["ort_yatak"] = agg.y / agg.n
    print(agg.round(1).to_string())
    out["siniflar_genel"] = {
        str(k): {"n": int(r.n), "alan_toplam_m2": float(r.a), "yatak_toplam": float(r.y),
                 "m2_per_yatak_toplam_bolu_toplam": float(r.m2), "ortalama_yatak": float(r.ort_yatak)}
        for k, r in agg.iterrows()}
    lo, hi = agg.loc["<200"], agg.loc[">=400"]
    slope = (hi.a / hi.n - lo.a / lo.n) / (hi.ort_yatak - lo.ort_yatak)
    a0_two = lo.a / lo.n - slope * lo.ort_yatak
    print(f"  iki nokta (sınıf ortalamaları): a={slope:.1f} m²/yatak, A0={a0_two:.0f} m²")
    out["iki_nokta_sinif_ortalamalari"] = {
        "tanim": "(<200) ve (>=400) sınıflarının ortalama hastane alanı / ortalama yatak noktalarından doğru",
        "a_m2_per_yatak": float(slope), "A0_m2": float(a0_two)}

    # OLS: alan = A0 + a·yatak
    out["ols_alan_yatak"] = {}
    for key, nm, s in [("genel", "genel n=47", g), ("genel_le600", "genel ≤600", g[g.yatak <= 600])]:
        m = sm.OLS(s.alan_m2.values, sm.add_constant(s.yatak.values)).fit()
        ci = m.conf_int()
        print(f"  OLS {nm}: A0={m.params[0]:.0f} (GA {ci[0][0]:.0f}…{ci[0][1]:.0f}; p={m.pvalues[0]:.2f}), "
              f"a={m.params[1]:.1f} (GA {ci[1][0]:.1f}…{ci[1][1]:.1f}), R²={m.rsquared:.3f}")
        res = {"n": int(len(s)), "A0_m2": float(m.params[0]), "A0_ci95": [float(ci[0][0]), float(ci[0][1])],
               "A0_p": float(m.pvalues[0]), "a_m2_per_yatak": float(m.params[1]),
               "a_ci95": [float(ci[1][0]), float(ci[1][1])], "r2": float(m.rsquared), "sabit_a_ile_A0": {}}
        for a in (130.0, 158.87):
            r = s.alan_m2 - a * s.yatak
            se = r.std(ddof=1) / np.sqrt(len(r))
            print(f"     a={a} sabitken A0 = ort(alan − a·yatak) = {r.mean():.0f} m² (SH {se:.0f})")
            res["sabit_a_ile_A0"][f"{a:g}"] = {"A0_m2": float(r.mean()), "se": float(se)}
        out["ols_alan_yatak"][key] = res

    tl_med, tl_agg = g.tl_m2.median(), g.proje_tutari_tl.sum() / g.alan_m2.sum()
    print(f"  KYP genel TL/m² medyan={tl_med:.0f}, toplam/toplam={tl_agg:.0f}")
    out["tl_per_m2_genel_kdv_dahil"] = {"medyan": float(tl_med), "toplam_bolu_toplam": float(tl_agg)}

    # Makine-teçhizat / inşaat (alt proje satırları; yer tutucu < 1 mn TL hariç)
    par = d[d.proje_no.notna()].set_index("proje_no")
    print("  makine-teçhizat / inşaat (alt projeler):")
    ratios = []
    for up, grp in d[d.ust_proje_no.notna()].groupby("ust_proje_no"):
        mak = grp[grp.proje_adi.str.contains("Makine|Teçhizat", case=False)].proje_tutari_tl.sum()
        ins = grp[grp.proje_adi.str.contains("İnşaat|Yapım", case=False)].proje_tutari_tl.sum()
        if mak >= 1e6 and ins >= 1e6:
            p = par.loc[up]
            print(f"     {up} {p.proje_adi[:45]:45} yatak={p.yatak} oran={mak / ins:.3f} "
                  f"(makine {common.fmt_tr(mak)} / inşaat {common.fmt_tr(ins)}; basılı s. {p.basili_sayfa})")
            ratios.append({"ust_proje_no": up, "proje_adi": p.proje_adi,
                           "yatak": None if pd.isna(p.yatak) else int(p.yatak),
                           "makine_tl": int(mak), "insaat_tl": int(ins), "oran": float(mak / ins),
                           "basili_sayfa": int(p.basili_sayfa)})
    out["makine_techizat_insaat_orani"] = ratios
    return out


# ---------------------------------------------------------------- şehir hastanesi m²/yatak adayları

# (etiket, EXAMPLES'taki hastane adı, source_id)
CITY_CANDIDATES = [
    ("Eskişehir", "Eskişehir Şehir Hastanesi", "maliyet_memurlar_2018"),
    ("Manisa", "Manisa Şehir Hastanesi", "maliyet_memurlar_2018"),
    ("Bilkent (3.810)", "Ankara Bilkent Şehir Hastanesi", "maliyet_medikalakademi_bilkent"),
    ("Bilkent (3.704, 2017 plan, 1,2 mn m²)", "Ankara Bilkent Şehir Hastanesi", "maliyet_kalkinma_ankara_2017"),
    ("Program 32 hastane", "Şehir hastaneleri programı (32 hastane, planlanan)", "maliyet_savas_2020"),
]
# Bilkent'in kapalı alanına hastane dışı yapıların girdiğini gösteren ifadeler (Kalkınma 2017, PDF s. 5)
BILKENT_NON_HOSPITAL = (
    "Sağlık Bakanlığı’nın yeni idari binasını",
    "Sağlık Bilimleri Üniversitesi, Kongre Merkezleri",
    "8 bin araç kapasiteli kapalı otoparklar bodrum",
    "800 araç kapasiteli olacak",
)


def city_m2_candidates() -> dict:
    """Şehir hastanesi m²/yatak adayları (boyut örneklerinden) ve Bilkent kapsam uyarısının doğrulaması."""
    rows = {}
    for label, name, sid in CITY_CANDIDATES:
        ex = next(e for e in EXAMPLES if e[0] == name and e[6] == sid and e[4])
        rows[label] = {"yatak": ex[3], "kapali_alan_m2": ex[4], "m2_per_yatak": ex[4] / ex[3], "source_id": sid}
        print(f"  {label}: {ex[4] / ex[3]:.1f} m²/yatak")
    kalk = doc_text("kalk")
    for phrase in BILKENT_NON_HOSPITAL:
        if phrase not in kalk:
            raise SystemExit(f"DOĞRULANAMADI: '{phrase}' {DOCS['kalk'][0]} içinde yok")
    print("  Bilkent kapsamı (Bakanlık idari binası, üniversite/kongre/ticari alanlar, 8 bin araçlık bodrum otopark) "
          "Kalkınma 2017 metninde doğrulandı")
    return {"adaylar": rows,
            "bilkent_kapsam_ifadeleri": {"belge": f"data/raw/maliyet/{DOCS['kalk'][0]}", "sayfa": "PDF s. 5",
                                         "ifadeler": list(BILKENT_NON_HOSPITAL)}}


def _round(x, nd: int = 6):
    """JSON için kararlı yuvarlama (platformlar arası son hane gürültüsünü keser)."""
    if isinstance(x, float):
        return round(x, nd)
    if isinstance(x, dict):
        return {k: _round(v, nd) for k, v in x.items()}
    if isinstance(x, list):
        return [_round(v, nd) for v in x]
    return x


def main() -> None:
    ensure_downloads(offline="--offline" in sys.argv)
    build_unit_cost_table()
    build_size_examples()
    print("KYP 2026 türetmeleri:")
    kyp = kyp_area_stats()
    print("Şehir hastanesi m²/yatak:")
    kyp["sehir_hastanesi_belge_adaylari"] = city_m2_candidates()
    kyp = {"aciklama": "2026 KYP Sağlık Bakanlığı hastane projelerinden yatak başına kapalı alan, çekirdek alan (A0) "
                       "ve makine-teçhizat oranı hesapları (kaynak kaydı: maliyet_hesap_kyp_alan). Alanlar KYP proje "
                       "karakteristiğindeki kapalı alandır (m²); tutarlar KDV dahil TL.",
           "uretici": "pipeline/18_costs.py", **kyp}
    OUT_KYP_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_KYP_JSON.write_text(json.dumps(_round(kyp), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"yazıldı: {OUT_KYP_JSON.relative_to(common.ROOT)}")


if __name__ == "__main__":
    main()
